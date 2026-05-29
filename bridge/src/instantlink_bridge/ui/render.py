"""240x240 LCD screen rendering — iOS 26 / Liquid Glass aesthetic."""

from __future__ import annotations

import time
from collections.abc import Iterable

from PIL import Image, ImageDraw, ImageFont

from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.ui.i18n import t
from instantlink_bridge.ui.models import UiMode, UiSnapshot
from instantlink_bridge.ui.status_indicator import StatusState, derive_status
from instantlink_bridge.ui.theme import Theme, theme_for

LCD_SIZE = (240, 240)
Font = ImageFont.ImageFont | ImageFont.FreeTypeFont

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
HINT_BAR_H = 32  # hint bar — taller to host two-line K-chips. Each chip
# stacks the key label (K1/K2/K3) on line 1 and the action label on line 2,
# which doubles the horizontal width budget per action.
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
    "hint": 10,
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
        # objects and accept arbitrary attrs.
        try:
            primary.cjk_sibling = sibling  # type: ignore[attr-defined]
        except AttributeError:
            # ImageFont.load_default() returns a slot-restricted object;
            # fall back to a module-level cache keyed by id().
            _CJK_SIBLING_BY_ID[id(primary)] = sibling
        fonts[key] = primary

    breath_clock = time.monotonic() if now is None else now
    draw_status_bar(draw, snapshot, fonts, breath_clock, theme=theme)

    if snapshot.mode is UiMode.READY:
        _ready(draw, snapshot, fonts, theme)
    elif snapshot.mode is UiMode.SETTINGS:
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
    """Draw the 30 px status bar with a Liquid Glass pill indicator.

    The full bar background is ``theme.bg`` (calm, no tint). The live status
    word sits inside a vibrant capsule pill centred at x=120. The pill colour
    is modulated by the breath envelope for breathing states, keeping the
    whole-bar tint approach retired.

    SETTINGS mode appends a page counter (``3/10``) to the right of the pill
    in ``theme.label_secondary``.
    """

    if theme is None:
        theme = theme_for("light")

    state = derive_status(snapshot)
    font_body = fonts["body"]
    font_small = fonts["small"]

    # Bar background — neutral, no tint
    draw.rectangle((0, 0, 239, STATUS_BAR_H - 1), fill=theme.bg)

    word = t(status_bar_word(snapshot), snapshot.language)

    # Resolve pill background from the theme's semantic pill tokens,
    # then modulate by the breath envelope at `now`.
    pill_bg_rgb = _state_pill_bg(state, theme)
    pill_bg_tinted = _apply_breath(state, pill_bg_rgb, now)
    pill_bg_hex = _rgb_to_hex(pill_bg_tinted)

    fg = state.foreground()
    fg_hex = _rgb_to_hex(fg)

    # Measure pill width: max(60, text_width + 24)
    word_bbox = draw.textbbox((0, 0), word, font=font_body)
    word_w = word_bbox[2] - word_bbox[0]
    pill_w = max(60, word_w + 24)
    pill_h = 22
    pill_x = 120 - pill_w // 2
    pill_y = (STATUS_BAR_H - pill_h) // 2

    draw_pill(draw, pill_x, pill_y, pill_w, pill_h, pill_bg_hex, fg_hex, word, font_body)

    # Settings counter to the right of the pill
    if snapshot.mode is UiMode.SETTINGS and snapshot.settings_rows:
        selected = min(snapshot.selected_index, len(snapshot.settings_rows) - 1)
        counter = f"{selected + 1}/{len(snapshot.settings_rows)}"
        counter_bbox = draw.textbbox((0, 0), counter, font=font_small)
        counter_h = counter_bbox[3] - counter_bbox[1]
        counter_x = pill_x + pill_w + 6
        counter_y = (STATUS_BAR_H - counter_h) // 2 - counter_bbox[1]
        _text(draw, counter_x, counter_y, counter, font_small, theme.label_secondary)


def _state_pill_bg(
    state: StatusState,
    theme: Theme,
) -> tuple[int, int, int]:
    """Return the full-intensity pill background RGB for a StatusState."""
    return state.base_color


def _apply_breath(
    state: StatusState,
    rgb: tuple[int, int, int],
    now: float,
) -> tuple[int, int, int]:
    """Modulate rgb by the breath envelope — returns rgb unchanged for SOLID."""
    return state.tint_at(now)


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
    UiMode.PRINT_COMPLETE: "Done",
    UiMode.ERROR: "Error",
    UiMode.SETTINGS: "Settings",
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
    if mode is UiMode.READY:
        return "Connected" if can_accept_images(snapshot) else "Waiting"
    if mode is UiMode.PRINTER_SEARCHING and _is_waiting_for_user_message(
        snapshot.printer_status_message
    ):
        return "Disconnected"
    return _MODE_STATUS_WORD.get(mode, "")


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

    left, center, right = hints
    zone_w = 80
    zone_max = zone_w - 8  # 4 px padding each side
    centers = (40, 120, 200)
    # Pill is sized to host two lines comfortably. Single-line hints centre
    # vertically inside the same chip so all three chips share one height.
    pill_h = HINT_BAR_H - 6
    pill_radius = 8

    for text, cx in zip((left, center, right), centers, strict=True):
        if not text:
            continue

        # Split first token (the key) from the rest of the action label.
        # "K1 Setting" → ("K1", "Setting"); "Hold K3" → ("Hold", "K3");
        # "Done" → ("Done", "").
        if " " in text:
            line1, line2 = text.split(" ", 1)
        else:
            line1, line2 = text, ""

        fitted1 = _fit_text_to_width(draw, line1, font, zone_max)
        fitted2 = _fit_text_to_width(draw, line2, font, zone_max)
        tw1 = _text_width(draw, fitted1, font)
        tw2 = _text_width(draw, fitted2, font)

        pill_w = max(tw1, tw2) + 14  # 7 px padding each side
        pill_w = max(pill_w, 28)
        pill_x = cx - pill_w // 2
        pill_y = HINT_BAR_Y + (HINT_BAR_H - pill_h) // 2

        draw.rounded_rectangle(
            (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h),
            radius=pill_radius,
            fill=theme.hint_bg,
        )

        # Two-line layout: line 1 at the top third, line 2 at the bottom
        # third; the small font is ~10 px so two lines fit in ~22 px.
        bbox1 = draw.textbbox((0, 0), fitted1, font=font)
        line_h = bbox1[3] - bbox1[1]
        if fitted2:
            # 4 px between the key label (line 1) and the action label (line 2)
            # gives the chip clear breathing room instead of stacking them flush.
            gap = 4
            total_h = line_h * 2 + gap
            line1_y = pill_y + (pill_h - total_h) // 2 - bbox1[1]
            line2_y = line1_y + line_h + gap
            tx1 = pill_x + (pill_w - tw1) // 2 - bbox1[0]
            tx2 = pill_x + (pill_w - tw2) // 2 - draw.textbbox((0, 0), fitted2, font=font)[0]
            draw.text((tx1, line1_y), fitted1, fill=theme.hint_fg, font=font)
            draw.text((tx2, line2_y), fitted2, fill=theme.hint_fg, font=font)
        else:
            line1_y = pill_y + (pill_h - line_h) // 2 - bbox1[1]
            tx1 = pill_x + (pill_w - tw1) // 2 - bbox1[0]
            draw.text((tx1, line1_y), fitted1, fill=theme.hint_fg, font=font)


def draw_settings_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    value: str,
    hint: str,
    *,
    selected: bool,
    font: Font,
    theme: Theme | None = None,
    row_height: int = 19,
) -> None:
    """Draw a settings row in iOS picker style.

    Selected row: ``theme.accent_blue`` background, ``theme.label_inverse`` text.
    Non-selected: transparent strip; label in ``theme.label_primary``, value in
    ``theme.label_secondary``.
    """

    if theme is None:
        theme = theme_for("light")

    if selected:
        # Selected row: flat vibrant accent fill (iOS picker style). The
        # earlier "pressed into glass" inner highlight + lowlight read as
        # 1 px scan lines on the LCD rather than glass depth — removed.
        draw.rounded_rectangle(
            (14, y, 226, y + row_height - 1),
            radius=4,
            fill=theme.accent_blue,
        )
        text_fill = theme.label_inverse
        value_fill = theme.label_inverse
    else:
        text_fill = theme.label_primary
        value_fill = theme.label_secondary

    kind = _settings_row_kind(hint)
    marker, _marker_fill = _settings_row_marker(kind, selected)
    marker_fill = text_fill  # always match row text colour

    label_max = 94
    _text(draw, 22, y + 3, _fit_text_to_width(draw, label, font, label_max), font, text_fill)

    marker_width = _text_width(draw, marker, font) if marker else 0
    marker_x = 218 - marker_width
    if marker:
        _text(draw, marker_x, y + 3, marker, font, marker_fill)

    value_right = marker_x - 4 if marker else 218
    value_text = _fit_text_to_width(draw, value, font, max(0, value_right - 122))
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
    accepting = can_accept_images(snapshot)
    if not accepting:
        _validation(draw, snapshot, fonts, theme)
        return

    # Centered title — translated via i18n so it reads "就绪" / "Ready".
    # y=56 (was 75): pushes the title up so the info card at y=104 has a
    # comfortable ~18 px gap below the title instead of touching it.
    _center_lines(draw, [t("Ready", snapshot.language)], 56, fonts["large"], theme.label_primary)

    # Card spanning x=12..228, y=104..200
    card_x, card_y = 12, 104
    card_w, card_h = 216, 96
    draw_card(draw, card_x, card_y, card_w, card_h, theme)

    # Build the list of rows to show
    row_data: list[tuple[str, str]] = []

    if snapshot.paired_printer is not None:
        row_data.append((t("Type", snapshot.language), _status_bar_printer_name(snapshot)))

    if snapshot.film_remaining is not None:
        row_data.append(
            (t("Film", snapshot.language), f"{snapshot.film_remaining}/{snapshot.film_capacity}")
        )

    if snapshot.printer_battery is not None:
        charging = "+" if snapshot.printer_is_charging else ""
        battery_val = f"{snapshot.printer_battery}%{charging}"
        life = printer_battery_life_text(snapshot)
        if life is not None:
            battery_val = f"{battery_val} ({life})"
        row_data.append((t("Battery", snapshot.language), battery_val))

    if snapshot.paired_printer is not None:
        bare_id = snapshot.paired_printer.name.removeprefix("INSTAX-")
        row_data.append((t("Printer", snapshot.language), bare_id))

    if snapshot.hotspot_ssid is not None:
        row_data.append((t("SSID", snapshot.language), snapshot.hotspot_ssid))

    depth = snapshot.image_queue_depth
    if depth == 1:
        row_data.append((t("Queue", snapshot.language), t("1 photo", snapshot.language)))
    elif depth > 1:
        photos_word = t("photos", snapshot.language)
        if photos_word == "photos":
            row_data.append((t("Queue", snapshot.language), f"{depth} photos"))
        else:
            row_data.append((t("Queue", snapshot.language), f"{depth} {photos_word}"))

    # Distribute rows within the card
    if row_data:
        num_rows = len(row_data)
        row_h = card_h // max(num_rows, 1)
        row_h = min(row_h, 20)  # cap to avoid oversized rows with few items
        total_content = num_rows * row_h
        start_y = card_y + (card_h - total_content) // 2

        for i, (label, value) in enumerate(row_data):
            ry = start_y + i * row_h
            row_mid = ry + row_h // 2

            # Label (left, secondary) — 16 px inset aligns with iOS separator inset
            prefix = f"{label}: "
            lw = _text_width(draw, prefix, fonts["small"])
            label_y = row_mid - _font_height(draw, prefix, fonts["small"]) // 2
            _text(draw, card_x + 16, label_y, prefix, fonts["small"], theme.label_secondary)

            # Value (follows label, primary)
            _text(
                draw, card_x + 16 + lw, label_y, value, fonts["small"], theme.label_primary
            )

            # Hairline after row (except last) — 16 px leading inset matches
            # iOS default UITableViewCell.separatorInset (16 pt leading).
            if i < num_rows - 1:
                draw_hairline(draw, card_x + 16, ry + row_h - 1, card_w - 32, theme)

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
        _text(draw, 18, 112, t("FTP and printer ready", snapshot.language), fonts["body"], theme.label_primary)
        _text(draw, 18, 132, t("Waiting for upload", snapshot.language), fonts["small"], theme.label_secondary)
    else:
        _text(draw, 18, 112, t("Next action", snapshot.language), fonts["body"], theme.label_primary)
        for index, cause in enumerate(causes[:3]):
            # cause strings come from readiness_cause_texts in English; the
            # i18n table has entries for the common ones ("Turn printer on",
            # "Wait for printer", "Replace film pack"). Translate at draw
            # time so 中文 mode picks them up.
            _text(
                draw,
                18,
                132 + index * 17,
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
    _text(draw, 18, 128, "No-film test is in Settings", fonts["small"], theme.label_secondary)

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
        _center_lines(draw, [t("Blocked", snapshot.language)], 75, fonts["large"], theme.label_primary)
        _text(draw, 18, 128, t("Close phone app or phone BT", snapshot.language), fonts["small"], theme.accent_yellow)
        _text(draw, 18, 146, t("Power-cycle printer, then retry", snapshot.language), fonts["small"], theme.label_secondary)
    elif message == "Printer seen; connecting":
        _center_lines(draw, [t("Connecting", snapshot.language)], 75, fonts["large"], theme.label_primary)
        _text(draw, 18, 128, t("Opening Bluetooth session", snapshot.language), fonts["small"], theme.label_primary)
        _text(draw, 18, 146, t("If stuck, close phone app", snapshot.language), fonts["small"], theme.label_secondary)
    elif message == "Saw other Instax":
        _center_lines(draw, [t("Wrong one", snapshot.language)], 75, fonts["large"], theme.label_primary)
        _text(draw, 18, 128, t("Selected printer not visible", snapshot.language), fonts["small"], theme.label_primary)
        _text(draw, 18, 146, t("Turn selected printer on", snapshot.language), fonts["small"], theme.accent_yellow)
    elif message in {"Scanning: 0 printers", "No printer signal"}:
        # No BLE signal yet — title names the active state, body gives the
        # action so a power-cycle is the obvious next step.
        _center_lines(draw, [t("Searching", snapshot.language)], 75, fonts["large"], theme.label_primary)
        _text(draw, 18, 128, t("Turn printer on and keep awake", snapshot.language), fonts["small"], theme.label_primary)
        _text(draw, 18, 146, t("Phone Bluetooth may grab it", snapshot.language), fonts["small"], theme.label_secondary)
    else:
        # Title states the active state ("Searching"); body (status_message)
        # carries the live retry copy (e.g. "Looking for printer"). The
        # message is set by the controller in English; translate at the
        # render boundary so 中文 mode picks up the i18n entry.
        _center_lines(draw, [t("Searching", snapshot.language)], 75, fonts["large"], theme.label_primary)
        _text(
            draw,
            18,
            128,
            _ellipsize(t(message, snapshot.language), 31),
            fonts["body"],
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
        _center_lines(draw, [t("Checking", snapshot.language)], 75, fonts["large"], theme.label_primary)
    elif message == "Hold K3 to re-pair":
        _center_lines(draw, [t("No printer", snapshot.language)], 75, fonts["large"], theme.label_primary)
        _text(draw, 18, 128, t("Printer not found nearby", snapshot.language), fonts["body"], theme.accent_yellow)
        hints = _mode_hints(snapshot)
        draw_hint_bar(draw, hints, fonts["hint"], theme)
        return
    else:
        _center_lines(draw, [t("Printer off", snapshot.language)], 75, fonts["large"], theme.label_primary)
    _text(draw, 18, 128, t("Keep it awake near bridge", snapshot.language), fonts["body"], theme.label_primary)

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
        _text(draw, 18, 126, _ellipsize(snapshot.last_image_name, 25), fonts["body"], theme.label_primary)
    _text(draw, 18, 148, t("Received over FTP", snapshot.language), fonts["small"], theme.label_secondary)
    _text(draw, 18, 164, film_status_text(snapshot), fonts["small"], theme.label_secondary)
    # No hint bar for IMAGE_RECEIVED (transitions automatically)


def _awaiting_confirm(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    title = snapshot.print_title or t("Printing soon", snapshot.language)
    detail = _physical_control_text(snapshot.print_detail or t("Press K2 to cancel", snapshot.language))
    if snapshot.preview_image is not None:
        # Wrap preview in a card
        draw_card(draw, 16, 40, 208, 114, theme)
        preview = snapshot.preview_image
        x = 120 - preview.width // 2
        y = 96 - preview.height // 2
        canvas.paste(preview, (x, y))
        _text(draw, 18, 158, _ellipsize(title, 27), fonts["body"], theme.label_primary)
        _text(draw, 18, 175, _ellipsize(detail, 31), fonts["small"], theme.accent_yellow)
        _text(draw, 18, 190, _ellipsize(preview_state_text(snapshot), 31), fonts["small"], theme.label_secondary)
    else:
        _center_lines(draw, [title], 62, fonts["large"], theme.label_primary)
        if snapshot.last_image_name is not None:
            _text(draw, 18, 104, _ellipsize(snapshot.last_image_name, 25), fonts["body"], theme.label_primary)
        _progress_bar(draw, 18, 128, snapshot.print_progress_percent, theme.accent_blue, fonts["small"], theme)
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
    detail = snapshot.print_detail or t("Working", snapshot.language)
    _text(draw, 18, 96, _ellipsize(detail, 31), fonts["body"], theme.label_primary)
    _progress_bar(draw, 18, 122, snapshot.print_progress_percent, theme.accent_blue, fonts["small"], theme)
    if snapshot.last_image_name is not None:
        _text(draw, 18, 150, _ellipsize(snapshot.last_image_name, 25), fonts["small"], theme.label_secondary)
    _text(draw, 18, 166, printer_model_text(snapshot), fonts["small"], theme.label_secondary)
    _text(draw, 18, 182, t("Do not power off", snapshot.language), fonts["small"], theme.accent_yellow)
    # No hint bar for PRINTING


def _print_complete(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(draw, [t("Sent", snapshot.language)], 75, fonts["large"], theme.label_primary)
    if snapshot.last_image_name is not None:
        _text(draw, 18, 126, _ellipsize(snapshot.last_image_name, 25), fonts["body"], theme.label_primary)
    _text(draw, 18, 148, t("Film should feed now", snapshot.language), fonts["small"], theme.label_secondary)
    _text(draw, 18, 164, film_status_text(snapshot), fonts["small"], theme.label_secondary)
    # No hint bar for PRINT_COMPLETE (auto-returns home)


def _needs_pairing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(draw, [t("No printer", snapshot.language)], 75, fonts["large"], theme.label_primary)
    _menu_item(draw, 122, t("Find printer", snapshot.language), selected=True, font=fonts["body"], theme=theme)
    _text(draw, 18, 162, t("Turn on printer first", snapshot.language), fonts["small"], theme.label_secondary)
    _text(draw, 18, 178, t("Then press K1", snapshot.language), fonts["small"], theme.label_secondary)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"], theme)


def _settings(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    rows = snapshot.settings_rows
    font = fonts["small"]
    if not rows:
        _text(draw, 18, 58, t("No settings available", snapshot.language), fonts["body"], theme.label_primary)
        draw_hint_bar(draw, _mode_hints(snapshot), fonts["hint"], theme)
        return

    _font_scale, row_scale = _scale_for_snapshot(snapshot)
    row_height = max(1, round(_BASE_ROW_HEIGHT * row_scale))

    selected = min(snapshot.selected_index, len(rows) - 1)
    selected_row = rows[selected]

    # Toast (settings_message) takes priority over help text. Both share a
    # dedicated strip *below* the rounded card so they can never overlap the
    # card's rounded borders.
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
        draw_settings_row(
            draw,
            y,
            t(row.label, snapshot.language),
            row.value,
            row.hint,
            selected=index == selected,
            font=font,
            theme=theme,
            row_height=row_height,
        )
        # Hairline between rows (not after last visible row)
        if offset < visible_count - 1:
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


def _pairing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
    theme: Theme,
) -> None:
    _center_lines(draw, [t("Searching", snapshot.language)], 70, fonts["large"], theme.label_primary)
    _text(draw, 18, 128, t("Keep printer awake", snapshot.language), fonts["body"], theme.label_primary)
    _text(draw, 18, 150, t("Close phone app if it fails", snapshot.language), fonts["small"], theme.label_secondary)

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
        _text(draw, 18, 143, t("Turn printer on first", snapshot.language), fonts["small"], theme.label_secondary)
    _menu_item(draw, 162, t("Try again", snapshot.language), selected=True, font=fonts["body"], theme=theme)

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
        return (("Up/Dn", "KEY1 Select", "Hold KEY3"),)
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
            return (("KEY1 Setting", "Done", "KEY3 Network"),)
        return (("KEY1 Setting", "Done", "Hold KEY3"),)
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


# Fallback CJK-sibling lookup for fonts that don't accept arbitrary attrs.
# Keyed by id(primary_font); cleared between renders implicitly because the
# primary fonts are rebuilt every render_snapshot call.
_CJK_SIBLING_BY_ID: dict[int, Font] = {}


def _has_cjk(text: str) -> bool:
    """Return True if ``text`` contains any CJK ideograph.

    Covers the three Unicode blocks the LCD is likely to encounter:
    CJK Unified Ideographs, the Extension-A block, and the CJK
    Compatibility Ideographs range. Hiragana/Katakana aren't in WQY
    coverage so we don't bother — the Chinese translations are pure
    Han characters.
    """

    return any(
        "一" <= ch <= "鿿"
        or "㐀" <= ch <= "䶿"
        or "豈" <= ch <= "﫿"
        for ch in text
    )


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
        return GREEN, "Complete"
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


def _font(size: int, prefer_cjk: bool = False) -> Font:
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

    Single line if the whole string fits. Otherwise we greedily fill line 1
    word by word until adding the next word would overflow, then put the
    rest on line 2 (ellipsised if it still overflows). Whitespace is the
    only break point — CJK strings without spaces stay on one line and get
    ellipsised by ``_fit_text_to_width``.
    """

    if _text_width(draw, text, font) <= max_width:
        return [text]
    words = text.split(" ")
    line1_words: list[str] = []
    i = 0
    while i < len(words):
        candidate = " ".join([*line1_words, words[i]])
        if _text_width(draw, candidate, font) > max_width:
            break
        line1_words.append(words[i])
        i += 1
    # Avoid an empty line 1 when the first word alone overflows: ellipsise it.
    if not line1_words:
        return [_fit_text_to_width(draw, text, font, max_width)]
    line1 = " ".join(line1_words)
    rest = " ".join(words[i:])
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
    if kind == "choose":
        return "<>", GREEN if not selected else TEXT
    if kind == "change":
        return "<>", GREEN if not selected else TEXT
    if kind == "run":
        return "!", YELLOW if not selected else TEXT
    if kind == "info":
        return "i", MUTED if not selected else TEXT
    if kind == "open":
        return ">", BLUE if not selected else TEXT
    return "", MUTED


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
    return snapshot.paired_printer.name


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
