"""Tests for instantlink_bridge.imaging.postprocess (phases 3 and 4)."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from instantlink_bridge.imaging.postprocess import AdjustmentProfile, apply_adjustments


def _make_rgb(
    color: tuple[int, int, int] = (200, 100, 50),
    size: tuple[int, int] = (32, 32),
) -> Image.Image:
    return Image.new("RGB", size, color)


def _checkerboard(size: int = 32) -> Image.Image:
    """2-tile checkerboard: alternating black/white tiles."""
    img = Image.new("RGB", (size, size), (0, 0, 0))
    half = size // 2
    for x in range(half, size):
        for y in range(0, half):
            img.putpixel((x, y), (255, 255, 255))
    for x in range(0, half):
        for y in range(half, size):
            img.putpixel((x, y), (255, 255, 255))
    return img


def _png_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Identity / fast-path tests
# ---------------------------------------------------------------------------


def test_identity_profile_returns_same_object() -> None:
    """Fast path: a default AdjustmentProfile must return the exact same object."""
    img = _make_rgb()
    result = apply_adjustments(img, AdjustmentProfile())
    assert result is img


def test_apply_adjustments_preserves_size_and_mode() -> None:
    """Size and mode must be unchanged after any adjustment."""
    img = _make_rgb(size=(100, 75))
    for profile in [
        AdjustmentProfile(),
        AdjustmentProfile(saturation=2.0),
        AdjustmentProfile(exposure=2.0),
        AdjustmentProfile(sharpness=2.0),
        AdjustmentProfile(hue=90),
    ]:
        result = apply_adjustments(img, profile)
        assert result.size == img.size
        assert result.mode == img.mode


# ---------------------------------------------------------------------------
# Saturation axis
# ---------------------------------------------------------------------------


def test_saturation_boost_moves_channels_away_from_mean() -> None:
    """saturation=2.0 on a non-grey image pushes channels away from the mean."""
    img = _make_rgb((200, 100, 50))  # clearly non-grey
    result = apply_adjustments(img, AdjustmentProfile(saturation=2.0))

    orig_px = img.getpixel((0, 0))
    new_px = result.getpixel((0, 0))
    orig_mean = sum(orig_px) / 3
    new_mean = sum(new_px) / 3

    # The dominant channel (R=200) should be even further from the mean.
    orig_r_dist = abs(orig_px[0] - orig_mean)
    new_r_dist = abs(new_px[0] - new_mean)
    assert new_r_dist > orig_r_dist, f"R channel should be further from mean: {orig_px} → {new_px}"


def test_saturation_zero_produces_greyscale() -> None:
    """saturation=0.0 desaturates the image to greyscale."""
    img = _make_rgb((200, 100, 50))
    result = apply_adjustments(img, AdjustmentProfile(saturation=0.0))
    r, g, b = result.getpixel((0, 0))
    # All channels equal (greyscale), within rounding.
    assert abs(r - g) <= 1 and abs(g - b) <= 1, f"Expected greyscale but got ({r}, {g}, {b})"


# ---------------------------------------------------------------------------
# Exposure axis
# ---------------------------------------------------------------------------


def test_exposure_boost_brightens_all_channels() -> None:
    """exposure=2.0 brightens every channel (capped at 255)."""
    img = _make_rgb((100, 80, 60))
    result = apply_adjustments(img, AdjustmentProfile(exposure=2.0))
    orig_px = img.getpixel((0, 0))
    new_px = result.getpixel((0, 0))
    assert new_px[0] >= orig_px[0]
    assert new_px[1] >= orig_px[1]
    assert new_px[2] >= orig_px[2]


def test_exposure_half_darkens_all_channels() -> None:
    """exposure=0.5 darkens every channel."""
    img = _make_rgb((200, 160, 120))
    result = apply_adjustments(img, AdjustmentProfile(exposure=0.5))
    orig_px = img.getpixel((0, 0))
    new_px = result.getpixel((0, 0))
    assert new_px[0] <= orig_px[0]
    assert new_px[1] <= orig_px[1]
    assert new_px[2] <= orig_px[2]


# ---------------------------------------------------------------------------
# Sharpness axis
# ---------------------------------------------------------------------------


def test_sharpness_changes_edge_contrast() -> None:
    """Sharpness adjustment changes edge contrast on a checkerboard."""
    img = _checkerboard(32)

    sharpened = apply_adjustments(img.copy(), AdjustmentProfile(sharpness=2.0))
    blurred = apply_adjustments(img.copy(), AdjustmentProfile(sharpness=0.0))

    # Measure edge contrast as the mean absolute difference between
    # horizontally-adjacent pixels in the first row.
    def edge_metric(image: Image.Image) -> float:
        pixels = [image.getpixel((x, 0)) for x in range(image.width)]
        diffs = [abs(pixels[i + 1][0] - pixels[i][0]) for i in range(len(pixels) - 1)]
        return sum(diffs) / len(diffs)

    sharp_metric = edge_metric(sharpened)
    blur_metric = edge_metric(blurred)
    # After sharpening, edges are crisper; after blurring, softer.
    assert sharp_metric >= blur_metric, (
        f"Expected sharpened ({sharp_metric:.1f}) >= blurred ({blur_metric:.1f})"
    )


# ---------------------------------------------------------------------------
# Hue axis
# ---------------------------------------------------------------------------


def test_hue_180_rotation_on_red_yields_cyan() -> None:
    """hue=180 on a red image should produce a cyan-ish result.

    Red (H≈0°) + 180° → cyan (H≈180°). The R channel should decrease
    and the G+B channels should increase.
    """
    img = _make_rgb((220, 20, 20))
    result = apply_adjustments(img, AdjustmentProfile(hue=180))
    orig_r, orig_g, orig_b = img.getpixel((0, 0))
    new_r, new_g, new_b = result.getpixel((0, 0))
    # R should drop; G or B should rise.
    assert new_r < orig_r, f"R should drop after 180° hue rotation: {orig_r} → {new_r}"
    assert new_g > orig_g or new_b > orig_b, (
        f"G or B should rise after 180° hue rotation: ({orig_g},{orig_b}) → ({new_g},{new_b})"
    )


def test_hue_0_returns_unchanged_image() -> None:
    """hue=0 must be the identity — same pixel values."""
    img = _make_rgb((180, 90, 45))
    result = apply_adjustments(img, AdjustmentProfile(hue=0))
    assert result is img


# ---------------------------------------------------------------------------
# from_config factory
# ---------------------------------------------------------------------------


def test_from_config_identity_maps_to_identity_profile() -> None:
    """AdjustmentProfile.from_config on all-zero config produces identity values."""
    from instantlink_bridge.config import AdjustmentsConfig

    cfg = AdjustmentsConfig(saturation=0, exposure=0, sharpness=0, hue=0)
    profile = AdjustmentProfile.from_config(cfg)
    assert profile == AdjustmentProfile(), f"Expected identity profile but got {profile}"


def test_from_config_plus100_saturation_maps_to_factor_2() -> None:
    from instantlink_bridge.config import AdjustmentsConfig

    cfg = AdjustmentsConfig(saturation=100)
    profile = AdjustmentProfile.from_config(cfg)
    assert profile.saturation == pytest.approx(2.0)


def test_from_config_minus100_saturation_maps_to_factor_0() -> None:
    from instantlink_bridge.config import AdjustmentsConfig

    cfg = AdjustmentsConfig(saturation=-100)
    profile = AdjustmentProfile.from_config(cfg)
    assert profile.saturation == pytest.approx(0.0)


def test_from_config_plus100_exposure_maps_to_factor_2() -> None:
    from instantlink_bridge.config import AdjustmentsConfig

    cfg = AdjustmentsConfig(exposure=100)
    profile = AdjustmentProfile.from_config(cfg)
    assert profile.exposure == pytest.approx(2.0)


def test_from_config_minus100_exposure_maps_to_factor_half() -> None:
    from instantlink_bridge.config import AdjustmentsConfig

    cfg = AdjustmentsConfig(exposure=-100)
    profile = AdjustmentProfile.from_config(cfg)
    assert profile.exposure == pytest.approx(0.5)


def test_from_config_hue_plus100_maps_to_180_degrees() -> None:
    from instantlink_bridge.config import AdjustmentsConfig

    cfg = AdjustmentsConfig(hue=100)
    profile = AdjustmentProfile.from_config(cfg)
    assert profile.hue == 180


def test_from_config_hue_minus100_maps_to_minus180_degrees() -> None:
    from instantlink_bridge.config import AdjustmentsConfig

    cfg = AdjustmentsConfig(hue=-100)
    profile = AdjustmentProfile.from_config(cfg)
    assert profile.hue == -180


# ---------------------------------------------------------------------------
# Phase 4: datestamp overlay
# ---------------------------------------------------------------------------


def _make_jpeg_with_exif(exif_date: str, size: tuple[int, int] = (200, 200)) -> bytes:
    """Return JPEG bytes for an image with DateTimeOriginal set."""
    img = Image.new("RGB", size, (180, 140, 100))
    exif = img.getexif()
    exif[36867] = exif_date  # DateTimeOriginal
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def test_datestamp_renders_when_text_set() -> None:
    """datestamp=True with non-empty datestamp_text changes the bottom-right region."""
    img = _make_rgb(size=(200, 200))
    profile = AdjustmentProfile(datestamp=True, datestamp_text="May 3, 2026")
    result = apply_adjustments(img.copy(), profile)

    # Bottom-right quadrant must differ from the plain colour fill.
    orig = img.crop((100, 100, 200, 200))
    stamped = result.crop((100, 100, 200, 200))
    assert orig.tobytes() != stamped.tobytes(), (
        "Bottom-right region should differ when datestamp_text is set"
    )


def test_datestamp_no_op_when_text_empty() -> None:
    """datestamp=True but empty datestamp_text must leave the image unchanged."""
    img = _make_rgb(size=(200, 200))
    profile = AdjustmentProfile(datestamp=True, datestamp_text="")
    result = apply_adjustments(img.copy(), profile)
    assert img.tobytes() == result.tobytes(), (
        "Image should be unchanged when datestamp_text is empty"
    )


# ---------------------------------------------------------------------------
# Phase 4: watermark overlay
# ---------------------------------------------------------------------------


def test_watermark_renders_when_text_set() -> None:
    """watermark=True with non-empty watermark_text changes the top-right region."""
    img = _make_rgb(size=(200, 200))
    profile = AdjustmentProfile(watermark=True, watermark_text="InstantLink")
    result = apply_adjustments(img.copy(), profile)

    # Top-right quadrant must differ from the plain colour fill.
    orig = img.crop((100, 0, 200, 100))
    stamped = result.crop((100, 0, 200, 100))
    assert orig.tobytes() != stamped.tobytes(), (
        "Top-right region should differ when watermark_text is set"
    )


def test_watermark_no_op_when_text_empty() -> None:
    """watermark=True but empty watermark_text must leave the image unchanged."""
    img = _make_rgb(size=(200, 200))
    profile = AdjustmentProfile(watermark=True, watermark_text="")
    result = apply_adjustments(img.copy(), profile)
    assert img.tobytes() == result.tobytes(), (
        "Image should be unchanged when watermark_text is empty"
    )


# ---------------------------------------------------------------------------
# Phase 6: vignette
# ---------------------------------------------------------------------------


def test_vignette_zero_is_identity() -> None:
    """vignette=0 must produce byte-identical output to the identity profile."""
    img = _make_rgb(size=(200, 200))
    result = apply_adjustments(img, AdjustmentProfile(vignette=0))
    # vignette=0 is the default; the whole profile is identity so same object.
    assert result is img


def test_vignette_darkens_corners_more_than_centre() -> None:
    """vignette=100 on a solid-colour fixture darkens corners more than the centre.

    Constraints from the plan:
    - Centre pixel is at most ~5 levels darker than original (~123 from 128).
    - Corner pixels are noticeably darker (≤ 50).
    """
    size = 200
    original_value = 128
    img = Image.new("RGB", (size, size), (original_value, original_value, original_value))
    profile = AdjustmentProfile(vignette=100)
    result = apply_adjustments(img.copy(), profile)

    cx, cy = size // 2, size // 2
    centre_r, _, _ = result.getpixel((cx, cy))
    corner_r, _, _ = result.getpixel((0, 0))

    # Centre should be close to unchanged (within 5 levels).
    assert centre_r >= original_value - 5, (
        f"Centre pixel should be near original {original_value}, got {centre_r}"
    )
    # Corner should be noticeably dark.
    assert corner_r <= 50, f"Corner pixel should be ≤ 50, got {corner_r}"
    # Corner must be darker than centre.
    assert corner_r < centre_r, (
        f"Corner ({corner_r}) should be darker than centre ({centre_r})"
    )


def test_vignette_runs_before_overlays() -> None:
    """vignette=100 + datestamp: the datestamp text survives on top of darkened corners.

    Verify:
    - A pixel near the top-left corner (away from the datestamp text) is dark
      (vignette took effect).
    - The datestamp overlay rendered on top (the bottom-right region changed
      from the plain-colour fill, indicating the text was drawn).
    """
    size = 200
    img = Image.new("RGB", (size, size), (128, 128, 128))
    profile = AdjustmentProfile(vignette=100, datestamp=True, datestamp_text="2026-05-30")
    result = apply_adjustments(img.copy(), profile)

    # Top-left corner should be dark (vignette applied).
    corner_r, corner_g, corner_b = result.getpixel((0, 0))
    assert corner_r <= 50 and corner_g <= 50 and corner_b <= 50, (
        f"Top-left corner should be dark after vignette=100, got ({corner_r},{corner_g},{corner_b})"
    )

    # Bottom-right region (datestamp area) must differ from a plain vignette-only result —
    # i.e. the text was drawn on top of the vignette.
    vignette_only = apply_adjustments(img.copy(), AdjustmentProfile(vignette=100))
    result_br = result.crop((100, 100, 200, 200))
    vignette_br = vignette_only.crop((100, 100, 200, 200))
    assert result_br.tobytes() != vignette_br.tobytes(), (
        "Bottom-right region should differ from vignette-only — datestamp should be on top"
    )
