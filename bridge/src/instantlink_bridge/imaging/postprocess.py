"""Adjustment stage between source decode and model-aware transform.

Operates at full source resolution so RAW/HIF benefit from the high-fidelity
colour space; the model-size JPEG encode is the last step.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from instantlink_bridge.config import AdjustmentsConfig  # pragma: no cover

__all__ = [
    "AdjustmentProfile",
    "apply_adjustments",
    "read_exif_datestamp_text",
    "render_adjustments_preview",
]

# The five discrete picker values exposed in UI (-100, -50, 0, +50, +100).
ADJUSTMENT_PICKER_VALUES: tuple[int, ...] = (-100, -50, 0, 50, 100)


@dataclass(frozen=True, slots=True)
class AdjustmentProfile:
    """Immutable description of all colour and overlay adjustments.

    All defaults are pass-through identity values — an ``AdjustmentProfile()``
    with no arguments applied via :func:`apply_adjustments` leaves every pixel
    unchanged.

    Internal float representation
    ------------------------------
    * ``saturation``: PIL ``ImageEnhance.Color`` factor. 1.0 = unchanged;
      0.0 = greyscale; 2.0 = double saturation.
    * ``exposure``: brightness factor derived from EV stops as
      ``2 ** (ev_stops / 1.0)`` where ev_stops in [-1, +1]. 1.0 = unchanged.
      Range: 0.5 (-1 EV) ... 2.0 (+1 EV), capped by design so Instax prints
      survive extreme inputs.
    * ``sharpness``: PIL ``ImageEnhance.Sharpness`` factor. 1.0 = unchanged;
      0.0 = blurred; 2.0 = double sharpness.
    * ``hue``: degrees of HSV hue rotation. 0 = unchanged; ±180 = full invert.
    """

    saturation: float = 1.0
    """PIL ``ImageEnhance.Color`` factor. 1.0 = unchanged."""

    exposure: float = 1.0
    """Brightness multiplicative factor. 1.0 = unchanged."""

    sharpness: float = 1.0
    """PIL ``ImageEnhance.Sharpness`` factor. 1.0 = unchanged."""

    hue: int = 0
    """Degrees of HSV hue rotation. 0 = unchanged."""

    vignette: int = 0
    """Corner-darkening strength. 0 = off (identity), 100 = heavy. One-sided: no negative values."""

    datestamp: bool = False
    """Overlay flag. When True and datestamp_text is non-empty, renders it bottom-right."""

    watermark: bool = False
    """Overlay flag. When True and watermark_text is non-empty, renders it bottom-left."""

    datestamp_text: str = ""
    """Pre-formatted datestamp string. Empty string disables the overlay even if datestamp=True.

    The controller formats EXIF DateTimeOriginal + locale before building the profile so
    apply_adjustments stays locale-agnostic.
    """

    watermark_text: str = ""
    """Watermark label to stamp. Empty string disables the overlay even if watermark=True."""

    @classmethod
    def from_config(cls, config: AdjustmentsConfig) -> AdjustmentProfile:
        """Build a profile from user-facing -100...+100 integer config values.

        Mapping:
        * saturation: ``factor = 1.0 + value / 100.0``
          -100 => 0.0 (greyscale), 0 => 1.0, +100 => 2.0.
        * exposure: ``factor = 2 ** (value / 100.0)``
          -100 => 0.5 (~-1 EV), 0 => 1.0, +100 => 2.0 (+1 EV).
        * sharpness: ``factor = 1.0 + value / 100.0``
          -100 => 0.0 (blurred), 0 => 1.0, +100 => 2.0.
        * hue: ``degrees = value * 1.8``
          -100 => -180 deg, 0 => 0 deg, +100 => +180 deg.

        Note: datestamp_text is NOT set here — the caller (controller / app.py)
        must read EXIF and format the date, then pass datestamp_text explicitly.
        watermark_text is taken from config.watermark_text.
        """
        return cls(
            saturation=1.0 + config.saturation / 100.0,
            exposure=2.0 ** (config.exposure / 100.0),
            sharpness=1.0 + config.sharpness / 100.0,
            hue=int(config.hue * 1.8),
            vignette=config.vignette,
            datestamp=config.datestamp,
            watermark=config.watermark,
            datestamp_text="",  # caller fills this in after reading EXIF
            watermark_text=config.watermark_text if config.watermark else "",
        )


_IDENTITY = AdjustmentProfile()

# Overlay rendering constants.
# Margin from edge: at least 16 px, scaled to image height.
_OVERLAY_MARGIN_DIVISOR = 40
_OVERLAY_MARGIN_MIN = 16
# Font size: image.height // 30, clamped to [12, 48].
_OVERLAY_FONT_SIZE_DIVISOR = 30
_OVERLAY_FONT_SIZE_MIN = 12
_OVERLAY_FONT_SIZE_MAX = 48


def apply_adjustments(image: Image.Image, profile: AdjustmentProfile) -> Image.Image:
    """Apply colour/overlay adjustments to ``image`` in place semantically.

    Application order: hue → saturation → exposure → sharpness → vignette → datestamp → watermark.

    Hue is applied first because it operates on the original colour space;
    the subsequent saturation, exposure, and sharpness adjustments are linear
    and commutative with each other but not with hue rotation. Applying hue
    last would interact with any saturation shift applied before it and
    produce slightly different results for combined profiles.

    Each axis short-circuits when the value equals the identity so a mixed
    profile (e.g. only saturation changed) pays only for the operations
    it actually needs.

    An identity profile (all defaults) returns the input image object
    unchanged — no copy, no PIL call.

    Overlays (datestamp, watermark) are applied BEFORE _fit_image so the
    full-resolution canvas carries the text and _fit_image resamples it
    cleanly into the model's print pixels.

    Parameters
    ----------
    image:
        Source RGB image at full decode resolution.
    profile:
        Describes which adjustments to apply. An ``AdjustmentProfile()`` with
        all defaults is a fast-path no-op.

    Returns
    -------
    Image.Image
        Adjusted image. May be the same object as ``image`` when the profile
        is identity.
    """
    if profile == _IDENTITY:
        return image  # identity profile: fast path, no copy

    out = image

    # --- Hue rotation (NumPy RGB→HSV channel roll→RGB) -------------------
    if profile.hue != 0:
        out = _apply_hue(out, profile.hue)

    # --- Saturation (PIL ImageEnhance.Color) ------------------------------
    if profile.saturation != 1.0:
        from PIL import ImageEnhance

        out = ImageEnhance.Color(out).enhance(profile.saturation)

    # --- Exposure (PIL ImageEnhance.Brightness) ---------------------------
    if profile.exposure != 1.0:
        from PIL import ImageEnhance

        out = ImageEnhance.Brightness(out).enhance(profile.exposure)

    # --- Sharpness (PIL ImageEnhance.Sharpness) ---------------------------
    if profile.sharpness != 1.0:
        from PIL import ImageEnhance

        out = ImageEnhance.Sharpness(out).enhance(profile.sharpness)

    # --- Vignette (radial corner-darkening, NumPy) ------------------------
    # Runs AFTER sharpness so colour/tone adjustments happen first, and
    # BEFORE overlays so datestamp/watermark text lands on top of the
    # darkened corners and stays legible.
    if profile.vignette != 0:
        out = _apply_vignette(out, profile.vignette)

    # --- Datestamp (bottom-right, white text + 2px black stroke) ---------
    # Both the bool flag AND a non-empty text string are required. The caller
    # (controller / app.py) formats the EXIF date before building the profile;
    # if no EXIF date was found it passes datestamp_text="" which no-ops here.
    if profile.datestamp and profile.datestamp_text:
        out = _render_overlay(out, profile.datestamp_text, anchor="rs")

    # --- Watermark (bottom-left, same style) ------------------------------
    if profile.watermark and profile.watermark_text:
        out = _render_overlay(out, profile.watermark_text, anchor="ls")

    return out


def _apply_vignette(image: Image.Image, strength: int) -> Image.Image:
    """Apply a radial corner-darkening vignette to ``image``.

    Builds a normalised radius map once with ``np.meshgrid`` broadcasting —
    no per-pixel Python loops.  The darkening factor is::

        r_norm = sqrt((x/w - 0.5)^2 + (y/h - 0.5)^2) * sqrt(2)

    where ``r_norm`` is 0.0 at the image centre and 1.0 at each corner.
    The darkening multiplier is::

        factor = 1.0 - clip(r_norm, 0, 1)^gamma * (strength / 100.0)

    ``gamma = 2.0`` gives a gentle falloff in the inner half and a steeper
    rolloff near the corners, matching the look of a real Instax-camera
    lens vignette.

    Parameters
    ----------
    image:
        Source RGB image.  Must be in ``"RGB"`` mode; the function converts
        if necessary.
    strength:
        0 = no darkening (identity); 100 = heavy vignette.
    """
    import numpy as np  # lazy import keeps module load cheap

    _GAMMA = 2.0

    if image.mode != "RGB":
        image = image.convert("RGB")

    w, h = image.size
    # xs: column coords normalised to [-0.5, 0.5], shape (1, w)
    # ys: row coords normalised to [-0.5, 0.5], shape (h, 1)
    xs = (np.arange(w, dtype=np.float32) / w - 0.5).reshape(1, w)
    ys = (np.arange(h, dtype=np.float32) / h - 0.5).reshape(h, 1)

    # r_norm is 0 at centre, 1.0 at each corner (sqrt(2) normalises the
    # half-diagonal so a corner lands exactly at 1.0).
    r_norm = np.sqrt(xs * xs + ys * ys) * np.sqrt(2.0)  # shape (h, w)
    r_norm = np.clip(r_norm, 0.0, 1.0)

    # Darkening factor: 1.0 at centre, 1-(strength/100) at corners.
    factor = 1.0 - (r_norm**_GAMMA) * (strength / 100.0)  # shape (h, w)

    # Apply to all three RGB channels in-place to halve peak memory usage.
    # Strategy: multiply the source array in-place (arr *= factor[..., np.newaxis])
    # rather than creating a separate arr_out copy.  Peak allocation is reduced
    # from ~H×W×3×2 floats (source + output) to ~H×W×3×1 float (source only,
    # factor map is H×W×1 = one channel).  Same pixel output, lower peak RSS
    # — important on the Pi Zero 2 W with 512 MB RAM.
    arr = np.array(image, dtype=np.float32)  # H×W×3 — copy so we can mutate
    arr *= factor[:, :, np.newaxis]          # in-place multiply (no second H×W×3 alloc)
    np.clip(arr, 0.0, 255.0, out=arr)        # in-place clip
    return Image.fromarray(arr.astype(np.uint8), mode="RGB")


def _render_overlay(
    image: Image.Image,
    text: str,
    anchor: str,
) -> Image.Image:
    """Render ``text`` onto ``image`` at the corner determined by ``anchor``.

    ``anchor`` follows PIL anchor semantics:
    * ``"rs"`` — right-bottom (datestamp).
    * ``"ls"`` — left-bottom (watermark, plan 037).
    * ``"rt"`` — right-top (legacy, retained for back-compat in case a
      caller still asks for it).

    Style: white fill, 2 px black stroke for legibility on busy photos.
    Margin from edge: ``max(image.height // 40, 16)`` px.
    Font size: ``image.height // 30`` clamped to [12, 48].
    """
    if image.mode != "RGB":
        out = image.convert("RGB")
    else:
        # Ensure we have a mutable copy so prior pipeline stages are not
        # mutated in place (PIL images share pixel buffers on copy=False).
        out = image.copy()

    w, h = out.size
    margin = max(h // _OVERLAY_MARGIN_DIVISOR, _OVERLAY_MARGIN_MIN)
    font_size = max(
        _OVERLAY_FONT_SIZE_MIN,
        min(h // _OVERLAY_FONT_SIZE_DIVISOR, _OVERLAY_FONT_SIZE_MAX),
    )

    # prefer_cjk: heuristic — if any character is outside the Latin BMP
    # block, try a CJK font first so characters render as glyphs not tofu.
    prefer_cjk = any(ord(c) > 0x007F for c in text)
    font = _overlay_font(font_size, prefer_cjk=prefer_cjk)

    draw = ImageDraw.Draw(out)

    if anchor == "rs":
        # right-bottom: position is (right_edge - margin, bottom_edge - margin)
        x, y = w - margin, h - margin
    elif anchor == "ls":
        # left-bottom (watermark, post plan 037)
        x, y = margin, h - margin
    else:
        # anchor == "rt": right-top (legacy, retained for back-compat
        # in case a caller still asks for it)
        x, y = w - margin, margin

    draw.text(
        (x, y),
        text,
        font=font,
        fill="white",
        stroke_width=2,
        stroke_fill="black",
        anchor=anchor,
    )
    return out


# ---------------------------------------------------------------------------
# Font loading for overlays
# ---------------------------------------------------------------------------
# Note: this mirrors the fallback ladder in ui/render.py:_font. That function
# lives in the UI module and is not importable from imaging/ without creating
# a circular dependency (ui imports imaging). We duplicate the path lists and
# the lru_cache logic here so postprocess.py stays independent of the UI layer.
# If the path lists ever need updating, keep both in sync.

_LATIN_FONT_PATHS: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)

_CJK_FONT_PATHS: tuple[str, ...] = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
)


@lru_cache(maxsize=16)
def _overlay_font(
    size: int, prefer_cjk: bool = False
) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Return a TrueType font for overlay rendering.

    Mirrors ui/render.py:_font but lives here to avoid a circular import.
    Cached per (size, prefer_cjk).
    """
    paths = (
        (*_CJK_FONT_PATHS, *_LATIN_FONT_PATHS)
        if prefer_cjk
        else (*_LATIN_FONT_PATHS, *_CJK_FONT_PATHS)
    )
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def read_exif_datestamp_text(path: object, language: str) -> str:
    """Read EXIF DateTimeOriginal from ``path`` and return a formatted date string.

    Returns an empty string when the tag is absent or the file cannot be read,
    so the caller can safely pass the result as ``datestamp_text`` — an empty
    string is a no-op in :func:`apply_adjustments`.

    EXIF tag 36867 is ``DateTimeOriginal`` in ``"YYYY:MM:DD HH:MM:SS"`` format.
    The ``path`` argument is typed as ``object`` so callers can pass a
    ``pathlib.Path`` or ``str`` without importing ``Path`` here.

    Locale mapping:
    * ``zh-Hans`` / ``zh*``: ``yyyy年M月d日``
    * everything else (default EN): ``MMM d, yyyy`` (e.g. ``"May 3, 2026"``)
    """
    import datetime

    try:
        with Image.open(path) as img:  # type: ignore[arg-type]
            exif = img.getexif()
            raw = exif.get(36867)  # DateTimeOriginal
    except Exception:
        return ""
    if not raw or not isinstance(raw, str):
        return ""
    try:
        dt = datetime.datetime.strptime(raw[:10], "%Y:%m:%d")
    except ValueError:
        return ""
    if language.lower().startswith("zh"):
        return f"{dt.year}年{dt.month}月{dt.day}日"
    # English default: "May 3, 2026" (%-d is POSIX-only; use lstrip on Windows)
    try:
        return dt.strftime("%b %-d, %Y")
    except ValueError:
        # Windows strftime does not support %-d; fall back to zero-padded form.
        return dt.strftime("%b %d, %Y").replace(" 0", " ")


@lru_cache(maxsize=4)
def _load_example_photo_resized(size: tuple[int, int]) -> Image.Image:
    """Load and resize the built-in example photo, cached per output size.

    Uses ``importlib.resources`` so the asset is accessible from a wheel
    install as well as an editable source install.  The returned image is
    read-only (do not mutate it); callers must copy before applying adjustments.
    """
    import importlib.resources

    ref = importlib.resources.files("instantlink_bridge.imaging").joinpath(
        "_example_photo.jpg"
    )
    with importlib.resources.as_file(ref) as path:
        with Image.open(path) as raw:
            resized = raw.resize(size, Image.Resampling.LANCZOS).convert("RGB")
    return resized


def render_adjustments_preview(
    profile: AdjustmentProfile,
    *,
    size: tuple[int, int] = (88, 88),
) -> Image.Image:
    """Load the built-in example photo, resize to ``size``, apply ``profile``.

    Returns an RGB ``Image.Image``.  The default ``(88, 88)`` matches the
    list-mode preview tile; phase 4 will pass larger sizes for the edit-mode
    view.

    Performance
    -----------
    The decoded-and-resized source image is LRU-cached keyed on ``size`` so
    repeated calls with different profiles only pay the adjustment cost, not
    the JPEG decode + resize.  The identity profile short-circuits via
    ``apply_adjustments`` (returns the cached source directly — no copy).

    Phase 4 note: the cached image must not be mutated.  ``apply_adjustments``
    only mutates for hue rotation (returns a new array-backed image), so the
    identity path is safe.  Any future PIL in-place operation must copy first.
    """
    source = _load_example_photo_resized(size)
    # apply_adjustments returns ``source`` unchanged (same object) for the
    # identity profile — that's fine because we never mutate the cached object.
    return apply_adjustments(source, profile)


def _apply_hue(image: Image.Image, degrees: int) -> Image.Image:
    """Rotate the HSV hue channel by ``degrees`` using NumPy array math.

    Converts RGB → HSV, shifts H by ``degrees / 360.0`` (wrapping [0, 1)),
    converts back to RGB. Uses only NumPy ufuncs — no per-pixel Python
    loops, no ``colorsys``.
    """
    import numpy as np  # lazy import keeps module load cheap

    arr = np.asarray(image, dtype=np.float32) / 255.0  # H×W×3, range [0,1]

    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]

    c_max = np.maximum(np.maximum(r, g), b)
    c_min = np.minimum(np.minimum(r, g), b)
    delta = c_max - c_min

    # --- H channel -------------------------------------------------------
    safe_delta = np.where(delta == 0, 1.0, delta)
    h = np.where(
        delta == 0,
        0.0,
        np.where(
            c_max == r,
            ((g - b) / safe_delta) % 6.0,
            np.where(
                c_max == g,
                (b - r) / safe_delta + 2.0,
                (r - g) / safe_delta + 4.0,
            ),
        ),
    )
    h = h / 6.0  # normalise to [0, 1)

    # Apply rotation (wrap with modulo so result stays in [0, 1)).
    shift = (degrees % 360) / 360.0
    h = (h + shift) % 1.0

    # --- S channel -------------------------------------------------------
    safe_cmax = np.where(c_max == 0, 1.0, c_max)
    s = np.where(c_max == 0, 0.0, delta / safe_cmax)

    # --- V channel -------------------------------------------------------
    v = c_max

    # --- HSV → RGB -------------------------------------------------------
    h6 = h * 6.0
    i = np.floor(h6).astype(np.int32) % 6
    f = h6 - np.floor(h6)
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))

    out = np.empty_like(arr)
    for channel, (v0, v1, v2, v3, v4, v5) in enumerate(
        [
            (v, q, p, p, t, v),
            (t, v, v, q, p, p),
            (p, p, t, v, v, q),
        ]
    ):
        out[..., channel] = np.where(
            i == 0,
            v0,
            np.where(
                i == 1,
                v1,
                np.where(
                    i == 2,
                    v2,
                    np.where(
                        i == 3,
                        v3,
                        np.where(i == 4, v4, v5),
                    ),
                ),
            ),
        )

    out_u8 = (np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    result = Image.fromarray(out_u8, mode="RGB")
    return result
