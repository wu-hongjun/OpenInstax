"""240x240 LCD screen rendering — iOS 26 / Liquid Glass aesthetic."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.ui.i18n import t
from instantlink_bridge.ui.models import UiMode, UiSnapshot
from instantlink_bridge.ui.settings import format_int_with_sign
from instantlink_bridge.ui.status_indicator import StatusState, derive_status
from instantlink_bridge.ui.theme import Theme, theme_for

LCD_SIZE = (240, 240)
Font = ImageFont.ImageFont | ImageFont.FreeTypeFont

_log = logging.getLogger(__name__)
_preview_failure_logged = False


def _log_preview_failure_once() -> None:
    """Log a one-shot WARNING when the adjustments preview render crashes.

    The render loop runs at 16 fps; without the once-per-process gate a
    failure would spam the journal at >60 lines/s. The state flips after
    the first hit and never resets — restart the service to re-arm the
    warning if you fixed the underlying preview asset (plan 037 polish #7).
    """

    global _preview_failure_logged
    if _preview_failure_logged:
        return
    _preview_failure_logged = True
    _log.warning(
        "Adjustments preview render failed; rendering placeholder. "
        "Subsequent failures will be suppressed.",
        exc_info=True,
    )


def _draw_preview_unavailable(
    draw: ImageDraw.ImageDraw,
    tile_x: int,
    tile_y: int,
    tile_w: int,
    tile_h: int,
    font: Font,
    label: str,
    theme: Theme,
) -> None:
    """Render a diagonal cross-hatch + label inside a preview tile area.

    Signals "preview is broken here" rather than blending into the card,
    so the user can tell their slider has no effect vs the preview being
    unavailable (plan 037 polish #7).
    """

    # Sparse diagonal hatching at ~8 px intervals — visible but not noisy.
    step = 8
    x0 = tile_x
    y0 = tile_y
    x1 = tile_x + tile_w
    y1 = tile_y + tile_h
    # Top-left to bottom-right diagonals.
    for offset in range(-tile_h, tile_w, step):
        p0 = (max(x0, x0 + offset), max(y0, y0 - offset))
        p1 = (min(x1, x0 + offset + tile_h), min(y1, y0 - offset + tile_h))
        draw.line([p0, p1], fill=theme.label_secondary, width=1)
    # Centred label so the user reads what's wrong.
    bbox = draw.textbbox((0, 0), label, font=font)
    label_w = bbox[2] - bbox[0]
    label_h = bbox[3] - bbox[1]
    label_x = tile_x + (tile_w - label_w) // 2
    label_y = tile_y + (tile_h - label_h) // 2
    # Knock out a small background patch so the diagonal hatch doesn't
    # cut through the label text.
    pad = 4
    draw.rectangle(
        (label_x - pad, label_y - pad, label_x + label_w + pad, label_y + label_h + pad),
        fill=theme.bg,
    )
    draw.text((label_x, label_y), label, font=font, fill=theme.label_secondary)


# SettingKey values whose ADJUSTMENT_EDIT mode renders Off/On pills instead of
# a slider (plan 037 phase 3). Kept as raw strings here so render.py does not
# depend on the SettingKey enum — snapshot.adjustment_edit_key is already the
# enum's `.value`.
_OVERLAY_TOGGLE_EDIT_KEYS: frozenset[str] = frozenset({"adjust_datestamp", "adjust_watermark"})

# ---------------------------------------------------------------------------
# Legacy colour constants — kept as fallbacks; theme tokens take precedence
# ---------------------------------------------------------------------------

BG = "#101820"
PANEL = "#172633"
TEXT = "#f4f7fb"
MUTED = "#a9b6c4"
GREEN = "#00a676"
YELLOW = "#f2c14e"
RED = "#e15554"
BLUE = "#3d8bfd"
BLACK = "#05080c"

# Vertical layout constants
STATUS_BAR_H = 36  # top status bar — 6 px taller than v1 so the pill has
# breathing room above + below (was 30 with pill almost touching the screen
# edge). BODY_TOP adapts via the formula below; body renderers already use
# STATUS_BAR_H + offset so no per-mode shift is needed.
HINT_BAR_H = 40  # hint bar — sized for two-line K-chips with generous
# padding. Each chip stacks the key label (KEY1/KEY2/KEY3) on line 1 and
# the action label on line 2; both render in the slightly-larger hint
# font (12 pt vs the previous 10) so the labels read at arm's length.
HINT_BAR_Y = 240 - HINT_BAR_H  # top of hint bar row
BODY_TOP = STATUS_BAR_H + 4  # first usable body y
TOAST_Y = HINT_BAR_Y - 12  # settings_message toast y — sits between card
# and hint bar so it can't overlap card borders or chips.


# Font-size scale tables.  MEDIUM (1.0) is the historical baseline; changing
# these multipliers must not alter the MEDIUM render.
_FONT_SCALES: dict[str, tuple[float, float]] = {
    # font_size_value: (font_multiplier, row_spacing_multiplier)
    # Scale shifted up 2026-05-29 — the previous SMALL (0.85x) was unreadable on
    # the ST7789 panel. Current SMALL is now what was MEDIUM, current MEDIUM is
    # what was LARGE, and a new LARGE on top.
    "small": (1.00, 1.00),
    "medium": (1.18, 1.10),
    "large": (1.40, 1.22),
}

# Base font sizes at MEDIUM (historical values).
_BASE_FONTS: dict[str, int] = {
    "small": 10,
    "body": 14,
    "title": 17,
    "large": 22,
    "hint": 12,
}

# Base row height at MEDIUM (architect's spec).
_BASE_ROW_HEIGHT = 18


def _scale_for_snapshot(snapshot: UiSnapshot) -> tuple[float, float]:
    """Return (font_scale, row_scale) for the snapshot's font_size."""
    return _FONT_SCALES.get(snapshot.font_size, _FONT_SCALES["medium"])


def _theme_for_snapshot(snapshot: UiSnapshot) -> Theme:
    """Return the resolved Theme for a snapshot's appearance field."""
    return theme_for(snapshot.appearance)


def render_snapshot(snapshot: UiSnapshot, now: float | None = None) -> Image.Image:
    """Render one UI frame.

    ``now`` is the breath-clock seed for the status indicator. Defaults to
    ``time.monotonic()`` so production code does nothing different; tests pass
    a fixed value for deterministic pixel comparisons.
    """

    theme = _theme_for_snapshot(snapshot)
    image = Image.new("RGB", LCD_SIZE, theme.bg)
    draw = ImageDraw.Draw(image)
    font_scale, _row_scale = _scale_for_snapshot(snapshot)
    # DejaVu has the cleanest Latin glyphs but no CJK coverage; WenQuanYi/
    # Noto cover CJK but render Latin slightly differently. Each font slot
    # carries both:
    #   primary   → DejaVu when language is English (preserves the v1 look),
    #                CJK-first when the user picked 中文.
    #   cjk_sibling (attached) → always a CJK-capable font of the same size,
    #                so per-glyph fallback inside `_text` can render
    #                strings like "中文" even on the English picker.
    prefer_cjk = snapshot.language.startswith("zh")
    fonts: dict[str, Font] = {}
    for key, base in _BASE_FONTS.items():
        size = max(1, round(base * font_scale))
        primary = _font(size, prefer_cjk=prefer_cjk)
        sibling = primary if prefer_cjk else _font(size, prefer_cjk=True)
        # Attach for the smart `_text` fallback. PIL fonts are plain Python
        # objects and accept arbitrary attrs at runtime; mypy can't see that
        # because `Font` is a union of stubs that don't declare the field.
        try:
            primary.cjk_sibling = sibling  # type: ignore[union-attr]
        except AttributeError:
            # ImageFont.load_default() returns a slot-restricted object;
            # fall back to a module-level cache keyed by id().
            _CJK_SIBLING_BY_ID[id(primary)] = sibling
        fonts[key] = primary

    breath_clock = time.monotonic() if now is None else now
    draw_status_bar(draw, snapshot, fonts, breath_clock, theme=theme)

    if snapshot.mode is UiMode.READY:
        _ready(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.ADJUSTMENT_EDIT:
        _adjustment_edit(image, draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.SETTINGS:
        if snapshot.settings_title == "Adjustments":
            _adjustments(image, draw, snapshot, fonts, theme)
        else:
            _settings(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.VALIDATION:
        _validation(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.NO_FILM:
        _no_film(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.PRINTER_SEARCHING:
        _printer_searching(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.PRINTER_OFFLINE:
        _printer_offline(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.IMAGE_RECEIVED:
        _image_received(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.AWAITING_CONFIRM:
        _awaiting_confirm(image, draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.PRINTING:
        _printing(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.PRINT_COMPLETE:
        _print_complete(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.PAIRING:
        _pairing(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.PAIR_FAILED:
        _pair_failed(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.ERROR:
        _error(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.BOOTING:
        _booting(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.CONFIRMATION_DIALOG:
        _confirmation_dialog(image, draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.HELP_DIALOG:
        _help_dialog(image, draw, snapshot, fonts, theme)
    else:
        _needs_pairing(draw, snapshot, fonts, theme)

    return image


# ---------------------------------------------------------------------------
# Liquid Glass building-block helpers
# ---------------------------------------------------------------------------


def _lighten(colour: str | tuple[int, int, int], amount: int = 40) -> str:
    """Return a hex colour lightened by ``amount`` (0-255) per channel."""
    if isinstance(colour, str):
        h = colour.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    else:
        r, g, b = colour
    return f"#{min(255, r + amount):02x}{min(255, g + amount):02x}{min(255, b + amount):02x}"


def _darken(colour: str | tuple[int, int, int], amount: int = 30) -> str:
    """Return a hex colour darkened by ``amount`` per channel."""
    if isinstance(colour, str):
        h = colour.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    else:
        r, g, b = colour
    return f"#{max(0, r - amount):02x}{max(0, g - amount):02x}{max(0, b - amount):02x}"


def draw_pill(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    fill: str | tuple[int, int, int],
    fg: str | tuple[int, int, int],
    text: str,
    font: Font,
) -> None:
    """Draw a capsule (rounded rect, radius = h//2) with centred text.

    Used for the status bar live-indicator pill and hint chips. We
    *tried* edge-light + specular streak polish here but at 240×240 the
    1 px highlights read as artefact lines, not glass depth — the shape
    + frosted fill already carry the glass vocabulary. Plain rounded
    rect + text wins on this hardware.
    """
    radius = h // 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=fill)

    # Centre text within the pill
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = x + (w - tw) // 2 - bbox[0]
    ty = y + (h - th) // 2 - bbox[1]
    draw.text((tx, ty), text, fill=fg, font=font)


def draw_card(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    theme: Theme,
    *,
    elevated: bool = False,
) -> None:
    """Draw a rounded card surface.

    ``elevated`` uses ``theme.surface_elevated`` instead of the default
    surface. Corner radius matches Apple's grouped-list style (10 pt).

    The edge-light rim simulation was removed: at 240×240 the 1 px
    accent strips read as bright/dark artefact bands above and below the
    card rather than glass depth. The flat fill + rounded corners are
    cleaner on this LCD.
    """
    fill = theme.surface_elevated if elevated else theme.surface
    draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=fill)


def draw_hairline(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    theme: Theme,
) -> None:
    """Draw a 1 px horizontal separator line."""
    draw.line((x, y, x + w, y), fill=theme.separator, width=1)


def draw_slider(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    value: int,
    min_value: int,
    max_value: int,
    *,
    theme: Theme,
    track_height: int = 6,
    thumb_width: int = 8,
    thumb_height: int = 12,
    symmetric: bool = True,
) -> int:
    """Draw a horizontal slider track + filled region + thumb.

    Returns the pixel x-coordinate of the thumb centre.

    Track colour: ``theme.surface_elevated``.
    Fill colour: ``theme.accent_blue``.
    Thumb fill: ``theme.label_inverse``; 1 px outline ``theme.separator``.
    Zero-line marker (symmetric mode, value != 0): ``theme.separator``.
    """
    # Edge case: degenerate range — draw thumb centred, skip fill.
    if min_value == max_value:
        thumb_cx = x + w // 2
        _draw_slider_thumb(draw, thumb_cx, y, track_height, thumb_width, thumb_height, theme)
        return thumb_cx

    # Track
    track_radius = track_height // 2
    draw.rounded_rectangle(
        (x, y, x + w, y + track_height),
        radius=track_radius,
        fill=theme.surface_elevated,
    )

    # Thumb position (clamped so the thumb never exits the track ends)
    raw_cx = x + int(w * (value - min_value) / (max_value - min_value))
    thumb_cx = max(x + thumb_width // 2, min(x + w - thumb_width // 2, raw_cx))

    # Fill region
    if symmetric:
        zero_x = x + int(w * (0 - min_value) / (max_value - min_value))
        # Centre-zero tick: always draw first so fill paints over on the
        # filled side; when value==0 the tick is the only thing in the centre
        # and remains fully visible (item 1, plan 036 audit follow-up).
        draw.line(
            (zero_x, y - 1, zero_x, y + track_height + 1),
            fill=theme.separator,
            width=1,
        )
        if value > 0:
            draw.rectangle(
                (zero_x, y, thumb_cx, y + track_height),
                fill=theme.accent_blue,
            )
        elif value < 0:
            draw.rectangle(
                (thumb_cx, y, zero_x, y + track_height),
                fill=theme.accent_blue,
            )
    else:
        # Asymmetric: fill from left edge to thumb
        if thumb_cx > x:
            draw.rectangle(
                (x, y, thumb_cx, y + track_height),
                fill=theme.accent_blue,
            )

    # Thumb
    _draw_slider_thumb(draw, thumb_cx, y, track_height, thumb_width, thumb_height, theme)

    return thumb_cx


def _draw_slider_thumb(
    draw: ImageDraw.ImageDraw,
    thumb_cx: int,
    track_y: int,
    track_height: int,
    thumb_width: int,
    thumb_height: int,
    theme: Theme,
) -> None:
    """Draw the slider thumb centred vertically on the track."""
    track_cy = track_y + track_height // 2
    tx0 = thumb_cx - thumb_width // 2
    ty0 = track_cy - thumb_height // 2
    tx1 = tx0 + thumb_width
    ty1 = ty0 + thumb_height
    draw.rounded_rectangle(
        (tx0, ty0, tx1, ty1),
        radius=4,
        fill=theme.slider_thumb_fill,
        outline=theme.separator,
        width=1,
    )


def draw_vertical_slider(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    h: int,
    value: int,
    min_value: int,
    max_value: int,
    *,
    theme: Theme,
    track_width: int = 6,
    thumb_width: int = 14,
    thumb_height: int = 10,
    symmetric: bool = True,
    value_label: str | None = None,
    label_font: Font | None = None,
) -> int:
    """Vertical analog of :func:`draw_slider`.

    The track runs from ``y`` (top — max value) downward to ``y + h``
    (bottom — min value), so UP on the joystick visibly moves the
    thumb up and increases the value. Returns the pixel y-coordinate
    of the thumb centre so callers can anchor a value badge to it.

    When ``value_label`` and ``label_font`` are supplied the thumb
    grows into a pill-shaped chip with the value rendered inside it,
    matching iOS-style "value-on-knob" sliders. The chip widens to
    fit the label plus a small horizontal pad; the active-value tint
    (``accent_blue``) is used for any non-zero reading, and the
    surface fill is used at zero so the centre tick stays visible.

    Used by the focused adjustment editor: joystick UP/DOWN nudges
    the value, so orienting the slider along the same axis as the
    gesture is the gesture's natural visual proxy.
    """

    has_chip = value_label is not None and label_font is not None
    if min_value == max_value:
        thumb_cy = y + h // 2
        _draw_vertical_slider_thumb(
            draw,
            x,
            thumb_cy,
            track_width,
            thumb_width,
            thumb_height,
            theme,
            value_label=value_label if has_chip else None,
            label_font=label_font if has_chip else None,
            value=value,
        )
        return thumb_cy

    track_radius = track_width // 2
    draw.rounded_rectangle(
        (x, y, x + track_width, y + h),
        radius=track_radius,
        fill=theme.surface_elevated,
    )

    # Map value → thumb-y. Higher value sits higher on the track:
    # raw_cy_offset is a fraction of ``h`` measured *down* from y.
    raw_cy = y + int(h * (max_value - value) / (max_value - min_value))
    # When a chip thumb is in use the actual rendered height grows;
    # clamp against the wider thumb so the chip never overhangs the
    # track ends.
    effective_thumb_h = thumb_height
    thumb_cy = max(y + effective_thumb_h // 2, min(y + h - effective_thumb_h // 2, raw_cy))

    if symmetric:
        zero_y = y + int(h * (max_value - 0) / (max_value - min_value))
        # Centre-zero tick, drawn first so a non-zero fill paints over
        # on the active side; at value == 0 the tick is the only mark.
        draw.line(
            (x - 1, zero_y, x + track_width + 1, zero_y),
            fill=theme.separator,
            width=1,
        )
        if value > 0:
            draw.rectangle(
                (x, thumb_cy, x + track_width, zero_y),
                fill=theme.accent_blue,
            )
        elif value < 0:
            draw.rectangle(
                (x, zero_y, x + track_width, thumb_cy),
                fill=theme.accent_blue,
            )
    else:
        # Asymmetric: fill from bottom (min) up to the thumb.
        if thumb_cy < y + h:
            draw.rectangle(
                (x, thumb_cy, x + track_width, y + h),
                fill=theme.accent_blue,
            )

    _draw_vertical_slider_thumb(
        draw,
        x,
        thumb_cy,
        track_width,
        thumb_width,
        thumb_height,
        theme,
        value_label=value_label if has_chip else None,
        label_font=label_font if has_chip else None,
        value=value,
    )

    return thumb_cy


def _draw_vertical_slider_thumb(
    draw: ImageDraw.ImageDraw,
    track_x: int,
    thumb_cy: int,
    track_width: int,
    thumb_width: int,
    thumb_height: int,
    theme: Theme,
    *,
    value_label: str | None = None,
    label_font: Font | None = None,
    value: int = 0,
) -> None:
    """Draw the vertical slider thumb centred horizontally on the track.

    When ``value_label`` is supplied the thumb becomes a pill chip
    sized to the label rather than the default 14 × 10 dot; the
    helper keeps both shapes here so callers don't have to manage two
    drawing paths.
    """

    track_cx = track_x + track_width // 2
    if value_label is not None and label_font is not None:
        # Pill chip with the current value inside. Background tracks
        # the active state: ``accent_blue`` when the value is non-zero
        # so the chip reads as part of the filled portion of the
        # track, and ``surface_elevated`` at zero so the centre tick
        # stays visible underneath.
        pad_x = 8
        pad_y = 3
        text_w = _text_width(draw, value_label, label_font)
        text_h = _font_height(draw, "Hg", label_font)
        chip_w = max(thumb_width, text_w + pad_x * 2)
        chip_h = max(thumb_height, text_h + pad_y * 2)
        tx0 = track_cx - chip_w // 2
        ty0 = thumb_cy - chip_h // 2
        tx1 = tx0 + chip_w
        ty1 = ty0 + chip_h
        fill = theme.accent_blue if value != 0 else theme.surface_elevated
        text_color = theme.label_inverse if value != 0 else theme.label_primary
        draw.rounded_rectangle(
            (tx0, ty0, tx1, ty1),
            radius=chip_h // 2,
            fill=fill,
            outline=theme.separator,
            width=1,
        )
        text_x = tx0 + (chip_w - text_w) // 2
        text_y = ty0 + (chip_h - text_h) // 2
        _text(draw, text_x, text_y, value_label, label_font, text_color)
        return

    tx0 = track_cx - thumb_width // 2
    ty0 = thumb_cy - thumb_height // 2
    tx1 = tx0 + thumb_width
    ty1 = ty0 + thumb_height
    draw.rounded_rectangle(
        (tx0, ty0, tx1, ty1),
        radius=4,
        fill=theme.slider_thumb_fill,
        outline=theme.separator,
        width=1,
    )


# ---------------------------------------------------------------------------
# New building-block helpers
# ---------------------------------------------------------------------------


def draw_status_bar(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    now: float = 0.0,
    *,
    theme: Theme | None = None,
) -> None:
    """Draw the 36 px status bar.

    Two surface modes depending on what the user is doing:

    - **Non-Settings modes** (READY, PRINTING, SEARCHING, …): a centered
      vibrant pill carries the live status word ("Connected" / "Searching"
      / "Printing" / "Ejecting" / …). The pill colour + breath modulation
      tell the user *whether things are OK*; the word inside tells them
      *what the device is doing*. This is the resting / operational
      surface — the user mostly sees this.
    - **Settings mode**: the pill collapses to its essence. The title
      text ("Print" / "Network" / "System" / …) takes over top-left,
      naming *where you are* in the menu. A small filled circle stays at
      top-center carrying the *same* status colour the pill would have
      shown — green if the printer's ready, yellow if it's searching,
      red on error. The dot keeps reporting reality so the user can see
      the device's health while they configure. Top-right shows a page
      counter ("2/6").

    Going into Settings is "collapsing the pill into a circle" — the
    status semantics are preserved, only the surface shrinks to make
    room for the menu's title text.
    """

    if theme is None:
        theme = theme_for("light")

    state = derive_status(snapshot)
    font_body = fonts["body"]
    font_small = fonts["small"]

    # Bar background — neutral, no tint
    draw.rectangle((0, 0, 239, STATUS_BAR_H - 1), fill=theme.bg)

    if snapshot.mode is UiMode.SETTINGS:
        _draw_status_bar_settings(draw, snapshot, state, font_body, font_small, now, theme)
    else:
        _draw_status_bar_pill(draw, snapshot, state, font_body, now, theme)


def _draw_status_bar_pill(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    state: StatusState,
    font_body: Font,
    now: float,
    theme: Theme,
) -> None:
    """Operational status bar: centered pill with the live status word."""

    word = t(status_bar_word(snapshot), snapshot.language)

    pill_bg_rgb = _state_pill_bg(state)
    pill_bg_tinted = _apply_breath(state, pill_bg_rgb, now)
    pill_bg_hex = _rgb_to_hex(pill_bg_tinted)

    fg = state.foreground()
    fg_hex = _rgb_to_hex(fg)

    # Pill width: tighter for Latin (24 px horizontal padding), looser for
    # CJK glyphs which sit wider and look cramped at the Latin floor.
    word_bbox = draw.textbbox((0, 0), word, font=font_body)
    word_w = int(word_bbox[2] - word_bbox[0])
    if _has_cjk(word):
        pill_w = max(76, word_w + 32)
    else:
        pill_w = max(60, word_w + 24)
    pill_h = 22

    pill_x = 120 - pill_w // 2
    # Optical centring: +1 px so the pill floats with 8 px above / 6 px
    # below rather than equal 7/7 margins (plan 034 item 6).
    pill_y = (STATUS_BAR_H - pill_h) // 2 + 1

    draw_pill(draw, pill_x, pill_y, pill_w, pill_h, pill_bg_hex, fg_hex, word, font_body)


def _draw_status_bar_settings(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    state: StatusState,
    font_body: Font,
    font_small: Font,
    now: float,
    theme: Theme,
) -> None:
    """Settings status bar: page title (left) + status dot (center) +
    page counter (right)."""

    # --- Title (top-left) -------------------------------------------------
    title = t(status_bar_word(snapshot), snapshot.language)
    title_bbox = draw.textbbox((0, 0), title, font=font_body)
    title_h = int(title_bbox[3] - title_bbox[1])
    title_top = int(title_bbox[1])
    # Optical centring: +1 px so the cap-height sits visually mid-bar even
    # though the descent-anchored bbox would otherwise look top-heavy.
    title_y = (STATUS_BAR_H - title_h) // 2 - title_top + 1
    _text(draw, 12, title_y, title, font_body, theme.label_primary)

    # --- Status dot (top-center) ------------------------------------------
    # Inherits the underlying device health (status_indicator._settings_inherit).
    # Same colour + breath the pill would have shown if Settings weren't open.
    dot_rgb = _apply_breath(state, _state_pill_bg(state), now)
    dot_hex = _rgb_to_hex(dot_rgb)
    dot_radius = 6  # 12 px diameter — visible at arm's length, not loud
    dot_cx = 120
    dot_cy = STATUS_BAR_H // 2 + 1  # mirrors title's +1 optical centring
    draw.ellipse(
        (dot_cx - dot_radius, dot_cy - dot_radius, dot_cx + dot_radius, dot_cy + dot_radius),
        fill=dot_hex,
    )

    # --- Page counter (top-right) -----------------------------------------
    if snapshot.settings_rows:
        selected = min(snapshot.selected_index, len(snapshot.settings_rows) - 1)
        counter = f"{selected + 1}/{len(snapshot.settings_rows)}"
        counter_bbox = draw.textbbox((0, 0), counter, font=font_small)
        # textbbox returns floats in current Pillow stubs; coerce so the
        # downstream pixel coordinates stay strictly int.
        counter_w = int(counter_bbox[2] - counter_bbox[0])
        counter_h = int(counter_bbox[3] - counter_bbox[1])
        counter_top = int(counter_bbox[1])
        counter_x = 232 - counter_w
        counter_y = (STATUS_BAR_H - counter_h) // 2 - counter_top
        _text(draw, counter_x, counter_y, counter, font_small, theme.label_secondary)


def _state_pill_bg(state: StatusState) -> tuple[int, int, int]:
    """Return the full-intensity pill background RGB for a StatusState.

    The status-bar pill / dot colour is the state's `base_color` — green
    for READY/PRINTING, yellow for SEARCHING/NOT_READY, red for ERROR/
    WARNING. The earlier theme-aware blue routing for SETTINGS was
    reverted; the Settings dot now inherits the underlying device
    health.
    """
    return state.base_color


def _apply_breath(
    state: StatusState,
    rgb: tuple[int, int, int],
    now: float,
) -> tuple[int, int, int]:
    """Return ``rgb`` unchanged — the breath envelope is disabled.

    Previously this returned ``state.tint_at(now)`` so the pill's tint
    pulsed across a 2 s breath cycle. That kept the render loop at 16 fps
    whenever a breathing state was active (`PRINTER_SEARCHING`, `PRINTING`,
    etc.) and pegged the Pi Zero 2 W's main asyncio thread near 80 %.

    Switching to solid colour drops the active-state CPU cost ~60 %.
    The state's colour family still signals the mode (yellow = working,
    green = ready, red = attention); the visual energy of the pulse is
    gone, but on a 240 × 240 LCD that read mostly as "the chip is busy"
    rather than as "the work is progressing".
    """

    del state, now  # signature preserved for forward-compat call sites
    return rgb


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


# Mapping from UiMode → one-word status the top bar shows. Kept here rather
# than in status_indicator.py because these are LCD-surface labels (the GPIO
# headless surface has no text); the colours/patterns are owned by
# status_indicator.StatusSignal which we share across surfaces.
_MODE_STATUS_WORD: dict[UiMode, str] = {
    UiMode.BOOTING: "Starting",
    UiMode.NEEDS_PAIRING: "No printer",
    UiMode.PAIRING: "Pairing",
    UiMode.PAIR_FAILED: "Pair failed",
    UiMode.PRINTER_SEARCHING: "Searching",
    UiMode.PRINTER_OFFLINE: "Disconnected",
    UiMode.VALIDATION: "Validating",
    UiMode.NO_FILM: "No film",
    UiMode.IMAGE_RECEIVED: "Received",
    UiMode.AWAITING_CONFIRM: "Preview",
    UiMode.PRINTING: "Printing",
    UiMode.PRINT_COMPLETE: "Ejecting",
    UiMode.ERROR: "Error",
    UiMode.SETTINGS: "Settings",
    UiMode.ADJUSTMENT_EDIT: "Adjustments",
}


def status_bar_word(snapshot: UiSnapshot) -> str:
    """Return the one-word status the top bar should display.

    READY is split into ``Connected`` (printer + FTP path are healthy) and
    ``Waiting`` (something is missing) so the user can tell at a glance
    whether the next ``FTP Trans. (This Img.)`` will actually print.

    PRINTER_SEARCHING is split into ``Searching`` (actively probing) and
    ``Disconnected`` (passively waiting on the user — the body says "Turn
    printer on"). Pairs with the status indicator's NOT_READY-solid vs
    SEARCHING-breathing split so the colour pattern and the word agree.
    """

    mode = snapshot.mode
    if mode is UiMode.SETTINGS:
        # Use the sub-page name populated by the controller (Print, Network,
        # System, About, Accessibility). Fall back to "Settings" when absent
        # so the bare root-menu view still gets a readable pill (plan 034 item 1b).
        return snapshot.settings_title if snapshot.settings_title else "Settings"
    if mode is UiMode.ADJUSTMENT_EDIT:
        # Show the axis being edited at the top of the screen rather than
        # the generic "Adjustments". Frees the card body for the preview
        # tile + slider + value badge and matches the macOS sheet's
        # title-at-top layout.
        return _ADJUSTMENT_EDIT_KEY_TO_LABEL.get(
            snapshot.adjustment_edit_key or "", "Adjustments"
        )
    if mode is UiMode.HELP_DIALOG:
        # The dialog overlay carries its own title within the card body;
        # the status bar reflects the underlying surface so the dim
        # background still reads as Adjustments / Settings context.
        return _MODE_STATUS_WORD.get(mode, "Help")
    if mode is UiMode.READY:
        return "Connected" if can_accept_images(snapshot) else "Waiting"
    if mode is UiMode.PRINTER_SEARCHING and _is_waiting_for_user_message(
        snapshot.printer_status_message
    ):
        return "Disconnected"
    return _MODE_STATUS_WORD.get(mode, "")


# Map adjustment-edit key (SettingKey.value) → canonical axis label
# shown in the top status bar. Mirrored in
# ``controller._ADJUSTMENT_AXIS_LABEL``; render.py keeps its own copy
# because the renderer must not import from controller.
_ADJUSTMENT_EDIT_KEY_TO_LABEL: dict[str, str] = {
    "adjust_saturation": "Saturation",
    "adjust_exposure": "Exposure",
    "adjust_sharpness": "Sharpness",
    "adjust_hue": "Hue",
    "adjust_vignette": "Vignette",
    "adjust_datestamp": "Datestamp",
    "adjust_watermark": "Watermark",
}


# Lightweight mirror of status_indicator._WAITING_FOR_USER_MESSAGES so the
# top-bar word can stay in lockstep with the indicator pattern without a
# circular import. Update both when adding a new "no BLE signal" message.
_TOP_BAR_WAITING_MESSAGES: frozenset[str] = frozenset(
    {
        "No printer signal",
        "Scanning: 0 printers",
    }
)


def _is_waiting_for_user_message(message: str | None) -> bool:
    return message is not None and message in _TOP_BAR_WAITING_MESSAGES


def draw_body_message(
    draw: ImageDraw.ImageDraw,
    lines: list[tuple[str, str]],
    start_y: int,
    fonts: dict[str, Font],
) -> None:
    """Draw a list of (text, color) lines stacked from start_y with 18px spacing."""

    y = start_y
    for text, color in lines:
        _text(draw, 18, y, text, fonts["body"], color)
        y += 18


def draw_hint_bar(
    draw: ImageDraw.ImageDraw,
    hints: tuple[str, str, str],
    font: Font,
    theme: Theme | None = None,
) -> None:
    """Draw the two-line hint bar at the bottom of the screen.

    Each non-empty hint becomes a capsule pill stacking the key label on
    line 1 (e.g. ``K1``) and the action label on line 2 (e.g. ``Setting``)
    so each chip gets the full 80 px zone width for one short word per
    line instead of trying to fit "K1 Setting" inline.

    The split happens on the first space — strings without a space render
    on a single (centred) line.
    """

    if theme is None:
        theme = theme_for("light")

    draw.rectangle((0, HINT_BAR_Y - 2, 239, 239), fill=theme.bg)

    # Single uniform pill spanning the bottom bar with the three hints
    # evenly distributed inside it. The previous three-individual-chip
    # design introduced visual artefacts at the chip boundaries (extra
    # spacing variations, asymmetric gaps when one chip was short and
    # another long). A single bar is calmer to look at and reads as
    # one cohesive control surface.
    pill_h = HINT_BAR_H - 8
    pill_radius = pill_h // 2  # full capsule
    pill_x0 = 8
    pill_x1 = 232
    pill_y = HINT_BAR_Y + (HINT_BAR_H - pill_h) // 2

    draw.rounded_rectangle(
        (pill_x0, pill_y, pill_x1, pill_y + pill_h),
        radius=pill_radius,
        fill=theme.hint_bg,
    )

    # Three equal columns inside the pill; column centers at 1/6, 3/6, 5/6
    # of the bar width so each label sits centred in its third regardless
    # of its width.
    bar_w = pill_x1 - pill_x0
    col_centers = (
        pill_x0 + bar_w // 6,
        pill_x0 + bar_w // 2,
        pill_x0 + bar_w * 5 // 6,
    )
    # Per-column text width budget — leave a small horizontal inset so
    # adjacent columns never visually merge. The capsule radius eats some
    # space on the outer columns, so cap conservatively.
    col_max_w = bar_w // 3 - 8

    for text, cx in zip(hints, col_centers, strict=True):
        if not text:
            continue

        # Split first token (the key) from the rest of the action label.
        # "KEY1 Setting" → ("KEY1", "Setting"); "Hold KEY3" → ("Hold", "KEY3");
        # "Done" → ("Done", "").
        if " " in text:
            line1, line2 = text.split(" ", 1)
        else:
            line1, line2 = text, ""

        fitted1 = _fit_text_to_width(draw, line1, font, col_max_w)
        fitted2 = _fit_text_to_width(draw, line2, font, col_max_w)
        tw1 = _text_width(draw, fitted1, font)
        tw2 = _text_width(draw, fitted2, font)

        bbox1 = draw.textbbox((0, 0), fitted1, font=font)
        line_h = bbox1[3] - bbox1[1]
        if fitted2:
            gap = 4
            total_h = line_h * 2 + gap
            line1_y = pill_y + (pill_h - total_h) // 2 - bbox1[1]
            line2_y = line1_y + line_h + gap
            tx1 = cx - tw1 // 2 - bbox1[0]
            tx2 = cx - tw2 // 2 - draw.textbbox((0, 0), fitted2, font=font)[0]
            # Line 1 = key label (KEY1/KEY2/KEY3): promote to label_primary so
            # the eye can scan key → action at arm's length (plan 034 item 11).
            # Line 2 = action word: stays in hint_fg (muted).
            draw.text((tx1, line1_y), fitted1, fill=theme.label_primary, font=font)
            draw.text((tx2, line2_y), fitted2, fill=theme.hint_fg, font=font)
        else:
            line1_y = pill_y + (pill_h - line_h) // 2 - bbox1[1]
            tx1 = cx - tw1 // 2 - bbox1[0]
            draw.text((tx1, line1_y), fitted1, fill=theme.hint_fg, font=font)


# Destructive run-action row labels (English source). Render layer tints
# these in ``theme.accent_destructive`` so the user sees the two-press
# confirm semantics before pressing KEY1 (plan 037 polish #5). Translated
# labels are mapped via t() at the controller layer, but the source-string
# match is reliable because the renderer receives already-translated text;
# the t() lookup degrades to the source on a miss so a label like "Forget"
# (English) and "忘记此设备" (zh-Hans) both pattern-match against this set
# at draw time when the source string is the row's ``label`` argument.
# Render layer receives ``label`` after t() — so include both forms.
_DESTRUCTIVE_ACTION_LABELS: frozenset[str] = frozenset(
    {
        "Forget",
        "Re-pair",
        "Reset credentials",
        "Reset BLE link",
        # zh-Hans variants (matches i18n.py table).
        "忘记此设备",
        "重新配对",
        "还原凭据",
        "BLE 连接已还原",
    }
)


def draw_settings_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    value: str,
    hint: str,
    *,
    selected: bool,
    font: Font,
    marker_font: Font | None = None,
    theme: Theme | None = None,
    row_height: int = 19,
    is_header: bool = False,
    header_font: Font | None = None,
) -> None:
    """Draw a settings row in iOS picker style.

    Selected row: ``theme.accent_blue`` background, ``theme.label_inverse`` text.
    Non-selected: transparent strip; label in ``theme.label_primary``, value in
    ``theme.label_secondary``.

    ``marker_font`` lets the caller render the trailing chevron in a heavier
    size than the row text. On iOS the disclosure chevron is visibly larger
    than the surrounding label; we mirror that by passing ``fonts["body"]``
    (≈1.4× the row font) so the chevron reads as an affordance, not as a
    stray punctuation glyph.

    ``is_header=True`` renders the row as an iOS-style section header:
    smaller font, secondary tint, no chevron, no selection highlight, no
    trailing hairline (the caller is responsible for suppressing the
    separator below header rows). Header rows take precedence over the
    legacy ``hint == "" and value == ""`` heuristic — pass ``is_header``
    explicitly when the caller knows the row's role.
    """

    if theme is None:
        theme = theme_for("light")
    if marker_font is None:
        marker_font = font

    # Section header rows: render as muted dividers — no highlight, no
    # chevron, smaller font — so they read as labels rather than selectable
    # rows (plan 037 polish #1, building on plan 036 audit item 5). The
    # ``is_header`` flag is the cleanest signal; we also retain the legacy
    # ``hint == "" and value == ""`` heuristic so callers that haven't been
    # migrated still get correct rendering.
    if is_header or (hint == "" and value == ""):
        # Never draw a selection highlight: the row is not actionable.
        # Use a slightly smaller font when available (header_font), and add
        # 2 px leading whitespace above so the header floats above the
        # following row instead of sharing its baseline.
        font_to_use = header_font if header_font is not None else font
        label_max = 200  # plenty of width — no value column
        _text(
            draw,
            22,
            y + 4,
            _fit_text_to_width(draw, label, font_to_use, label_max),
            font_to_use,
            theme.label_secondary,
        )
        return

    kind = _settings_row_kind(hint)
    marker, _marker_fill = _settings_row_marker(kind, selected)

    if selected:
        # Selected row: flat vibrant accent fill (iOS picker style). Radius
        # matches the outer card (10 px) so the highlight reads as a single
        # rounded "pebble" rather than a tight ribbon nested inside a
        # softer container — the previous 4 px corners visibly disagreed
        # with the card's 10 px corners.
        draw.rounded_rectangle(
            (14, y, 226, y + row_height - 1),
            radius=10,
            fill=theme.accent_blue,
        )
        text_fill = theme.label_inverse
        value_fill = theme.label_inverse
    elif kind == "run":
        # Action rows (Pair, Reconnect, Forget, Reset credentials,
        # Save current, Refresh status, Re-pair) — tint the label so the
        # row reads as actionable rather than informational. Destructive
        # actions (Re-pair, Forget, Reset credentials) get the red accent;
        # everything else gets the iOS blue (plan 037 polish #5).
        text_fill = (
            theme.accent_destructive if label in _DESTRUCTIVE_ACTION_LABELS else theme.accent_blue
        )
        value_fill = theme.label_secondary
    else:
        text_fill = theme.label_primary
        value_fill = theme.label_secondary
    # iOS chevron tint: secondary grey on normal rows, inverse on selected.
    # Always tracks the row's text colour so it disappears into the active-
    # row glow rather than fighting it with a competing accent (the previous
    # blue/green/yellow chevrons clashed with the selected-row blue fill).
    marker_fill = theme.label_inverse if selected else theme.label_secondary

    label_max = 94
    _text(draw, 22, y + 3, _fit_text_to_width(draw, label, font, label_max), font, text_fill)

    if marker:
        # Anchor the chevron at the right edge (x=218) and the row's true
        # vertical midpoint using PIL's anchor="rm" (right-middle). This
        # uses the font's actual visual metrics rather than the glyph
        # bbox, so the chevron sits optically centred no matter the font
        # scale. The previous formula keyed off `_font_height` which
        # only measured the chevron glyph itself and consistently parked
        # it 2-3 px below the row centre.
        row_cy = y + row_height // 2
        marker_width = _text_width(draw, marker, marker_font)
        draw.text(
            (218, row_cy),
            marker,
            font=marker_font,
            fill=marker_fill,
            anchor="rm",
        )
        # Widen gap from chevron to value text: -6 instead of -4 so the value
        # field doesn't crash into the chevron at LARGE font scale (plan 034 item 16).
        value_right = 218 - marker_width - 6
    else:
        value_right = 218

    # Guarantee a minimum 40 px value field before truncating. If the available
    # space is narrower (can happen at LARGE scale with a wide chevron) fall back
    # to rendering nothing — the chevron alone signals that the row is actionable.
    value_field_w = value_right - 122
    if value_field_w < 40:
        return
    value_text = _fit_text_to_width(draw, value, font, value_field_w)
    value_width = _text_width(draw, value_text, font)
    _text(draw, max(122, value_right - value_width), y + 3, value_text, font, value_fill)


# ---------------------------------------------------------------------------
# Mode renderers
# ---------------------------------------------------------------------------


def _booting(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(draw, [t("Starting", snapshot.language)], 75, fonts["large"], theme.label_primary)
    # No hint bar for BOOTING


def _ready(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    if not can_accept_images(snapshot):
        _validation(draw, snapshot, fonts, theme)
        return

    # Hoist hot-path locals: `snapshot.language` and `fonts["small"]` are
    # read 10+ times in this function. Pulling them into locals removes
    # the repeated attribute / dict lookups from the render path.
    lang = snapshot.language
    font_small = fonts["small"]

    # Centered title — translated via i18n so it reads "就绪" / "Ready".
    # y=56 (was 75): pushes the title up so the info card at y=104 has a
    # comfortable ~18 px gap below the title instead of touching it.
    _center_lines(draw, [t("Ready", lang)], 56, fonts["large"], theme.label_primary)

    # Card spanning x=12..228, y=104..200
    card_x, card_y = 12, 104
    card_w, card_h = 216, 96
    draw_card(draw, card_x, card_y, card_w, card_h, theme)

    # Build the list of row "groups". Each group is one rendered row; a
    # group with one (label, value) draws a normal full-width row, a group
    # with two draws a half-card pair separated by a vertical hairline. The
    # split row is what compacts Film and Battery into a single line.
    row_groups: list[list[tuple[str, str]]] = []

    if snapshot.paired_printer is not None:
        row_groups.append([(t("Type", lang), _status_bar_printer_name(snapshot))])

    film_cell: tuple[str, str] | None = None
    if snapshot.film_remaining is not None:
        film_cell = (
            t("Film", lang),
            f"{snapshot.film_remaining}/{snapshot.film_capacity}",
        )

    battery_cell: tuple[str, str] | None = None
    if snapshot.printer_battery is not None:
        charging = "+" if snapshot.printer_is_charging else ""
        # Drop the body-line battery-life estimate from this row: pairing
        # Film + Battery on one line leaves no room for "(4h32m left)", and
        # the user-facing value (percentage) is the part that matters at
        # arm's length. Battery-life is still surfaced via the helper for
        # the future Mac/headless views.
        battery_cell = (t("Battery", lang), f"{snapshot.printer_battery}%{charging}")

    # Pair Film + Battery on a single split row when both are present;
    # fall back to a single-row render if only one is available.
    if film_cell is not None and battery_cell is not None:
        row_groups.append([film_cell, battery_cell])
    elif film_cell is not None:
        row_groups.append([film_cell])
    elif battery_cell is not None:
        row_groups.append([battery_cell])

    # Bare-serial Printer row removed: the serial is already in
    # Settings → Print → Serial and duplicates nothing useful at print
    # time. Replace with FTP host + SSID the user actually needs during
    # camera setup (plan 034 item 7).
    #
    # Layout: Host gets a dedicated full-width row (camera's FTP server
    # field — the most critical value). SSID gets a second full-width row
    # when known. A split row was tried but neither value fits in the 92 px
    # half-card at body font; the plan says "prioritise Host and full-line
    # the SSID below it" when both don't fit.
    ftp_host_addr: str
    if snapshot.hotspot_host is not None:
        ftp_host_addr = snapshot.hotspot_host
    elif snapshot.wifi_host is not None:
        ftp_host_addr = snapshot.wifi_host
    else:
        ftp_host_addr = _ready_ftp_line(snapshot)
    row_groups.append([(t("Host", lang), ftp_host_addr)])
    ssid = snapshot.hotspot_ssid
    if ssid is not None:
        row_groups.append([(t("Wi-Fi", lang), ssid)])

    depth = snapshot.image_queue_depth
    if depth == 1:
        row_groups.append([(t("Queue", lang), t("1 photo", lang))])
    elif depth > 1:
        # `t()` falls through to the source on a miss, so wrapping the
        # plural word always yields *some* string — no second branch
        # needed. The conditional pluralisation lived here as a leftover
        # from an earlier i18n design and was equivalent to its else arm.
        row_groups.append([(t("Queue", lang), f"{depth} {t('photos', lang)}")])

    if not row_groups:
        hints = _mode_hints(snapshot)
        draw_hint_bar(draw, hints, fonts["hint"], theme)
        return

    # Distribute groups within the card.
    num_rows = len(row_groups)
    row_h = min(card_h // num_rows, 20)  # cap to avoid oversized rows
    total_content = num_rows * row_h
    start_y = card_y + (card_h - total_content) // 2
    # Vertical offset to the label baseline — constant across all rows
    # so the font-height query happens once instead of once per loop iter.
    label_dy = (row_h - _font_height(draw, "Ag", font_small)) // 2
    label_x_full = card_x + 16
    cell_w = (card_w - 32) // 2  # half-card width for split rows
    divider_x = card_x + card_w // 2

    for i, group in enumerate(row_groups):
        ry = start_y + i * row_h
        label_y = ry + label_dy

        if len(group) == 1:
            # Single full-width row — Type / Printer / Queue.
            label, value = group[0]
            prefix = f"{label}: "
            lw = _text_width(draw, prefix, font_small)
            _text(draw, label_x_full, label_y, prefix, font_small, theme.label_secondary)
            _text(draw, label_x_full + lw, label_y, value, font_small, theme.label_primary)
        else:
            # Split row: each cell takes a half-card with a vertical
            # hairline divider between them. Labels keep the trailing
            # ":" so the eye can still bind label↔value across the gap.
            # The plan-034 audit briefly promoted the value to
            # fonts["body"] for a "small grey label → big black value"
            # hierarchy, but on the live LCD that left the Film + Battery
            # row visibly taller than the single-cell rows above and
            # below — the size delta read as inconsistency rather than
            # hierarchy. The colour delta (label_secondary → label_primary)
            # alone is enough at LCD viewing distance, so the value
            # now renders in ``font_small`` matching every other row.
            for cell_idx, (label, value) in enumerate(group):
                cx = label_x_full + cell_idx * cell_w
                prefix = f"{label}: "
                lw = _text_width(draw, prefix, font_small)
                _text(draw, cx, label_y, prefix, font_small, theme.label_secondary)
                _text(draw, cx + lw, label_y, value, font_small, theme.label_primary)
            # Vertical divider — 1 px hairline in the same secondary
            # tint as the row hairlines, inset from the row top/bottom
            # so it reads as a slim "·" between the cells, not a frame.
            draw.line(
                (divider_x, ry + 3, divider_x, ry + row_h - 5),
                fill=theme.separator,
                width=1,
            )

        # Hairline after row (except last) — 16 px leading inset matches
        # iOS default UITableViewCell.separatorInset (16 pt leading).
        if i < num_rows - 1:
            draw_hairline(draw, label_x_full, ry + row_h - 1, card_w - 32, theme)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _validation(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    accepting = can_accept_images(snapshot)
    _center_lines(
        draw,
        [t("Ready", snapshot.language) if accepting else t("Setup needed", snapshot.language)],
        75,
        fonts["large"],
        theme.label_primary,
    )
    causes = readiness_cause_texts(snapshot)
    if not causes:
        _text(
            draw,
            18,
            118,
            t("FTP and printer ready", snapshot.language),
            fonts["body"],
            theme.label_primary,
        )
        _text(
            draw,
            18,
            136,
            t("Waiting for upload", snapshot.language),
            fonts["small"],
            theme.label_secondary,
        )
    else:
        # "Next action" label moved to y=118 and first cause to y=136 to
        # tighten the label→cause binding and add breathing room above
        # (plan 034 item 15: +6 px shift vs previous y=112/132).
        _text(
            draw, 18, 118, t("Next action", snapshot.language), fonts["body"], theme.label_primary
        )
        for index, cause in enumerate(causes[:3]):
            # cause strings come from readiness_cause_texts in English; the
            # i18n table has entries for the common ones ("Turn printer on",
            # "Wait for printer", "Replace film pack"). Translate at draw
            # time so 中文 mode picks them up.
            _text(
                draw,
                18,
                136 + index * 17,
                _ellipsize(t(cause, snapshot.language), 31),
                fonts["small"],
                theme.accent_yellow,
            )

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _no_film(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(draw, [t("No film", snapshot.language)], 75, fonts["large"], theme.label_primary)
    _center_lines(
        draw,
        [t("No-film test is in Settings", snapshot.language)],
        128,
        fonts["small"],
        theme.label_secondary,
    )

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _printer_searching(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    message = snapshot.printer_status_message or t("Keep printer awake", snapshot.language)
    if message in {"Restart printer", "Close phone app"}:
        _center_lines(
            draw, [t("Blocked", snapshot.language)], 75, fonts["large"], theme.label_primary
        )
        _center_lines(
            draw,
            [t("Close phone app or phone BT", snapshot.language)],
            128,
            fonts["small"],
            theme.accent_yellow,
        )
        _center_lines(
            draw,
            [t("Power-cycle printer, then retry", snapshot.language)],
            146,
            fonts["small"],
            theme.label_secondary,
        )
    elif message == "Printer seen; connecting":
        _center_lines(
            draw, [t("Connecting", snapshot.language)], 75, fonts["large"], theme.label_primary
        )
        _center_lines(
            draw,
            [t("Opening Bluetooth session", snapshot.language)],
            128,
            fonts["small"],
            theme.label_primary,
        )
        _center_lines(
            draw,
            [t("If stuck, close phone app", snapshot.language)],
            146,
            fonts["small"],
            theme.label_secondary,
        )
    elif message == "Saw other Instax":
        _center_lines(
            draw, [t("Wrong one", snapshot.language)], 75, fonts["large"], theme.label_primary
        )
        _center_lines(
            draw,
            [t("Selected printer not visible", snapshot.language)],
            128,
            fonts["small"],
            theme.label_primary,
        )
        _center_lines(
            draw,
            [t("Turn selected printer on", snapshot.language)],
            146,
            fonts["small"],
            theme.accent_yellow,
        )
    elif message in {"Scanning: 0 printers", "No printer signal"}:
        # No BLE signal yet — promote the action to the title slot since
        # the status pill already shows "Searching" (plan 037 polish #9
        # drops the duplicate "Searching" body title).
        _center_lines(
            draw,
            [t("Turn printer on", snapshot.language)],
            75,
            fonts["large"],
            theme.label_primary,
        )
        _center_lines(
            draw,
            [t("Bridge keeps trying", snapshot.language)],
            128,
            fonts["small"],
            theme.label_secondary,
        )
    else:
        # Status pill at the top already shows "Searching"; promote the
        # live retry message into the title slot so the body says what's
        # actually happening (plan 037 polish #9).
        _center_lines(
            draw,
            [_ellipsize(t(message, snapshot.language), 22)],
            75,
            fonts["large"],
            theme.label_primary,
        )

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _printer_offline(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    message = snapshot.printer_status_message or t("Printer offline", snapshot.language)
    if message == "Checking printer":
        _center_lines(
            draw, [t("Checking", snapshot.language)], 75, fonts["large"], theme.label_primary
        )
    elif message == "Hold K3 to re-pair":
        _center_lines(
            draw, [t("No printer", snapshot.language)], 75, fonts["large"], theme.label_primary
        )
        _center_lines(
            draw,
            [t("Printer not found nearby", snapshot.language)],
            128,
            fonts["body"],
            theme.accent_yellow,
        )
        hints = _mode_hints(snapshot)
        draw_hint_bar(draw, hints, fonts["hint"], theme)
        return
    else:
        _center_lines(
            draw, [t("Printer off", snapshot.language)], 75, fonts["large"], theme.label_primary
        )
    _center_lines(
        draw,
        [t("Keep it awake near bridge", snapshot.language)],
        128,
        fonts["body"],
        theme.label_primary,
    )

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _image_received(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(draw, [t("Received", snapshot.language)], 75, fonts["large"], theme.label_primary)
    if snapshot.last_image_name is not None:
        _text(
            draw,
            18,
            126,
            _ellipsize(snapshot.last_image_name, 25),
            fonts["body"],
            theme.label_primary,
        )
    _text(
        draw,
        18,
        148,
        t("Received over FTP", snapshot.language),
        fonts["small"],
        theme.label_secondary,
    )
    _text(draw, 18, 164, film_status_text(snapshot), fonts["small"], theme.label_secondary)
    draw_hint_bar(draw, ("", t("Auto print", snapshot.language), ""), fonts["hint"], theme)


def _awaiting_confirm(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    title = snapshot.print_title or t("Printing soon", snapshot.language)
    detail = _physical_control_text(
        snapshot.print_detail or t("Press K2 to cancel", snapshot.language)
    )
    if snapshot.preview_image is not None:
        # Wrap preview in a card
        draw_card(draw, 16, 40, 208, 114, theme)
        preview = snapshot.preview_image
        x = 120 - preview.width // 2
        y = 96 - preview.height // 2
        canvas.paste(preview, (x, y))
        _text(draw, 18, 158, _ellipsize(title, 27), fonts["body"], theme.label_primary)
        _text(draw, 18, 175, _ellipsize(detail, 31), fonts["small"], theme.accent_yellow)
        _text(
            draw,
            18,
            190,
            _ellipsize(preview_state_text(snapshot), 31),
            fonts["small"],
            theme.label_secondary,
        )
    else:
        _center_lines(draw, [title], 62, fonts["large"], theme.label_primary)
        if snapshot.last_image_name is not None:
            _text(
                draw,
                18,
                104,
                _ellipsize(snapshot.last_image_name, 25),
                fonts["body"],
                theme.label_primary,
            )
        _progress_bar(
            draw, 18, 128, snapshot.print_progress_percent, theme.accent_blue, fonts["small"], theme
        )
        _text(draw, 18, 154, _ellipsize(detail, 31), fonts["small"], theme.accent_yellow)
        _text(draw, 18, 172, film_status_text(snapshot), fonts["small"], theme.label_secondary)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _printing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    title = snapshot.print_title or t("Sending to printer", snapshot.language)
    _center_lines(draw, [title], 58, fonts["large"], theme.label_primary)
    # `print_detail` is only populated during non-SENDING stages now
    # (e.g. "Checking printer"). The chunk-count / KB sub-string was
    # dropped per user feedback: the progress bar + the percent-suffixed
    # title carry the full signal. When the BLE callback sets detail to
    # None, skip the secondary line entirely so we don't show a redundant
    # "Working" fallback. y=96 is reserved; the progress bar stays at 122.
    if snapshot.print_detail:
        _text(
            draw,
            18,
            96,
            _ellipsize(snapshot.print_detail, 31),
            fonts["body"],
            theme.label_primary,
        )
    _progress_bar(
        draw, 18, 122, snapshot.print_progress_percent, theme.accent_blue, fonts["small"], theme
    )
    if snapshot.last_image_name is not None:
        _text(
            draw,
            18,
            150,
            _ellipsize(snapshot.last_image_name, 25),
            fonts["small"],
            theme.label_secondary,
        )
    _text(draw, 18, 166, printer_model_text(snapshot), fonts["small"], theme.label_secondary)
    _text(
        draw, 18, 182, t("Do not power off", snapshot.language), fonts["small"], theme.accent_yellow
    )
    # No hint bar for PRINTING


def _print_complete(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(draw, [t("Ejecting", snapshot.language)], 75, fonts["large"], theme.label_primary)
    if snapshot.last_image_name is not None:
        _text(
            draw,
            18,
            126,
            _ellipsize(snapshot.last_image_name, 25),
            fonts["body"],
            theme.label_primary,
        )
    _text(
        draw,
        18,
        148,
        t("Film ejecting", snapshot.language),
        fonts["small"],
        theme.label_secondary,
    )
    _text(draw, 18, 164, film_status_text(snapshot), fonts["small"], theme.label_secondary)
    draw_hint_bar(draw, _mode_hints(snapshot), fonts["hint"], theme)


def _needs_pairing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(
        draw, [t("No printer", snapshot.language)], 75, fonts["large"], theme.label_primary
    )
    _menu_item(
        draw,
        122,
        t("Find printer", snapshot.language),
        selected=True,
        font=fonts["body"],
        theme=theme,
    )
    _text(
        draw,
        18,
        162,
        t("Turn on printer first", snapshot.language),
        fonts["small"],
        theme.label_secondary,
    )
    _text(
        draw, 18, 178, t("Then press K1", snapshot.language), fonts["small"], theme.label_secondary
    )

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _settings(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    rows = snapshot.settings_rows
    # Hoist the language string out of the loop — it's hit twice per row
    # for label/value translation, and reading the dataclass attr 30+
    # times per Settings render adds up at 16 fps.
    lang = snapshot.language
    font = fonts["small"]
    # The trailing chevron "›" (U+203A) is a Latin glyph; the CJK font used
    # when language=zh-Hans (wqy-zenhei / Hiragino fall-back) has no entry
    # for it and would render tofu (□). Build a Latin-only body-sized font
    # for the marker so the chevron lands cleanly in every language.
    font_scale, row_scale = _scale_for_snapshot(snapshot)
    marker_font = _font(max(1, round(_BASE_FONTS["body"] * font_scale)), prefer_cjk=False)
    # Section-header rows render in a 9 pt font (smaller than the 10 pt small
    # body) so they read as labels rather than greyed-out picker rows
    # (plan 037 polish #1).
    header_font = _font(max(1, round(9 * font_scale)), prefer_cjk=(lang == "zh-Hans"))
    if not rows:
        _text(draw, 18, 58, t("No settings available", lang), fonts["body"], theme.label_primary)
        draw_hint_bar(draw, _mode_hints(snapshot), fonts["hint"], theme)
        return

    row_height = max(1, round(_BASE_ROW_HEIGHT * row_scale))

    selected = min(snapshot.selected_index, len(rows) - 1)
    selected_row = rows[selected]

    # Toast (settings_message) takes priority over help text. Both share a
    # dedicated strip *below* the rounded card so they can never overlap the
    # card's rounded borders.
    toast_message = snapshot.settings_message
    help_text = selected_row.help if selected_row.help else ""
    # Destructive confirms now use the iOS-style dialog overlay (plan 040), so
    # ``settings_message`` here is informational only ("Saved", "Choose
    # action", etc.) — always rendered in the existing accent_yellow help
    # tint. The legacy destructive-toast red-strip path was removed when the
    # controller stopped emitting those toasts.
    if toast_message is not None:
        bottom_text = toast_message
        bottom_color = theme.accent_yellow
    elif help_text:
        bottom_text = help_text
        bottom_color = theme.label_secondary
    else:
        bottom_text = ""
        bottom_color = theme.label_secondary
    bottom_shown = bool(bottom_text)

    # Card occupies the body area; if a bottom strip is shown, leave room
    # for up to two lines of help text (28 px) below the card so it has
    # space to wrap when the help string is long. Single short strings
    # still occupy only one visible line.
    card_top = STATUS_BAR_H + 2
    card_bottom = (HINT_BAR_Y - 28) if bottom_shown else (HINT_BAR_Y - 4)
    card_h = card_bottom - card_top

    # Compute how many rows fit inside the card with 4 px padding top/bottom.
    body_height = card_h - 8
    visible_count = max(1, body_height // row_height)

    start = min(max(0, selected - 4), max(0, len(rows) - visible_count))

    # Draw the full-page card backdrop behind the rows
    draw_card(draw, 12, card_top, 216, card_h, theme)

    for offset, row in enumerate(rows[start : start + visible_count]):
        index = start + offset
        y = card_top + 4 + offset * row_height
        # Compose the row's value string. ``i18n_value_prefix`` lets the
        # controller emit a translatable prefix (e.g. "On") plus a raw
        # user-typed suffix (e.g. ` · "Hello"`); we translate just the
        # prefix and concatenate (plan 037 polish #4).
        if row.i18n_value_prefix:
            value_str = t(row.i18n_value_prefix, lang) + row.value
        else:
            # Translate both label and value. Values are mixed: some are
            # registered option labels ("Dark", "Large", "Hotspot", "saved"),
            # others are dynamic data (printer serial, IP, film count). t()
            # falls back to the source string on a miss, so dynamic data
            # passes through unchanged while option labels pick up i18n.
            value_str = t(row.value, lang)
        draw_settings_row(
            draw,
            y,
            t(row.label, lang),
            value_str,
            row.hint,
            selected=index == selected,
            font=font,
            # Chevron sits in a Latin body-sized font (≈1.4× the row font)
            # so the disclosure affordance reads from arm's length on the
            # 240×240 panel. Must be Latin-only — the CJK fonts used in
            # zh-Hans mode have no glyph for "›" (U+203A) and would tofu.
            marker_font=marker_font,
            theme=theme,
            row_height=row_height,
            is_header=row.is_header,
            header_font=header_font,
        )
        # Hairline between rows (not after last visible row). Suppress the
        # hairline directly below a header row so the divider reads as a
        # group label rather than a sectioned row (plan 037 polish #1).
        if offset < visible_count - 1 and not row.is_header:
            separator_y = y + row_height
            draw_hairline(draw, 16, separator_y, 210, theme)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)

    if bottom_shown:
        # Translate at the render boundary — `bottom_text` is the controller-
        # built English help string (or settings_message toast); the i18n
        # table carries the translations and t() falls back to the source
        # when no entry exists. Translate FIRST, then wrap, so the wrap
        # measurement uses the rendered character widths.
        bottom_text = t(bottom_text, snapshot.language)
        bottom_y = card_bottom + 2
        max_w = 240 - 32
        lines = _wrap_two_lines(draw, bottom_text, font, max_w)
        for i, line in enumerate(lines):
            _text(draw, 16, bottom_y + i * 12, line, font, bottom_color)


# ---------------------------------------------------------------------------
# Adjustments page — dedicated renderer (plan 036 phase 2, Option A)
# ---------------------------------------------------------------------------

# Rows rendered as sliders (label as stored in SettingsRow.label).
# Preset + Save keep the picker-style chevron; Datestamp + Watermark get
# a plain label + On/Off value; all others become slider rows.
_SLIDER_ROW_LABELS: frozenset[str] = frozenset(
    {"Saturation", "Exposure", "Sharpness", "Hue", "Vignette"}
)
_TOGGLE_ROW_LABELS: frozenset[str] = frozenset({"Datestamp", "Watermark"})
_PICKER_ROW_LABELS: frozenset[str] = frozenset({"Preset", "Save current"})

# Slider range per label.  Vignette is [0, 100] (asymmetric); all colour
# axes are [-100, +100] (symmetric).
_SLIDER_RANGE: dict[str, tuple[int, int]] = {
    "Saturation": (-100, 100),
    "Exposure": (-100, 100),
    "Sharpness": (-100, 100),
    "Hue": (-100, 100),
    "Vignette": (0, 100),
}

# Full-width slider layout for the redesigned Adjustments list page.
# LCD card inner width: x=14..226 (212 px usable).
# Label zone  : x=18..100  (82 px) — fits "Saturation", "Sharpness", CJK.
# Slider zone : x=104..184 (80 px).
# Value column: x=188..210 (22 px, right of slider) — fits "+100"/"-100".
# Chevron     : x=216      (always visible on slider/picker rows).
_ADJ_SLIDER_X = 104
_ADJ_SLIDER_W = 80
_ADJ_VALUE_X = 188  # left edge of value column
_ADJ_CHEVRON_X = 216  # chevron anchor (left edge)


def _adjustments(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    """Render the Adjustments settings page — full-width rows, no preview tile.

    Redesigned layout (plan 036 Option A, "iOS Photos Edit" pattern):

    All 9 rows use the full card width.  The preview tile has been removed from
    list mode; live feedback lives in edit mode (192×108 tile).

    Row variants:
    - Slider row (Saturation/Exposure/Sharpness/Hue/Vignette):
        label x=18..100 | slider x=104..184 | value x=188..210 | chevron x=216
        Chevron is always visible (not only when selected) so every slider row
        reads as "KEY1 opens edit mode".
    - Picker row (Preset, Save current): standard draw_settings_row.
    - Toggle row (Datestamp, Watermark): label + On/Off, no slider, no chevron.

    Row height is capped at 17 px so all 9 rows fit at medium font scale.
    """
    rows = snapshot.settings_rows
    lang = snapshot.language
    font = fonts["small"]
    font_scale, row_scale = _scale_for_snapshot(snapshot)
    marker_font = _font(max(1, round(_BASE_FONTS["body"] * font_scale)), prefer_cjk=False)

    if not rows:
        _text(draw, 18, 58, t("No settings available", lang), fonts["body"], theme.label_primary)
        draw_hint_bar(draw, _mode_hints(snapshot), fonts["hint"], theme)
        return

    # Cap row height at 17 px so all 9 rows fit without scrolling at medium scale.
    row_height = min(17, max(1, round(_BASE_ROW_HEIGHT * row_scale)))

    selected = min(snapshot.selected_index, len(rows) - 1)
    selected_row = rows[selected]

    # Toast / help strip (same logic as _settings). Destructive confirms
    # now use the dialog overlay (plan 040), so ``settings_message`` here is
    # informational only ("Saved", "Choose action", etc.) and always renders
    # in the existing accent_yellow help tint.
    toast_message = snapshot.settings_message
    help_text = selected_row.help if selected_row.help else ""
    if toast_message is not None:
        bottom_text = toast_message
        bottom_color = theme.accent_yellow
    elif help_text:
        bottom_text = help_text
        bottom_color = theme.label_secondary
    else:
        bottom_text = ""
        bottom_color = theme.label_secondary
    bottom_shown = bool(bottom_text)

    card_top = STATUS_BAR_H + 2
    card_bottom = (HINT_BAR_Y - 28) if bottom_shown else (HINT_BAR_Y - 4)
    card_h = card_bottom - card_top
    body_height = card_h - 8
    visible_count = max(1, body_height // row_height)
    start = min(max(0, selected - 4), max(0, len(rows) - visible_count))

    draw_card(draw, 12, card_top, 216, card_h, theme)

    for offset, row in enumerate(rows[start : start + visible_count]):
        index = start + offset
        row_y = card_top + 4 + offset * row_height
        is_selected = index == selected
        label_str = t(row.label, lang)
        # Preset row uses a trailing " · edited" badge to mark "modified
        # from preset" (plan 037 polish #6, replaces the cryptic " *"). The
        # composite string ("Black & white · edited") has no i18n entry, so
        # a naive t() falls through to English. Split + translate + re-append
        # so the prefix picks up the locale (Black & white → 黑白) and the
        # " · edited" badge itself is translated independently.
        _EDITED_SUFFIX = " · edited"
        if row.value.endswith(_EDITED_SUFFIX):
            preset_name = row.value[: -len(_EDITED_SUFFIX)]
            value_str = t(preset_name, lang) + " · " + t("edited", lang)
        else:
            value_str = t(row.value, lang)

        if row.label in _SLIDER_ROW_LABELS:
            _draw_adjustments_slider_row(
                draw,
                row_y,
                row_height,
                label_str,
                row.label,
                value_str,
                selected=is_selected,
                font=font,
                marker_font=marker_font,
                theme=theme,
            )
        elif row.label in _TOGGLE_ROW_LABELS:
            _draw_adjustments_toggle_row(
                draw,
                row_y,
                row_height,
                label_str,
                value_str,
                selected=is_selected,
                font=font,
                theme=theme,
            )
        else:
            # Preset, Save current — standard picker row
            draw_settings_row(
                draw,
                row_y,
                label_str,
                value_str,
                row.hint,
                selected=is_selected,
                font=font,
                marker_font=marker_font,
                theme=theme,
                row_height=row_height,
            )

        if offset < visible_count - 1:
            separator_y = row_y + row_height
            draw_hairline(draw, 16, separator_y, 210, theme)

    # Scroll-indicator strip on the right edge of the card when not all rows
    # fit. With 10 rows on Adjustments only 7-8 fit at MEDIUM scale; the
    # bottom rows ("Save current", etc.) would otherwise be permanently
    # below the fold for first-time users (plan 037 polish #2).
    if len(rows) > visible_count:
        _draw_scrollbar(
            draw,
            card_top=card_top,
            card_h=card_h,
            start=start,
            visible_count=visible_count,
            total=len(rows),
            theme=theme,
        )

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)

    if bottom_shown:
        bottom_text = t(bottom_text, snapshot.language)
        bottom_y = card_bottom + 2
        max_w = 240 - 32
        lines = _wrap_two_lines(draw, bottom_text, font, max_w)
        for i, line in enumerate(lines):
            _text(draw, 16, bottom_y + i * 12, line, font, bottom_color)


def _draw_adjustments_slider_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    row_height: int,
    label_str: str,
    label_key: str,
    value_str: str,
    *,
    selected: bool,
    font: Font,
    marker_font: Font,
    theme: Theme,
) -> None:
    """Render an Adjustments list row as label + value + chevron.

    Layout (matches ``_draw_adjustments_toggle_row``):
    - Label zone : x=22..160 (138 px) — fits long axis names and CJK.
    - Value column: right-justified to x=200, in label_secondary.
    - Chevron "›" : x=216 — signals KEY1 opens edit mode.

    The full-width inline mini-slider that previously sat between label
    and value was removed: when the user presses KEY1 the focused
    ``ADJUSTMENT_EDIT`` mode draws a proper-sized slider + live preview
    on its own surface, so duplicating a thumbnail slider in the row
    just added visual noise without communicating the value better
    than the numeric badge already does. ``label_key`` is kept on the
    signature so the caller's tuple-unpacking site stays untouched
    even though we no longer key the slider range off it.
    """
    # ``label_key`` is unused but retained for caller compatibility —
    # see the docstring above.
    del label_key

    if selected:
        # Full-width highlight, mirroring the toggle row treatment.
        draw.rounded_rectangle(
            (14, y, 226, y + row_height - 1),
            radius=10,
            fill=theme.accent_blue,
        )
        label_fill: str = theme.label_inverse
        value_fill: str = theme.label_inverse
        chevron_fill: str = theme.label_inverse
    else:
        label_fill = theme.label_primary
        value_fill = theme.label_secondary
        chevron_fill = theme.label_secondary

    label_max = 138
    _text(draw, 22, y + 3, _fit_text_to_width(draw, label_str, font, label_max), font, label_fill)

    val_w = _text_width(draw, value_str, font)
    _text(draw, 200 - val_w, y + 3, value_str, font, value_fill)

    # Chevron always visible — "KEY1 opens edit mode" affordance.
    # Must use marker_font (Latin-only): the CJK fonts loaded in zh-Hans
    # mode have no glyph for "›" U+203A and would tofu (□).
    _text(draw, _ADJ_CHEVRON_X, y + 3, "›", marker_font, chevron_fill)


def _draw_adjustments_toggle_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    row_height: int,
    label_str: str,
    value_str: str,
    *,
    selected: bool,
    font: Font,
    theme: Theme,
) -> None:
    """Render a Datestamp / Watermark toggle row (no slider, no chevron).

    Label on the left; value on the right in ``accent_green`` when "On",
    ``label_secondary`` when "Off".
    """

    if selected:
        draw.rounded_rectangle(
            (14, y, 226, y + row_height - 1),
            radius=10,
            fill=theme.accent_blue,
        )
        label_fill: str = theme.label_inverse
        value_fill: str = theme.label_inverse
    else:
        label_fill = theme.label_primary
        if value_str.lower() in ("on", "yes", "true"):
            value_fill = theme.accent_green
        else:
            value_fill = theme.label_secondary

    label_max = 94
    _text(draw, 22, y + 3, _fit_text_to_width(draw, label_str, font, label_max), font, label_fill)

    val_w = _text_width(draw, value_str, font)
    _text(draw, 218 - val_w, y + 3, value_str, font, value_fill)


def _adjustment_edit(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    """Render the focused-adjustment-edit mode (plan 036 phase 4).

    Layout (240 × 240):
    - Status bar at top (36 px) — title "Adjustments" + dot + page counter.
    - Card border at (12, 38, 228, 188), radius=10.
    - Preview tile at (16, 42) with size 192 × 108.
    - Axis label at (22, 164) in body font.
    - Value label right-aligned to x=222 in accent_blue.
    - Slider track at (22, 172, width=174, height=8).
    - Range labels: small font, label_secondary, "−100"/"0" left and "+100"/"100" right.
    - Help strip: "Up/Dn ±5 · Left/Right ±25" in label_secondary between card and hint bar.
    - Hint bar: KEY1 OK / KEY2 Cancel / KEY3 Help.
    """
    from instantlink_bridge.imaging.postprocess import (
        AdjustmentProfile,
        render_adjustments_preview,
    )

    lang = snapshot.language
    font_body = fonts["body"]
    font_small = fonts["small"]
    # --- Card ---------------------------------------------------------------
    card_x0, card_y0, card_x1, card_y1 = 12, 38, 228, 188
    draw_card(draw, card_x0, card_y0, card_x1 - card_x0, card_y1 - card_y0, theme)

    edit_key = snapshot.adjustment_edit_key or ""
    current_value = snapshot.adjustment_edit_value
    is_toggle = edit_key in _OVERLAY_TOGGLE_EDIT_KEYS

    # Slider axes use a two-column layout: shrunk preview on the left,
    # vertical slider on the right (joystick UP/DOWN moves the thumb
    # along the same axis as the gesture). Toggle axes keep the
    # original full-width preview because the Off/On pills sit below
    # the preview rather than beside it.
    if is_toggle:
        _ADJ_EDIT_W = 192
        _ADJ_EDIT_H = 108
        tile_x, tile_y = 16, 42
    else:
        # 138 px wide leaves ~58 px of right-column real estate for
        # the value badge + range labels + slider track; 108 px tall
        # matches the toggle layout so the row of On/Off pills (when
        # the edit is for a toggle) still has room without shifting.
        _ADJ_EDIT_W = 138
        _ADJ_EDIT_H = 130
        tile_x, tile_y = 16, 42

    profile = snapshot.adjustments_profile or AdjustmentProfile()
    try:
        preview_img = render_adjustments_preview(profile, size=(_ADJ_EDIT_W, _ADJ_EDIT_H))
        image.paste(preview_img, (tile_x, tile_y))
    except Exception:
        # Preview failure must never crash the renderer. Draw a
        # diagonal cross-hatch + "Preview unavailable" label so the
        # tile area is visibly broken rather than blending into the
        # card (plan 037 polish #7). Log once per process at WARNING
        # so the failure surfaces in journalctl.
        _log_preview_failure_once()
        _draw_preview_unavailable(
            draw,
            tile_x,
            tile_y,
            _ADJ_EDIT_W,
            _ADJ_EDIT_H,
            font_small,
            t("Preview unavailable", lang),
            theme,
        )

    # Thin border around preview
    draw.rounded_rectangle(
        (tile_x, tile_y, tile_x + _ADJ_EDIT_W, tile_y + _ADJ_EDIT_H),
        radius=4,
        outline=theme.separator,
        width=1,
    )

    # The axis name now lives in the top status bar (see
    # ``status_bar_word``) and the current value rides the slider thumb
    # as a chip (see the ``else`` branch below). The card body is free
    # for the preview + the slider — no standalone value badge.

    if is_toggle:
        # --- Off / On pills -------------------------------------------------
        # Two pills centred horizontally beside (or at) where the slider
        # would sit. Working value 0 → Off pill active, 1 → On pill active.
        # Active pill: accent_blue fill, label_inverse text.
        # Inactive pill: surface fill, label_secondary text.
        pill_w = 70
        pill_h = 24
        gap = 8
        total_w = pill_w * 2 + gap
        pills_y = 164
        left_x = (240 - total_w) // 2
        right_x = left_x + pill_w + gap
        off_active = current_value == 0
        on_active = current_value != 0
        for px, label, active in (
            (left_x, t("Off", lang), off_active),
            (right_x, t("On", lang), on_active),
        ):
            fill = theme.accent_blue if active else theme.surface
            text_colour = theme.label_inverse if active else theme.label_secondary
            draw.rounded_rectangle(
                (px, pills_y, px + pill_w, pills_y + pill_h),
                radius=12,
                fill=fill,
                outline=theme.separator,
                width=1,
            )
            label_w = _text_width(draw, label, font_body)
            label_x = px + (pill_w - label_w) // 2
            # Approximate vertical centring for body font: descent ~ 4 px.
            label_y_inner = pills_y + (pill_h - 14) // 2
            _text(draw, label_x, label_y_inner, label, font_body, text_colour)

        help_strip = t("KEY1 commit · KEY2 cancel", lang)
    else:
        # --- Vertical slider track (right column) ---------------------------
        # The joystick edits with UP/DOWN, so the track runs along the
        # same axis: max value at the top, min at the bottom. The thumb
        # is a pill chip carrying the current value (no separate badge).
        symmetric = edit_key != "adjust_vignette"
        col_x0 = 160
        col_w = 60
        track_x = col_x0 + (col_w - 6) // 2  # track_width=6, centred
        # No separate value badge now that the chip rides the thumb;
        # the track can use the full body-card height between the
        # range labels.
        top_label_y = 44
        track_y = 58
        track_h = 116
        bottom_label_y = track_y + track_h + 2
        lo, hi = (-100, 100) if symmetric else (0, 100)
        # Top range label
        top_label = "+100" if symmetric else "100"
        top_w = _text_width(draw, top_label, font_small)
        _text(
            draw,
            col_x0 + (col_w - top_w) // 2,
            top_label_y,
            top_label,
            font_small,
            theme.label_secondary,
        )
        # Slider track with value-on-chip thumb. ``format_int_with_sign``
        # gives "+20" / "-30"; vignette is unsigned so use the plain int.
        val_chip = (
            format_int_with_sign(current_value) if symmetric else str(current_value)
        )
        draw_vertical_slider(
            draw,
            track_x,
            track_y,
            track_h,
            current_value,
            lo,
            hi,
            theme=theme,
            track_width=6,
            thumb_width=32,
            thumb_height=18,
            symmetric=symmetric,
            value_label=val_chip,
            label_font=font_small,
        )
        # Bottom range label
        bottom_label = "−100" if symmetric else "0"
        bot_w = _text_width(draw, bottom_label, font_small)
        _text(
            draw,
            col_x0 + (col_w - bot_w) // 2,
            bottom_label_y,
            bottom_label,
            font_small,
            theme.label_secondary,
        )

        help_strip = t("Up/Dn ±10 · K1 OK · K2/Left Cancel", lang)

    # --- Help strip ---------------------------------------------------------
    help_y = card_y1 + 3
    help_w = _text_width(draw, help_strip, font_small)
    help_x = (240 - help_w) // 2
    _text(draw, help_x, help_y, help_strip, font_small, theme.label_secondary)

    # --- Hint bar -----------------------------------------------------------
    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _pairing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(
        draw, [t("Searching", snapshot.language)], 70, fonts["large"], theme.label_primary
    )
    _text(
        draw,
        18,
        128,
        t("Keep printer awake", snapshot.language),
        fonts["body"],
        theme.label_primary,
    )
    _text(
        draw,
        18,
        150,
        t("Close phone app if it fails", snapshot.language),
        fonts["small"],
        theme.label_secondary,
    )

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _pair_failed(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(draw, [t("Failed", snapshot.language)], 75, fonts["large"], theme.label_primary)
    message = snapshot.message or t("No INSTAX printer found", snapshot.language)
    for index, line in enumerate(_wrap_words(message, 24)[:2]):
        _text(draw, 18, 126 + index * 17, line, fonts["small"], theme.label_primary)
    if len(_wrap_words(message, 24)) < 2:
        _text(
            draw,
            18,
            143,
            t("Turn printer on first", snapshot.language),
            fonts["small"],
            theme.label_secondary,
        )
    _menu_item(
        draw, 162, t("Try again", snapshot.language), selected=True, font=fonts["body"], theme=theme
    )

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _error(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    title, detail, hint = error_copy_for_message(snapshot.message)
    _center_lines(draw, _wrap_words(title, 16)[:2], 50, fonts["large"], theme.label_primary)
    for index, line in enumerate(_wrap_words(detail, 27)[:2]):
        _text(draw, 18, 126 + index * 17, line, fonts["small"], theme.label_primary)
    if hint is not None:
        _text(draw, 18, 165, _ellipsize(hint, 31), fonts["small"], theme.accent_yellow)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


# ---------------------------------------------------------------------------
# Confirmation dialog overlay (plan 040)
# ---------------------------------------------------------------------------

# Centred-card geometry on the 240×240 panel. The card sits over a dimmed
# overlay so the user reads the dialog as a modal, not a swap.
_CONFIRM_CARD_X = 15
_CONFIRM_CARD_Y = 45
_CONFIRM_CARD_W = 210
_CONFIRM_CARD_H = 150
_CONFIRM_BUTTON_H = 32
_CONFIRM_DIM_FILL = (0, 0, 0)
# 40 % black overlay tint applied by darkening the whole RGB frame.
_CONFIRM_DIM_FACTOR = 0.60


def _confirmation_dialog(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    """Render the iOS-style yes/no confirmation dialog overlay (plan 040).

    Layout on 240×240:

    * 40 % black overlay over the existing frame (the renderer already drew
      the previous mode's content via the status bar; we just dim it).
    * Centred card 210×150 at (15, 45), rounded radius 12, theme.surface
      with a 1-px theme.separator border.
    * Title row centred near the top (bold body font).
    * Message wrapped to up to three lines under the title.
    * Two buttons at the bottom: Cancel | Confirm, divided by a 1-px line.
      The focused button gets a tinted background; the confirm label is
      red for destructive flows and blue for constructive flows.

    Initial focus = Cancel; the controller enforces that invariant when it
    opens the dialog so an accidental KEY1 press is always safe.
    """

    lang = snapshot.language
    title = snapshot.confirmation_title or ""
    message = snapshot.confirmation_message or ""
    confirm_label = snapshot.confirmation_confirm_label or "OK"
    destructive = snapshot.confirmation_destructive
    focus = snapshot.confirmation_focus

    # 1. Dim overlay — darken every pixel by 40 % so the existing screen
    # contents read as ambient context, not interactive UI. PIL's plain
    # rectangle with an alpha tuple requires RGBA; the bridge frame is RGB,
    # so blend in-place per-pixel via Image.point (the typed equivalent of
    # Image.eval) which is fast enough at 240×240 for a one-shot mode
    # transition (the render loop short-circuits on unchanged snapshots).
    dimmed = image.point(lambda v: int(v * _CONFIRM_DIM_FACTOR))
    image.paste(dimmed)
    # Reset the draw cursor: PIL's ``ImageDraw.Draw`` holds a reference to
    # the underlying ``Image`` buffer; ``paste`` mutates that buffer in
    # place, so subsequent draw calls land on the dimmed canvas.

    # 2. Card backdrop with rounded corners + 1 px border.
    card_x1 = _CONFIRM_CARD_X + _CONFIRM_CARD_W
    card_y1 = _CONFIRM_CARD_Y + _CONFIRM_CARD_H
    draw.rounded_rectangle(
        (_CONFIRM_CARD_X, _CONFIRM_CARD_Y, card_x1, card_y1),
        radius=12,
        fill=theme.surface,
        outline=theme.separator,
        width=1,
    )

    # 3. Title — translate at the render boundary and centre horizontally.
    title_str = t(title, lang)
    title_font = fonts["body"]
    title_w = _text_width(draw, title_str, title_font)
    title_x = _CONFIRM_CARD_X + (_CONFIRM_CARD_W - title_w) // 2
    title_y = _CONFIRM_CARD_Y + 12
    _text(draw, title_x, title_y, title_str, title_font, theme.label_primary)

    # 4. Message — wrap to up to 3 lines.
    message_str = t(message, lang)
    message_font = fonts["small"]
    message_max_w = _CONFIRM_CARD_W - 24
    message_lines = _wrap_three_lines(draw, message_str, message_font, message_max_w)
    message_top = title_y + _font_height(draw, title_str or "Hg", title_font) + 10
    line_h = _font_height(draw, "Hg", message_font) + 2
    for i, line in enumerate(message_lines):
        line_w = _text_width(draw, line, message_font)
        line_x = _CONFIRM_CARD_X + (_CONFIRM_CARD_W - line_w) // 2
        _text(draw, line_x, message_top + i * line_h, line, message_font, theme.label_secondary)

    # 5. Button row at the bottom of the card.
    button_top = card_y1 - _CONFIRM_BUTTON_H
    button_divider_x = _CONFIRM_CARD_X + _CONFIRM_CARD_W // 2
    cancel_focused = focus == "cancel"
    confirm_focused = focus == "confirm"

    # Focus tints: a subtle 18 %-alpha overlay over the button cell. We
    # approximate by mixing the accent with the surface fill since the
    # frame is RGB-only.
    confirm_accent = theme.accent_destructive if destructive else theme.accent_blue
    if cancel_focused:
        _draw_button_focus_fill(
            draw,
            _CONFIRM_CARD_X,
            button_top,
            button_divider_x,
            card_y1,
            theme.accent_blue,
            theme,
            corner="bottom_left",
        )
    if confirm_focused:
        _draw_button_focus_fill(
            draw,
            button_divider_x,
            button_top,
            card_x1,
            card_y1,
            confirm_accent,
            theme,
            corner="bottom_right",
        )

    # Vertical divider between Cancel and Confirm.
    draw.line(
        (button_divider_x, button_top, button_divider_x, card_y1 - 1),
        fill=theme.separator,
        width=1,
    )
    # Horizontal hairline above the button row.
    draw.line(
        (_CONFIRM_CARD_X, button_top, card_x1, button_top),
        fill=theme.separator,
        width=1,
    )

    # Vertically center each label INSIDE its button cell using PIL's
    # ``anchor='mm'`` (middle-middle). The earlier textbbox math was
    # fragile across PIL versions — different Pillows treat the default
    # anchor differently, and using "Hg" as the reference height left
    # descender-less labels like "Cancel" / "Forget" looking
    # bottom-heavy. ``anchor='mm'`` centres both axes on the (x, y)
    # point unambiguously, so we just pass the geometric centre of each
    # button cell and PIL does the rest.
    button_font = fonts["body"]
    button_cjk = _cjk_font_for(button_font)
    button_center_y = button_top + _CONFIRM_BUTTON_H // 2
    cancel_center_x = _CONFIRM_CARD_X + (_CONFIRM_CARD_W // 4)
    confirm_center_x = button_divider_x + (_CONFIRM_CARD_W // 4)

    cancel_text = t("Cancel", lang)
    cancel_font = button_cjk if button_cjk is not None and _has_cjk(cancel_text) else button_font
    draw.text(
        (cancel_center_x, button_center_y),
        cancel_text,
        font=cancel_font,
        fill=theme.label_primary,
        anchor="mm",
    )

    confirm_text = t(confirm_label, lang)
    confirm_font = (
        button_cjk if button_cjk is not None and _has_cjk(confirm_text) else button_font
    )
    draw.text(
        (confirm_center_x, button_center_y),
        confirm_text,
        font=confirm_font,
        fill=confirm_accent,
        anchor="mm",
    )

    # Hint bar reuses the standard mode-hint dispatch; CONFIRMATION_DIALOG
    # surfaces its own KEY1/KEY2 labels via ``_footer_label_lines``.
    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _help_dialog(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    """Render the read-only help overlay.

    The card carries the row / axis label as a header and the
    setting's help text as up to four wrapped body lines, with a
    "Press any key" hint at the bottom. No buttons — every input
    dismisses (see :py:meth:`BridgeUi._handle_help_dialog_action`),
    matching the iOS info-popup convention.

    Reuses the confirmation dialog's dim factor and card geometry so
    a help overlay opened on top of an Adjustments preview looks
    visually consistent with the confirm overlay used for destructive
    flows on the same surfaces.
    """

    lang = snapshot.language
    title = snapshot.help_dialog_title or ""
    body = snapshot.help_dialog_body or ""

    # 1. Dim the underlying mode's frame so the card reads as the
    # active foreground without losing the context underneath.
    dimmed = image.point(lambda v: int(v * _CONFIRM_DIM_FACTOR))
    image.paste(dimmed)

    # 2. Card backdrop — same geometry as the confirmation dialog.
    card_x0 = _CONFIRM_CARD_X
    card_y0 = _CONFIRM_CARD_Y
    card_x1 = _CONFIRM_CARD_X + _CONFIRM_CARD_W
    card_y1 = _CONFIRM_CARD_Y + _CONFIRM_CARD_H
    draw.rounded_rectangle(
        (card_x0, card_y0, card_x1, card_y1),
        radius=12,
        fill=theme.surface,
        outline=theme.separator,
        width=1,
    )

    # 3. Title (axis / row label) centred.
    title_str = t(title, lang)
    title_font = fonts["body"]
    title_w = _text_width(draw, title_str, title_font)
    title_x = card_x0 + (_CONFIRM_CARD_W - title_w) // 2
    title_y = card_y0 + 12
    _text(draw, title_x, title_y, title_str, title_font, theme.label_primary)

    # 4. Body — wrap to up to four lines so longer help strings fit.
    body_str = t(body, lang)
    body_font = fonts["small"]
    body_max_w = _CONFIRM_CARD_W - 24
    body_lines = _wrap_lines(draw, body_str, body_font, body_max_w, max_lines=4)
    body_top = title_y + _font_height(draw, title_str or "Hg", title_font) + 10
    line_h = _font_height(draw, "Hg", body_font) + 2
    for i, line in enumerate(body_lines):
        line_w = _text_width(draw, line, body_font)
        line_x = card_x0 + (_CONFIRM_CARD_W - line_w) // 2
        _text(draw, line_x, body_top + i * line_h, line, body_font, theme.label_secondary)

    # 5. Footer hint — "Press any key to close".
    hint = t("Press any key", lang)
    hint_font = fonts["small"]
    hint_w = _text_width(draw, hint, hint_font)
    hint_x = card_x0 + (_CONFIRM_CARD_W - hint_w) // 2
    hint_y = card_y1 - 18
    _text(draw, hint_x, hint_y, hint, hint_font, theme.label_secondary)

    # No bottom hint bar — every input dismisses, so the standard
    # KEY1/KEY2/KEY3 legend would be misleading.


def _wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Font,
    max_width: int,
    *,
    max_lines: int,
) -> list[str]:
    """Greedy word-wrap helper used by the help dialog body.

    A small superset of ``_wrap_three_lines`` — callers pick the line
    cap. The last line is suffixed with "…" if the input exceeded the
    budget so the truncation is visible.
    """

    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = current + " " + word
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines:
        lines.append(current)
    # If we ran out of room mid-wrap, mark the truncation. ``current``
    # holds the last accepted chunk; the loop above breaks before
    # pushing it when at the cap.
    if len(lines) == max_lines and current and lines[-1] != current:
        last = lines[-1]
        while last and _text_width(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = (last + "…") if last else "…"
    return lines


def _draw_button_focus_fill(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    accent: str,
    theme: Theme,
    *,
    corner: str,
) -> None:
    """Paint the focused-button background tint inside the card.

    The card has rounded bottom corners (radius 12). The Cancel button
    sits in the bottom-left of the card so its tint must round only at
    its outer bottom-left corner; Confirm mirrors this on the right.
    The inside edges (where the buttons meet at the divider) and the top
    edges (where the buttons meet the message area) stay square so the
    tint fills the button cell cleanly with no corner bleed.
    """

    tint = _blend(accent, theme.surface, 0.18)
    # PIL ``rounded_rectangle.corners`` is (top_left, top_right,
    # bottom_right, bottom_left) per the Pillow docs.
    if corner == "bottom_left":
        corners = (False, False, False, True)
    elif corner == "bottom_right":
        corners = (False, False, True, False)
    else:
        corners = (False, False, False, False)
    draw.rounded_rectangle(
        (x0, y0, x1, y1),
        # Match the card's outer radius so the tint meets the card edge
        # cleanly. See the card render above; both literals stay at 12.
        radius=12,
        fill=tint,
        corners=corners,
    )


def _blend(fg: str, bg: str, alpha: float) -> str:
    """Return ``fg`` over ``bg`` at ``alpha`` (0..1) as a hex colour."""

    fr, fg_g, fb = int(fg[1:3], 16), int(fg[3:5], 16), int(fg[5:7], 16)
    br, bg_g, bb = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
    r = round(alpha * fr + (1 - alpha) * br)
    g = round(alpha * fg_g + (1 - alpha) * bg_g)
    b = round(alpha * fb + (1 - alpha) * bb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _wrap_three_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Font,
    max_width: int,
) -> list[str]:
    """Wrap ``text`` to up to three lines fitting ``max_width``.

    Stacks two passes of :func:`_wrap_two_lines`: wrap once to get line 1 +
    remainder, then wrap the remainder again. Reusing the existing helper
    keeps the CJK/word-boundary logic in one place and adds no new
    measurement code paths.
    """

    if _text_width(draw, text, font) <= max_width:
        return [text]
    head_lines = _wrap_two_lines(draw, text, font, max_width)
    if len(head_lines) < 2:
        return head_lines
    line1 = head_lines[0]
    rest = text[len(line1) :].lstrip(" ")
    if not rest or _text_width(draw, rest, font) <= max_width:
        return [line1, rest] if rest else [line1]
    tail_lines = _wrap_two_lines(draw, rest, font, max_width)
    return [line1, *tail_lines[:2]]


# ---------------------------------------------------------------------------
# Hint data (replaces _footer / _footer_label_lines as the source of truth)
# ---------------------------------------------------------------------------


def _mode_hints(snapshot: UiSnapshot) -> tuple[str, str, str]:
    """Return the per-mode (left, center, right) hint strings, translated to
    the snapshot's active language.

    The translation happens here (rather than at every draw_hint_bar caller)
    so the K1/K2/K3 labels are localised automatically across every body
    renderer; new screens get i18n for free.
    """

    lines = _footer_label_lines(snapshot)
    if not lines:
        return ("", "", "")
    left, center, right = lines[0]
    lang = snapshot.language
    return (t(left, lang), t(center, lang), t(right, lang))


# ---------------------------------------------------------------------------
# Legacy footer helpers — kept because tests import them
# ---------------------------------------------------------------------------


def _footer_label_lines(snapshot: UiSnapshot) -> tuple[tuple[str, str, str], ...]:
    if snapshot.mode is UiMode.BOOTING:
        return (("", "Starting", ""),)
    if snapshot.mode is UiMode.CONFIRMATION_DIALOG:
        # Confirmation dialog (plan 040): LEFT/RIGHT toggle focus between
        # Cancel and Confirm; KEY1 activates the focused button; KEY2 always
        # cancels. The chip labels match those bindings exactly.
        return (("Left/Right", "KEY1 OK", "KEY2 Cancel"),)
    if snapshot.mode is UiMode.ADJUSTMENT_EDIT:
        return (("KEY1 OK", "KEY2 Cancel", "KEY3 Help"),)
    if snapshot.mode is UiMode.SETTINGS:
        return (
            # Three chips, three physical keys (KEY1/KEY2/KEY3 left-to-right
            # on the LCD HAT silkscreen). The joystick handles every nav
            # action by itself — Up/Dn used to live in the left chip but
            # it was a joystick descriptor pretending to be a key shortcut.
            # Now each chip names exactly its physical key's shortcut.
            ("KEY1 OK", "KEY2 Back", "KEY3 Help"),
        )
    if snapshot.mode is UiMode.NEEDS_PAIRING:
        # Chip reads "KEY3 Pair" because short-press KEY3 now also starts
        # pairing (controller routes HELP → _start_pairing in NEEDS_PAIRING).
        # The old "Hold KEY3" text was misleading — short-press was a silent
        # no-op and the hold target was unstated (plan 034 item 2).
        return (("Up/Dn", "KEY1 Select", "KEY3 Pair"),)
    if snapshot.mode is UiMode.PAIR_FAILED:
        return (("KEY1 Retry", "KEY2 Back", "KEY3 Retry"),)
    if snapshot.mode is UiMode.PAIRING:
        return (("", "Scanning", "KEY2 Back"),)
    if snapshot.mode is UiMode.AWAITING_CONFIRM:
        if snapshot.preview_tool == "crop":
            return (("4-way Pan", "KEY1 Print", "KEY2 Cancel"),)
        if snapshot.preview_tool == "rotate":
            return (("Left/Right", "KEY1 Print", "KEY2 Cancel"),)
        return (("Up/Dn Edit", "KEY1 Print", "KEY2 Cancel"),)
    if snapshot.mode is UiMode.PRINTING:
        return (("", "Printing", ""),)
    if snapshot.mode is UiMode.PRINT_COMPLETE:
        if snapshot.paired_printer is not None:
            return (("KEY1 Setting", "Ejecting", "KEY3 Network"),)
        return (("KEY1 Setting", "Ejecting", "Hold KEY3"),)
    if snapshot.paired_printer is not None:
        return (("KEY1 Setting", "KEY2 Refresh", "KEY3 Network"),)
    return (("KEY1 Setting", "KEY2 Refresh", "Hold KEY3"),)


# ---------------------------------------------------------------------------
# Retained private helpers
# ---------------------------------------------------------------------------


def _menu_item(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    *,
    selected: bool,
    font: Font,
    theme: Theme | None = None,
) -> None:
    if theme is None:
        theme = theme_for("light")
    fill = theme.accent_blue if selected else theme.surface
    text_fill = theme.label_inverse if selected else theme.label_secondary
    draw.rounded_rectangle((18, y, 222, y + 24), radius=4, fill=fill)
    prefix = ">" if selected else " "
    _text(draw, 28, y + 5, f"{prefix} {label}", font, text_fill)


def _progress_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    percent: int | None,
    fill: str,
    font: Font,
    theme: Theme | None = None,
) -> None:
    if theme is None:
        theme = theme_for("light")
    if percent is None:
        _text(draw, x, y + 2, "Progress: working...", font, theme.label_secondary)
        return
    bounded = max(0, min(100, percent))
    width = 204
    height = 12
    draw.rounded_rectangle((x, y, x + width, y + height), radius=3, fill=theme.surface)
    if bounded > 0:
        filled = max(4, int(width * bounded / 100))
        draw.rounded_rectangle((x, y, x + filled, y + height), radius=3, fill=fill)
    label = f"{bounded}%"
    label_width = _text_width(draw, label, font)
    _text(draw, x + width - label_width, y + 16, label, font, theme.label_secondary)


def _center_lines(
    draw: ImageDraw.ImageDraw,
    lines: Iterable[str],
    start_y: int,
    font: Font,
    fill: str,
) -> None:
    y = start_y
    for line in lines:
        width = _text_width(draw, line, font)
        _text(draw, 120 - width // 2, y, line, font, fill)
        y += 26


# Fallback CJK-sibling lookup for fonts that don't accept arbitrary
# attrs (`ImageFont.load_default()` is slot-restricted and rejects
# attribute assignment). Keyed by ``id(primary_font)``. Since `_font` is
# `lru_cache`d, the same primary objects are returned across renders and
# this dict is naturally bounded to the same ~5-entry slot ladder; no
# eviction logic needed.
_CJK_SIBLING_BY_ID: dict[int, Font] = {}


# Pre-compiled CJK detector. Covers CJK Unified Ideographs
# (U+4E00–U+9FFF), Extension-A (U+3400–U+4DBF), and CJK Compatibility
# Ideographs (U+F900–U+FAFF). Hiragana/Katakana aren't in WQY coverage
# so we don't bother — the Chinese translations are pure Han.
#
# Compiled once at import time; the C-level scan via `re.search` is much
# faster than the previous per-character Python loop with chained
# comparisons. `_has_cjk` runs for every drawn string, including the hot
# `_text_width` path used during row layout.
_CJK_RE = re.compile("[一-鿿㐀-䶿豈-﫿]")


def _has_cjk(text: str) -> bool:
    """Return True if ``text`` contains any CJK ideograph."""

    return _CJK_RE.search(text) is not None


def _cjk_font_for(font: Font) -> Font | None:
    """Return the CJK sibling font registered for ``font``, or None."""

    sibling = getattr(font, "cjk_sibling", None)
    if sibling is not None:
        return sibling  # type: ignore[no-any-return]
    return _CJK_SIBLING_BY_ID.get(id(font))


def _text(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: Font,
    fill: str,
) -> None:
    """Draw ``text`` at ``(x, y)``.

    Auto-switches to the font's CJK sibling when the string contains any
    Han characters so picker options like "中文" render correctly even in
    English mode (DejaVu has no CJK glyphs). Pure-Latin strings keep the
    primary font for crisper Latin rendering.
    """

    if _has_cjk(text):
        cjk_font = _cjk_font_for(font)
        if cjk_font is not None:
            draw.text((x, y), text, fill=fill, font=cjk_font)
            return
    draw.text((x, y), text, fill=fill, font=font)


def _font_height(draw: ImageDraw.ImageDraw, text: str, font: Font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[3] - bbox[1])


def _mode_chrome(mode: UiMode) -> tuple[str, str]:
    if mode is UiMode.SETTINGS:
        return BLUE, "Settings"
    if mode is UiMode.READY:
        return GREEN, "Ready"
    if mode is UiMode.VALIDATION:
        return YELLOW, "Waiting"
    if mode is UiMode.NO_FILM:
        return RED, "Attention"
    if mode is UiMode.PRINTER_SEARCHING:
        return BLUE, "Searching"
    if mode is UiMode.PRINTER_OFFLINE:
        return YELLOW, "Attention"
    if mode is UiMode.IMAGE_RECEIVED:
        return BLUE, "Received"
    if mode is UiMode.AWAITING_CONFIRM:
        return BLUE, "Preview"
    if mode is UiMode.PRINTING:
        return BLUE, "Printing"
    if mode is UiMode.PRINT_COMPLETE:
        return GREEN, "Ejecting"
    if mode is UiMode.PAIRING:
        return YELLOW, "Finding"
    if mode in {UiMode.PAIR_FAILED, UiMode.ERROR}:
        return RED, "Error" if mode is UiMode.ERROR else "Attention"
    if mode is UiMode.BOOTING:
        return YELLOW, "Starting"
    return BLUE, "Printer setup"


def _snapshot_chrome(snapshot: UiSnapshot) -> tuple[str, str]:
    if snapshot.mode is UiMode.SETTINGS:
        return BLUE, snapshot.settings_title
    if snapshot.mode is UiMode.READY and not can_accept_images(snapshot):
        return YELLOW, "Waiting"
    return _mode_chrome(snapshot.mode)


# DejaVu / Arial have no CJK glyphs, so for Chinese we prefer Noto Sans CJK
# (most Pi OS images bundle it via fonts-noto-cjk) and fall back to other
# common CJK families before resigning to the Latin-only fonts that would
# render Chinese as tofu boxes.
_LATIN_FONT_PATHS: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)

_CJK_FONT_PATHS: tuple[str, ...] = (
    # Pi OS — what production runs:
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    # macOS dev — Apple's location varies between Intel/Apple-Silicon and
    # macOS release. Try the common ones so render previews work locally.
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
)


@lru_cache(maxsize=32)
def _font(size: int, prefer_cjk: bool = False) -> Font:
    """Return a TrueType font for ``size``, preferring CJK or Latin first.

    Cached per ``(size, prefer_cjk)``. Without the cache, every
    ``render_snapshot`` call re-opened the TTF from disk for every slot
    (small/body/title/large/hint × Latin + CJK = 10 opens), and at the
    16 fps breath rate that became ~160 disk reads/second on the Pi's
    SD card. The bridge ships fewer than ten font slots even in the
    worst case (3 font-size scales × {Latin, CJK} × {small, body,
    title, large, hint}), so a 32-entry LRU is comfortably bounded.
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


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: Font) -> int:
    """Return the rendered width of ``text`` in pixels.

    Mirrors the CJK-fallback path in :func:`_text` so layout maths
    (centring, fit-to-width) stay accurate when the string contains
    Han characters and the actual draw call switches to the sibling.
    """

    if _has_cjk(text):
        cjk_font = _cjk_font_for(font)
        if cjk_font is not None:
            font = cjk_font
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
    return int(right - left)


def _wrap_two_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Font,
    max_width: int,
) -> list[str]:
    """Wrap ``text`` into at most two lines, each fitting ``max_width``.

    Strategy:

    * Whole string fits → single line.
    * Has spaces → greedy word-boundary wrap.
    * Has spaces but the first "word" alone overflows, OR has no spaces
      (CJK) → fall through to character-boundary wrap so Chinese strings
      get two lines too. Without this, Chinese help text always stayed
      on one ellipsised line because ``split(" ")`` produced a single
      "word".

    Remaining text after line 2 is ellipsised by ``_fit_text_to_width``.
    """

    if _text_width(draw, text, font) <= max_width:
        return [text]

    # Word-boundary wrap first.
    if " " in text:
        words = text.split(" ")
        line1_words: list[str] = []
        i = 0
        while i < len(words):
            candidate = " ".join([*line1_words, words[i]])
            if _text_width(draw, candidate, font) > max_width:
                break
            line1_words.append(words[i])
            i += 1
        if line1_words:
            line1 = " ".join(line1_words)
            rest = " ".join(words[i:])
            line2 = _fit_text_to_width(draw, rest, font, max_width)
            return [line1, line2]
        # First word alone overflows — fall through to char wrap below.

    # Character-boundary wrap (CJK or overflowing single word).
    line1_chars: list[str] = []
    j = 0
    while j < len(text):
        candidate = "".join([*line1_chars, text[j]])
        if _text_width(draw, candidate, font) > max_width:
            break
        line1_chars.append(text[j])
        j += 1
    if not line1_chars:
        return [_fit_text_to_width(draw, text, font, max_width)]
    line1 = "".join(line1_chars)
    rest = text[j:]
    line2 = _fit_text_to_width(draw, rest, font, max_width)
    return [line1, line2]


def _fit_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Font,
    max_width: int,
) -> str:
    """Return text shortened enough to fit the requested pixel width."""

    if max_width <= 0:
        return ""
    if _text_width(draw, text, font) <= max_width:
        return text
    marker = "."
    marker_width = _text_width(draw, marker, font)
    if marker_width > max_width:
        return ""
    fitted = text
    while fitted and _text_width(draw, f"{fitted}{marker}", font) > max_width:
        fitted = fitted[:-1]
    return f"{fitted}{marker}" if fitted else marker


def _physical_control_text(text: str) -> str:
    """Normalize visible control labels to the physical hardware names."""

    if text.startswith("Crop:"):
        text = text.replace("joystick", "4-way pan").replace("Joy", "4-way pan")
    replacements = (
        ("R/K1", "Right/KEY1"),
        ("L/K2", "Left/KEY2"),
        ("K1/R", "KEY1/Right"),
        ("K1", "KEY1"),
        ("K2", "KEY2"),
        ("K3", "KEY3"),
        ("joystick", "Up/Dn"),
        ("Joy", "Up/Dn"),
    )
    normalized = text
    for old, new in replacements:
        normalized = normalized.replace(old, new)
    return normalized


def _sentence_case(text: str) -> str:
    """Convert short UI headings to sentence case without touching acronyms."""

    words = text.split()
    if len(words) <= 1:
        return text
    cased = [words[0]]
    cased.extend(word if word.isupper() else word.lower() for word in words[1:])
    return " ".join(cased)


def _draw_scrollbar(
    draw: ImageDraw.ImageDraw,
    *,
    card_top: int,
    card_h: int,
    start: int,
    visible_count: int,
    total: int,
    theme: Theme,
) -> None:
    """Draw a 2 px scrollbar on the right edge of the rows card.

    Used on Adjustments + Settings when rows overflow the visible area.
    The thumb height maps the visible range proportional to the total row
    count with a 12 px floor so it stays clickable / visible (plan 037
    polish #2). The track sits at ``x=224`` so it hugs the card's inner
    right edge (the standard card spans x=12..228 with a 2 px inset).
    """

    if total <= visible_count:
        return
    track_x = 224
    track_y0 = card_top + 4
    track_y1 = card_top + card_h - 4
    track_h = track_y1 - track_y0
    if track_h <= 0:
        return
    thumb_h = max(12, round(track_h * visible_count / total))
    # Clamp the thumb so it never overshoots the track.
    max_top_offset = track_h - thumb_h
    if total - visible_count <= 0:
        thumb_top = track_y0
    else:
        thumb_top = track_y0 + round(max_top_offset * start / (total - visible_count))
    thumb_bottom = thumb_top + thumb_h
    draw.rectangle(
        (track_x, thumb_top, track_x + 2, thumb_bottom),
        fill=theme.label_secondary,
    )


def _settings_row_kind(hint: str) -> str:
    hint_lower = hint.lower()
    if "choose" in hint_lower or "set" in hint_lower:
        return "choose"
    if "change" in hint_lower:
        return "change"
    if "run" in hint_lower:
        return "run"
    if "info" in hint_lower:
        return "info"
    if "open" in hint_lower:
        return "open"
    return "plain"


def _settings_row_marker(kind: str, selected: bool) -> tuple[str, str]:
    """Pick the trailing affordance glyph for a settings row.

    Matches iOS Settings vocabulary: the chevron is the "navigates to a
    new view" affordance — only sub-page openers get it. Pickers, actions,
    and toggles bounce the user back to the same screen and so do not get
    a chevron (plan 037 polish #5).

    The returned colour is unused (the draw site picks the theme-aware
    secondary/inverse tint to match label colour); it's kept in the tuple
    only to preserve the call signature.
    """

    if kind == "open":
        # U+203A "›" (single right-pointing angle quotation mark) is a
        # proper narrow chevron — matches iOS' grouped-list disclosure
        # affordance. We draw it in `fonts["body"]` at the call site so
        # it sits visibly larger than the row label, mirroring iOS where
        # the chevron is heavier than the surrounding text.
        return "›", ""
    # All other kinds (choose, change, run, info, plain) get no chevron.
    return "", ""


def _ready_ftp_line(snapshot: UiSnapshot) -> str:
    """Return the FTP address line for the READY screen body."""

    if snapshot.hotspot_host is not None:
        return f"Bridge Wi-Fi  {snapshot.hotspot_host}"
    if snapshot.wifi_host is not None:
        return f"Same Wi-Fi  {snapshot.wifi_host}"
    if snapshot.camera_transport_message is not None:
        return _ellipsize(snapshot.camera_transport_message, 34)
    return "FTP: no address"


# ---------------------------------------------------------------------------
# Public utility functions (used by controller + tests)
# ---------------------------------------------------------------------------


def film_status_text(snapshot: UiSnapshot) -> str:
    """Return the user-facing film counter line."""

    if snapshot.film_remaining is None:
        if snapshot.printer_status_message == "Checking printer":
            return "Film: checking..."
        return "Film: unknown"
    if snapshot.film_remaining <= 0 and snapshot.allow_print_without_film:
        return f"Film: {snapshot.film_remaining}/{snapshot.film_capacity} test"
    return f"Film: {snapshot.film_remaining}/{snapshot.film_capacity}"


def printer_detail_text(snapshot: UiSnapshot) -> str | None:
    """Return a compact second printer status line."""

    if snapshot.printer_battery is not None:
        return f"Printer battery: {snapshot.printer_battery}%{battery_life_suffix(snapshot)}"
    return snapshot.printer_status_message


def battery_life_suffix(snapshot: UiSnapshot) -> str:
    """Return the charge-state / battery-life clause appended after a battery percentage.

    Shows ``charging`` while on charge, the smoothed time-remaining estimate while discharging,
    or an empty string when no estimate is available yet.
    """

    if snapshot.printer_is_charging:
        return " charging"
    minutes = snapshot.printer_battery_minutes_remaining
    if minutes is None:
        return ""
    return f"  {format_battery_life(minutes)} left"


def format_battery_life(minutes: int) -> str:
    """Format a minutes-remaining estimate as a compact ``Hh Mm`` / ``Mm`` string."""

    minutes = max(0, minutes)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def printer_compact_status_text(snapshot: UiSnapshot) -> str:
    """Return film and battery in one line for the small LCD."""

    if snapshot.printer_battery is None:
        return film_status_text(snapshot)
    return (
        f"{film_status_text(snapshot)}  Printer battery: "
        f"{snapshot.printer_battery}%{battery_life_suffix(snapshot)}"
    )


def top_bar_status_text(snapshot: UiSnapshot) -> str | None:
    """Return the compact live status shown only in the title bar."""

    if snapshot.mode is UiMode.SETTINGS:
        return None
    if snapshot.mode is UiMode.BOOTING:
        return "Starting services"
    if snapshot.mode is UiMode.PAIRING:
        return "Scanning for INSTAX-*"
    if snapshot.mode is UiMode.PAIR_FAILED:
        return _ellipsize(snapshot.message or "No printer found", 31)
    if snapshot.mode is UiMode.ERROR:
        return _ellipsize(snapshot.message or "Check logs", 31)
    if snapshot.mode is UiMode.PRINTING:
        return _ellipsize(snapshot.print_detail or "Printing", 31)
    if snapshot.mode is UiMode.AWAITING_CONFIRM:
        return _preview_top_status_text(snapshot)

    parts = [camera_top_status_text(snapshot), printer_top_status_text(snapshot)]
    compact = [part for part in parts if part]
    if not compact:
        return None
    return " | ".join(compact)


def camera_top_status_text(snapshot: UiSnapshot) -> str:
    """Return a title-bar sized FTP receive status."""

    if snapshot.camera_receive_ready:
        return ftp_mode_label(snapshot)
    if snapshot.camera_status_message is not None:
        return _cause_text(snapshot.camera_status_message)
    if snapshot.hotspot_host is not None:
        return "Bridge Wi-Fi starting"
    if snapshot.wifi_host is not None:
        return "Same Wi-Fi adv"
    if snapshot.usb_connected:
        return "USB IP"
    return "No FTP Wi-Fi"


def printer_top_status_text(snapshot: UiSnapshot) -> str:
    """Return a title-bar sized printer, film, and battery status."""

    if snapshot.paired_printer is None:
        return "No printer"
    if snapshot.mode is UiMode.PRINTER_SEARCHING:
        message = snapshot.printer_status_message
        if message is not None and message != "Looking for printer":
            return _ellipsize(message, 24)
        return "Printer searching"
    if snapshot.mode is UiMode.PRINTER_OFFLINE:
        if snapshot.printer_status_message == "Hold K3 to re-pair":
            return "Re-pair printer"
        return "Printer offline"
    if snapshot.film_remaining is None:
        return _ellipsize(snapshot.printer_status_message or "Printer checking", 24)
    if snapshot.film_remaining <= 0 and not snapshot.allow_print_without_film:
        return "No film"

    film = (
        f"{_printer_model_short_text(snapshot)} {snapshot.film_remaining}/{snapshot.film_capacity}"
    )
    if snapshot.film_remaining <= 0 and snapshot.allow_print_without_film:
        film = f"{film} test"
    if snapshot.printer_battery is not None:
        return f"{film} {snapshot.printer_battery}%{top_bar_battery_state_text(snapshot)}"
    return film


def top_bar_battery_state_text(snapshot: UiSnapshot) -> str:
    """Return the tiny charge-state marker for the top-bar battery percentage.

    A ``+`` flags charging; the discharge time-remaining estimate is *not*
    included here — it lives on the READY body via :func:`printer_battery_life_text`
    so the top bar stays minimal at 240 px.
    """

    if snapshot.printer_is_charging:
        return "+"
    return ""


def _preview_top_status_text(snapshot: UiSnapshot) -> str:
    if snapshot.print_title is not None:
        return _ellipsize(snapshot.print_title, 31)
    return printer_top_status_text(snapshot)


def _printer_model_short_text(snapshot: UiSnapshot) -> str:
    paired_model = snapshot.paired_printer.model if snapshot.paired_printer is not None else None
    model = snapshot.printer_model or paired_model
    if model is None:
        return "Film"
    labels = {
        PrinterModel.MINI: "Mini",
        PrinterModel.MINI_LINK3: "Mini3",
        PrinterModel.SQUARE: "Sq",
        PrinterModel.WIDE: "Wide",
    }
    return labels.get(model, "Film")


def _status_bar_printer_name(snapshot: UiSnapshot) -> str:
    """Return a friendly printer name for the status bar.

    Prefers a model-derived name (e.g. "Instax Link Square") over the raw BLE
    identifier.  Falls back to the raw BLE name when the model is unknown, and
    to "No printer" when no printer is paired.
    """

    if snapshot.paired_printer is None:
        return "No printer"
    model_names = {
        PrinterModel.MINI: "Instax Link Mini",
        PrinterModel.MINI_LINK3: "Instax Link Mini 3",
        PrinterModel.SQUARE: "Instax Link Square",
        PrinterModel.WIDE: "Instax Link Wide",
    }
    if snapshot.paired_printer.model is not None:
        name = model_names.get(snapshot.paired_printer.model)
        if name is not None:
            return name
    # Never leak the raw BLE name (INSTAX-XXXXXXXX) — it contains hardware
    # vocabulary the user should not have to decode (plan 034 item 7).
    return "Instax printer"


def _status_bar_printer_chip(snapshot: UiSnapshot) -> str | None:
    """Return the compact film/battery chip for the right side of the status bar.

    Shows nothing when no printer is selected or film status is unknown. Shows
    film count and optional battery % when available; the time-remaining
    estimate lives on the READY body so the top chip stays compact.
    """

    if snapshot.paired_printer is None:
        return None
    if snapshot.film_remaining is None:
        return None
    film = f"{snapshot.film_remaining}/{snapshot.film_capacity}"
    if snapshot.printer_battery is not None:
        charging = "+" if snapshot.printer_is_charging else ""
        return f"{film}  {snapshot.printer_battery}%{charging}"
    return film


def printer_battery_life_text(snapshot: UiSnapshot) -> str | None:
    """Return the body-line printer battery life estimate (e.g. "4h32m left").

    Returns ``None`` when the printer is charging or no minutes-remaining
    estimate is available. Lives in the body so the top status bar stays
    minimal; both the LCD READY screen and the future Mac/headless views can
    consume this same string.
    """

    if snapshot.paired_printer is None:
        return None
    if snapshot.printer_is_charging:
        return None
    minutes = snapshot.printer_battery_minutes_remaining
    if minutes is None:
        return None
    return f"{format_battery_life(minutes)} left"


def bridge_power_header_text(snapshot: UiSnapshot) -> str | None:
    """Return the tiny bridge-power text for the title bar."""

    if snapshot.bridge_battery_percent is not None:
        if snapshot.bridge_power_alert in {"warning", "critical"}:
            return f"Bridge low {snapshot.bridge_battery_percent}%"
        return f"Bridge {snapshot.bridge_battery_percent}%"
    return None


def camera_link_ready(snapshot: UiSnapshot) -> bool:
    """Return whether an FTP receive path is visible."""

    return snapshot.camera_receive_ready


def printer_ready(snapshot: UiSnapshot) -> bool:
    """Return whether the selected printer has a current usable status."""

    if snapshot.mode in {
        UiMode.BOOTING,
        UiMode.NEEDS_PAIRING,
        UiMode.PAIRING,
        UiMode.PRINTER_SEARCHING,
        UiMode.PRINTER_OFFLINE,
        UiMode.NO_FILM,
        UiMode.PAIR_FAILED,
        UiMode.ERROR,
    }:
        return False
    return (
        snapshot.printer_status_fresh
        and snapshot.paired_printer is not None
        and snapshot.film_remaining is not None
        and (snapshot.film_remaining > 0 or snapshot.allow_print_without_film)
    )


def can_accept_images(snapshot: UiSnapshot) -> bool:
    """Return whether FTP receive and printer are healthy enough to accept images."""

    return camera_link_ready(snapshot) and printer_ready(snapshot)


def camera_link_text(snapshot: UiSnapshot) -> str:
    """Return the validation line for FTP receive state."""

    if snapshot.camera_receive_ready and snapshot.camera_transport_message is not None:
        return f"FTP: {_ellipsize(snapshot.camera_transport_message, 26)}"
    if snapshot.camera_receive_ready:
        return f"FTP: {ftp_mode_label(snapshot)} ready"
    if snapshot.camera_status_message is not None:
        return f"FTP: {_ellipsize(snapshot.camera_status_message, 26)}"
    if snapshot.hotspot_host is not None:
        return f"FTP: Bridge {snapshot.hotspot_host}"
    if snapshot.wifi_host is not None:
        return f"FTP: Same Wi-Fi adv {snapshot.wifi_host}"
    if snapshot.camera_connected:
        return "FTP: link not ready"
    return "FTP: no FTP Wi-Fi"


def printer_readiness_text(snapshot: UiSnapshot) -> str:
    """Return the validation line for printer readiness."""

    if snapshot.paired_printer is None:
        return "Printer: not selected"
    if snapshot.mode is UiMode.PRINTER_OFFLINE:
        return "Printer: offline"
    if snapshot.mode is UiMode.PRINTER_SEARCHING:
        return "Printer: searching"
    if snapshot.film_remaining is None:
        return "Printer: checking film"
    if snapshot.film_remaining <= 0:
        if snapshot.allow_print_without_film:
            return f"Printer: test mode, Film {snapshot.film_remaining}/{snapshot.film_capacity}"
        return "Printer: no film"
    return f"Printer: ready, Film {snapshot.film_remaining}/{snapshot.film_capacity}"


def readiness_cause_texts(snapshot: UiSnapshot) -> list[str]:
    """Return short LCD-safe causes blocking end-to-end readiness."""

    causes: list[str] = []
    if not camera_link_ready(snapshot):
        causes.append(_cause_text(snapshot.camera_status_message or "Choose FTP Wi-Fi"))
    if snapshot.paired_printer is None:
        causes.append("Find printer")
    elif snapshot.mode is UiMode.PRINTER_OFFLINE:
        causes.append(_cause_text(snapshot.printer_status_message or "Turn printer on"))
    elif snapshot.mode is UiMode.PRINTER_SEARCHING:
        causes.append(_cause_text(snapshot.printer_status_message or "Wait for printer"))
    elif snapshot.film_remaining is None:
        causes.append("Wait for printer status")
    elif snapshot.film_remaining <= 0 and not snapshot.allow_print_without_film:
        causes.append("Replace film pack")
    return causes


def printer_model_text(snapshot: UiSnapshot) -> str:
    """Return the user-facing printer type line."""

    paired_model = snapshot.paired_printer.model if snapshot.paired_printer is not None else None
    model = snapshot.printer_model or paired_model
    if model is None:
        return "Type: detecting"
    labels = {
        PrinterModel.MINI: "Mini",
        PrinterModel.MINI_LINK3: "Mini Link 3",
        PrinterModel.SQUARE: "Square",
        PrinterModel.WIDE: "Wide",
    }
    return f"Type: {labels[model]}"


def preview_state_text(snapshot: UiSnapshot) -> str:
    """Return compact edit state for the preview screen."""

    tool = snapshot.preview_tool.capitalize()
    return (
        f"{tool}  Zoom {snapshot.preview_zoom:.2g}x  Rot {snapshot.preview_rotation_degrees % 360}"
    )


def error_copy_for_message(message: str | None) -> tuple[str, str, str | None]:
    """Return LCD-sized title, detail, and recovery hint for an error message."""

    if message is None:
        return "Bridge error", "Check logs", None
    normalized = message.lower()
    if "pair printer first" in normalized or "select printer first" in normalized:
        return "No printer selected", "Open Printer settings", "Turn printer on first"
    if "printer offline" in normalized:
        return "Printer offline", "Turn printer on", "Keep it awake near bridge"
    if "printer type unknown" in normalized:
        return "Printer type unknown", "Set Printer type", "Settings > Printer"
    if "printer timed out" in normalized:
        return "Printer timed out", "Keep printer awake", "Try again"
    if "battery low" in normalized or "battery too low" in normalized:
        return "Printer battery low", "Charge printer first", "Retry after charge"
    if "cover open" in normalized or "cover is open" in normalized:
        return "Cover open", "Close printer cover", "Retry when latched"
    if "printer busy" in normalized or "printer is busy" in normalized:
        return "Printer busy", "Wait for Instax", "Retry in a moment"
    if "no film" in normalized:
        return "No film left", "Replace film pack", "No-film test in Settings"
    if "image too large" in normalized:
        return "Image too large", "Use smaller JPEG/HIF", "Try lower quality"
    if "image timed out" in normalized:
        return "Image timed out", "RAW/HIF conversion took too long", "Try JPEG"
    if "image unsupported" in normalized:
        return "Image unsupported", "Use JPG, HIF, or ARW", "Check file type"
    if "preview failed" in normalized:
        return "Preview failed", "Image could not be prepared", "Cancel and retry"
    return "Bridge error", message, None


def ftp_mode_label(snapshot: UiSnapshot) -> str:
    """Return the current user-facing FTP receive mode."""

    if snapshot.camera_transport_message is not None:
        if snapshot.camera_transport_message.startswith(("Admin USB", "USB IP")):
            return "USB IP"
        if snapshot.camera_transport_message.startswith("Bridge"):
            return "Bridge Wi-Fi"
        if snapshot.camera_transport_message.startswith("Same Wi-Fi"):
            return "Same Wi-Fi adv"
    if snapshot.hotspot_host is not None:
        return "Bridge Wi-Fi"
    if snapshot.wifi_host is not None:
        return "Same Wi-Fi adv"
    return "No FTP Wi-Fi"


def active_ftp_status_text(snapshot: UiSnapshot) -> str:
    """Return the most useful current FTP address or blocking state."""

    if snapshot.camera_transport_message is not None:
        return snapshot.camera_transport_message
    if snapshot.hotspot_host is not None:
        return f"Bridge FTP {snapshot.hotspot_host}"
    if snapshot.wifi_host is not None:
        return f"Same Wi-Fi adv {snapshot.wifi_host}"
    if snapshot.preferred_wifi_host is not None:
        return f"Same Wi-Fi adv prefer {snapshot.preferred_wifi_host}"
    if snapshot.usb_connected:
        return "USB IP connected"
    return "No FTP Wi-Fi"


def ftp_mode_hint_text(snapshot: UiSnapshot) -> str:
    """Return one short hint for the non-active receive modes."""

    if snapshot.hotspot_host is not None:
        return "Same Wi-Fi adv in Advanced"
    if snapshot.wifi_host is not None:
        return "Bridge Wi-Fi in Settings"
    if snapshot.usb_connected:
        return "USB IP in Network"
    return "Open Upload FTP setup"


def usb_ftp_status_text(snapshot: UiSnapshot) -> str:
    """Return the user-facing USB admin link status."""

    if (
        snapshot.camera_transport_message is not None
        and snapshot.camera_transport_message.startswith(("Admin USB", "USB IP"))
    ):
        return snapshot.camera_transport_message.replace("Admin USB", "USB IP")
    if snapshot.camera_receive_ready and snapshot.usb_connected:
        return f"USB IP {snapshot.ftp_host}"
    if snapshot.usb_connected:
        return "USB IP connected"
    return "USB IP off"


def wifi_ftp_status_text(snapshot: UiSnapshot) -> str:
    """Return the user-facing Wi-Fi FTP address line."""

    return home_wifi_ftp_status_text(snapshot)


def hotspot_ftp_status_text(snapshot: UiSnapshot) -> str:
    """Return the user-facing bridge hotspot FTP address line."""

    if snapshot.hotspot_host is not None:
        return f"Bridge FTP {snapshot.hotspot_host}"
    return f"Bridge Wi-Fi off {snapshot.hotspot_ftp_host}"


def home_wifi_ftp_status_text(snapshot: UiSnapshot) -> str:
    """Return the user-facing home Wi-Fi FTP address line."""

    if snapshot.wifi_host is not None:
        return f"Same Wi-Fi adv {snapshot.wifi_host}"
    if snapshot.preferred_wifi_host is not None:
        return f"Same Wi-Fi adv prefer {snapshot.preferred_wifi_host}"
    return "Same Wi-Fi adv off"


def wifi_preference_mismatch(snapshot: UiSnapshot) -> bool:
    """Return whether Wi-Fi is up but not at the configured preferred address."""

    return (
        snapshot.wifi_host is not None
        and snapshot.preferred_wifi_host is not None
        and snapshot.wifi_host != snapshot.preferred_wifi_host
    )


def _ellipsize(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1]}."


def _cause_text(text: str) -> str:
    if text.lower() in {"no receive mode", "no camera wi-fi", "no ftp wi-fi"}:
        return "Choose FTP Wi-Fi"
    if text.lower() in {"peer subnet conflict", "same-wifi subnet conflict"}:
        return "Wi-Fi subnet conflicts"
    return _ellipsize(text, 31)


def _wrap_words(text: str, max_chars: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word[:max_chars]
    if current:
        lines.append(current)
    return lines
