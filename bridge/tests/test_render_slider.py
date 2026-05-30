"""Tests for plan 036 phase 2: draw_slider primitive.

Pixel-level assertions use the default track_height=6, thumb_width=8, so
the track occupies rows y .. y+5 and the thumb occupies rows y-3 .. y+8
(track_cy = y+3, thumb extends ±6 from there).
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from instantlink_bridge.ui.render import draw_slider
from instantlink_bridge.ui.theme import theme_for

# Use light theme for pixel assertions (colours are well-defined).
THEME = theme_for("light")

# Helper: pixel at (px, py) in a fresh image rendered by draw_slider.
def _render(
    value: int,
    min_value: int = -100,
    max_value: int = 100,
    w: int = 100,
    x: int = 10,
    y: int = 20,
    symmetric: bool = True,
) -> tuple[Image.Image, int]:
    """Return (image, thumb_cx) for the given slider parameters."""
    img = Image.new("RGB", (240, 240), THEME.bg)
    draw = ImageDraw.Draw(img)
    thumb_cx = draw_slider(
        draw,
        x,
        y,
        w,
        value,
        min_value,
        max_value,
        theme=THEME,
        symmetric=symmetric,
    )
    return img, thumb_cx


def _pixel(img: Image.Image, px: int, py: int) -> tuple[int, int, int]:
    r, g, b = img.getpixel((px, py))  # type: ignore[misc]
    return (r, g, b)


def _hex_to_rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ---------------------------------------------------------------------------
# test_slider_value_zero_centres_thumb_on_zero_line
# ---------------------------------------------------------------------------


def test_slider_value_zero_centres_thumb_on_zero_line() -> None:
    """Symmetric range [-100, +100], value=0: thumb_cx should be x + w//2 = 60."""
    _, thumb_cx = _render(value=0, min_value=-100, max_value=100, w=100, x=10)
    # zero_x for [-100,+100] range: 10 + int(100 * (0 - (-100)) / 200) = 10 + 50 = 60
    assert thumb_cx == 60, f"Expected thumb_cx=60, got {thumb_cx}"


# ---------------------------------------------------------------------------
# test_slider_value_positive_fills_right_half
# ---------------------------------------------------------------------------


def test_slider_value_positive_fills_right_half() -> None:
    """value=+50 → fill covers zero_x..thumb_cx; pixel at x+60 should be accent_blue."""
    # zero_x = 10 + 50 = 60; thumb_cx = 10 + int(100 * 150/200) = 10 + 75 = 85
    img, _thumb_cx = _render(value=50, min_value=-100, max_value=100, w=100, x=10, y=20)

    accent_blue = _hex_to_rgb(THEME.accent_blue)
    surface_elevated = _hex_to_rgb(THEME.surface_elevated)

    # A pixel between zero_x (60) and thumb_cx (85) should be accent_blue.
    # Use track_cy = y + 3 = 23 for a pixel that's strictly inside the fill.
    fill_pixel = _pixel(img, 70, 23)
    assert fill_pixel == accent_blue, (
        f"Expected accent_blue {accent_blue} at fill zone, got {fill_pixel}"
    )

    # A pixel left of zero_x (e.g. x+45=55) should be surface_elevated (unfilled track).
    unfilled_pixel = _pixel(img, 55, 23)
    assert unfilled_pixel == surface_elevated, (
        f"Expected surface_elevated {surface_elevated} left of zero, got {unfilled_pixel}"
    )


# ---------------------------------------------------------------------------
# test_slider_value_negative_fills_left_half
# ---------------------------------------------------------------------------


def test_slider_value_negative_fills_left_half() -> None:
    """value=-50 → fill covers thumb_cx..zero_x; pixel at x+40 should be accent_blue."""
    # zero_x = 60; thumb_cx = 10 + int(100 * 50/200) = 10 + 25 = 35
    img, _thumb_cx = _render(value=-50, min_value=-100, max_value=100, w=100, x=10, y=20)

    accent_blue = _hex_to_rgb(THEME.accent_blue)
    surface_elevated = _hex_to_rgb(THEME.surface_elevated)

    # A pixel between thumb_cx (35) and zero_x (60): x+45=55, track_cy=23
    fill_pixel = _pixel(img, 45, 23)
    assert fill_pixel == accent_blue, (
        f"Expected accent_blue {accent_blue} in fill zone, got {fill_pixel}"
    )

    # A pixel right of zero_x: x+70=80, should be surface_elevated
    unfilled_pixel = _pixel(img, 80, 23)
    assert unfilled_pixel == surface_elevated, (
        f"Expected surface_elevated {surface_elevated} right of zero, got {unfilled_pixel}"
    )


# ---------------------------------------------------------------------------
# test_slider_asymmetric_fills_from_left
# ---------------------------------------------------------------------------


def test_slider_asymmetric_fills_from_left() -> None:
    """symmetric=False, range [0,100], value=75 → left ~3/4 filled."""
    # thumb_cx = 10 + int(100 * 75/100) = 10 + 75 = 85
    # Fill region: x=10 .. thumb_cx=85, track_cy = 20+3 = 23
    img, _thumb_cx = _render(
        value=75, min_value=0, max_value=100, w=100, x=10, y=20, symmetric=False
    )

    accent_blue = _hex_to_rgb(THEME.accent_blue)
    surface_elevated = _hex_to_rgb(THEME.surface_elevated)

    # Pixel at x+30=40, track_cy=23 → inside fill
    fill_pixel = _pixel(img, 40, 23)
    assert fill_pixel == accent_blue, (
        f"Expected accent_blue {accent_blue} in asymmetric fill, got {fill_pixel}"
    )

    # Pixel at x+95=105, track_cy=23 → outside fill (beyond thumb)
    unfilled_pixel = _pixel(img, 105, 23)
    assert unfilled_pixel == surface_elevated, (
        f"Expected surface_elevated {surface_elevated} beyond thumb, got {unfilled_pixel}"
    )


# ---------------------------------------------------------------------------
# test_slider_thumb_clamps_inside_track
# ---------------------------------------------------------------------------


def test_slider_thumb_clamps_inside_track() -> None:
    """value=+100 → thumb_cx clamped to x + w - thumb_width//2 = 10+100-4 = 106."""
    _, thumb_cx = _render(value=100, min_value=-100, max_value=100, w=100, x=10)
    # raw_cx = 10 + int(100 * 200/200) = 10 + 100 = 110
    # clamped: x + w - thumb_width//2 = 10 + 100 - 4 = 106
    assert thumb_cx == 106, f"Expected clamped thumb_cx=106, got {thumb_cx}"


def test_slider_thumb_clamps_min_inside_track() -> None:
    """value=-100 → thumb_cx clamped to x + thumb_width//2 = 10+4 = 14."""
    _, thumb_cx = _render(value=-100, min_value=-100, max_value=100, w=100, x=10)
    # raw_cx = 10 + int(100 * 0/200) = 10
    # clamped: x + thumb_width//2 = 10 + 4 = 14
    assert thumb_cx == 14, f"Expected clamped thumb_cx=14, got {thumb_cx}"


# ---------------------------------------------------------------------------
# test_slider_returns_thumb_cx
# ---------------------------------------------------------------------------


def test_slider_returns_thumb_cx() -> None:
    """Confirm return value matches the expected formula for an unclamped position."""
    # value=25, range [-100,+100], w=100, x=10
    # raw_cx = 10 + int(100 * (25 - (-100)) / 200) = 10 + int(100 * 125/200) = 10 + 62 = 72
    # Not clamped (14 <= 72 <= 106).
    _, thumb_cx = _render(value=25, min_value=-100, max_value=100, w=100, x=10)
    assert thumb_cx == 72, f"Expected thumb_cx=72, got {thumb_cx}"


# ---------------------------------------------------------------------------
# test_slider_degenerate_range
# ---------------------------------------------------------------------------


def test_slider_degenerate_range() -> None:
    """min_value == max_value → thumb centred, no crash."""
    img, thumb_cx = _render(value=0, min_value=50, max_value=50, w=100, x=10)
    assert thumb_cx == 60, f"Expected degenerate thumb_cx=60, got {thumb_cx}"
    assert img.size == (240, 240)


# ---------------------------------------------------------------------------
# test_slider_symmetric_has_centre_zero_tick (plan 036 audit item 1)
# ---------------------------------------------------------------------------


def test_slider_symmetric_has_centre_zero_tick() -> None:
    """Symmetric slider, value=0: the tick at zero_x must be visible.

    When value==0 no fill is drawn.  The 1 px vertical tick is drawn at zero_x
    and extends 1 px above the track (y-1 = 19).  We check the pixel just above
    the track where the thumb (centred on track_cy=23) does not paint, so the
    separator colour is not overwritten.
    zero_x = x + int(w * (0-(-100)) / 200) = 10 + 50 = 60
    tick extends from y-1=19 to y+track_height+1=27.  The thumb's top edge is
    track_cy - thumb_height//2 = 23 - 6 = 17, so y=19 is inside the thumb.
    Check y=19 which is still within the tick but also inside the thumb — the
    thumb draws on top, so we should see slider_thumb_fill there.
    Better: check pixel at (60, 19) — the tick draws first at y-1=19,
    then the thumb draws on top since thumb_height=12 and track_height=6,
    track_cy=23, thumb top=17.  So (60,19) is inside the thumb, not useful.
    Use the pixel just at the track's top edge: (60, y) = (60, 20).  The
    track is filled surface_elevated first, then the tick overwrites it with
    separator, then fill (none for value=0) would overwrite.  The thumb
    occupies tx0..tx1 = 56..64, ty0..ty1 = 17..29 — so (60,20) is inside
    the thumb and gets thumb fill.  The tick at y=20 IS overwritten by thumb.
    The only reliable pixel is above the thumb: y < 17.  The tick extends
    to y-1=19, but 19 > 17 so still inside thumb.
    Conclusion: with default track_height=6 and thumb_height=12 the tick is
    completely covered by the thumb at value=0.  Use a larger track so the
    tick extends beyond the thumb, OR verify via a wider render with y offset
    big enough that we can sample the tick extension at y < thumb top.
    Use thumb_width=4 in a custom render: thumb top = 23-6=17, tick from y-1=19
    — still inside.  The invariant that matters is: the tick is drawn; in
    render the tick width=1 means pixel (60, 20..26) is separator BEFORE the
    fill and BEFORE the thumb; the thumb paints white on top for value=0.
    So the correct test: render with value=+50 and verify the tick at zero_x
    is still visible in the UNFILLED region above the fill (tick colour
    persists where fill does not paint).  For value=+50 the fill covers
    zero_x..thumb_cx; zero_x itself is the left boundary of the fill rect so
    the pixel at zero_x may be fill colour.  Check zero_x-1 (just left of
    zero_x, in the unfilled region) for the tick — but the tick is 1 px wide
    exactly at zero_x, not zero_x-1.
    Actual cleanest proof: render value=0, use a large track_height so the
    tick extension at y-1 is outside the thumb vertically.

    Simplest fix: with track_height=20, thumb_height=12 → track_cy=y+10,
    thumb top = track_cy - 6 = y+4.  Tick extends to y-1.  So y-1 < y+4 →
    still inside thumb vertical span (thumb goes from y+4 to y+16).
    There is no vertical position where the tick is outside the thumb because
    the thumb is always at least track_height tall and centred on the track.

    Real proof: render with value != 0 and check the separator-coloured tick
    extension in the above-track zone outside the thumb's horizontal span.
    With value=+1 the thumb moves right of zero_x, so the tick at zero_x is
    no longer covered by the thumb.  Check pixel at (zero_x, y-1) = (60, 19).
    thumb_cx for value=+1: raw_cx = 10 + int(100 * 101/200) = 10 + 50 = 60
    (integer truncation!) → same as zero_x.  Use value=+10:
    raw_cx = 10 + int(100 * 110/200) = 10 + 55 = 65; clamped: ok.
    thumb occupies 65-4=61..69 horizontally.  Pixel (60, 19) is at zero_x=60,
    outside the thumb's x range (61..69) → only the tick paint is here.
    """
    # Render with value=+10 so the thumb moves right of zero_x.
    # zero_x=60, thumb_cx=65 → tick at x=60 is outside the thumb (61..69).
    # The tick extends from y-1=19 to y+track_height+1=27.
    # Pixel (60, 19) is above the track and outside the thumb → separator colour.
    img, _ = _render(value=10, min_value=-100, max_value=100, w=100, x=10, y=20, symmetric=True)

    separator = _hex_to_rgb(THEME.separator)

    # The pixel just above the track at zero_x, outside the thumb, must be separator.
    tick_pixel = _pixel(img, 60, 19)
    assert tick_pixel == separator, (
        f"Expected separator {separator} at centre-zero tick (60,19), got {tick_pixel}"
    )


def test_slider_symmetric_tick_painted_over_by_fill_when_nonzero() -> None:
    """Symmetric slider, value=+50: the fill region paints over the tick.

    The pixel at zero_x inside the fill region should be accent_blue, not separator,
    because fill draws after the tick.
    zero_x = 60, fill is from zero_x..thumb_cx for positive values,
    so the pixel at (60, 23) is the left boundary of the fill.
    """
    img, _ = _render(value=50, min_value=-100, max_value=100, w=100, x=10, y=20, symmetric=True)

    accent_blue = _hex_to_rgb(THEME.accent_blue)
    # Pixel at zero_x+1 = 61 (inside fill, not on boundary) should be accent_blue.
    fill_pixel = _pixel(img, 61, 23)
    assert fill_pixel == accent_blue, (
        f"Expected accent_blue {accent_blue} inside fill zone at (61,23), got {fill_pixel}"
    )


def test_slider_asymmetric_has_no_centre_tick() -> None:
    """Asymmetric slider must NOT draw a centre tick — left-edge anchors the scale."""
    img, _ = _render(value=0, min_value=0, max_value=100, w=100, x=10, y=20, symmetric=False)

    separator = _hex_to_rgb(THEME.separator)
    surface_elevated = _hex_to_rgb(THEME.surface_elevated)

    # For asymmetric with value=0, thumb is at x (clamped to x+4), no fill is drawn.
    # The track centre at (60, 23) should be surface_elevated — no centre tick.
    centre_pixel = _pixel(img, 60, 23)
    assert centre_pixel == surface_elevated, (
        f"Expected surface_elevated {surface_elevated} at (60,23) for asymmetric slider, "
        f"got {centre_pixel} (separator={separator})"
    )


# ---------------------------------------------------------------------------
# test_slider_thumb_fill_is_bright_in_both_themes (plan 036 audit item 4)
# ---------------------------------------------------------------------------


def test_slider_thumb_fill_is_bright_in_dark_theme() -> None:
    """Dark mode thumb fill must be near-white (r>200, g>200, b>200).

    The slider_thumb_fill token is #FFFFFF in both themes so the thumb
    always reads as a bright notch against the dark track.
    """
    dark_theme = theme_for("dark")
    img = Image.new("RGB", (240, 240), dark_theme.bg)
    draw = ImageDraw.Draw(img)
    # value=0 → thumb at zero_x = x + w//2 = 10+50 = 60; track_cy = y+3 = 23
    draw_slider(draw, 10, 20, 100, 0, -100, 100, theme=dark_theme, symmetric=True)

    # Thumb centre: thumb_cx=60, track_cy=23.  Thumb is 8px wide, 12px tall,
    # centred on the track, so the thumb fill pixel is at (60, 23).
    r, g, b = img.getpixel((60, 23))  # type: ignore[misc]
    assert r > 200 and g > 200 and b > 200, (
        f"Dark mode thumb fill expected near-white, got ({r},{g},{b}). "
        "slider_thumb_fill must be #FFFFFF in DARK_THEME."
    )
