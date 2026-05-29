"""240x240 LCD screen rendering."""

from __future__ import annotations

from collections.abc import Iterable

from PIL import Image, ImageDraw, ImageFont

from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.ui.models import UiMode, UiSnapshot

LCD_SIZE = (240, 240)
Font = ImageFont.ImageFont | ImageFont.FreeTypeFont

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
STATUS_BAR_H = 30  # top status bar height (single line)
HINT_BAR_Y = 220  # top of hint bar row (240 - 20)
BODY_TOP = STATUS_BAR_H + 4  # first usable body y
TOAST_Y = 208  # settings_message toast y


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


def render_snapshot(snapshot: UiSnapshot) -> Image.Image:
    """Render one UI frame."""

    image = Image.new("RGB", LCD_SIZE, BG)
    draw = ImageDraw.Draw(image)
    font_scale, _row_scale = _scale_for_snapshot(snapshot)
    fonts: dict[str, Font] = {
        key: _font(max(1, round(base * font_scale))) for key, base in _BASE_FONTS.items()
    }

    draw_status_bar(draw, snapshot, fonts)

    if snapshot.mode is UiMode.READY:
        _ready(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.SETTINGS:
        _settings(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.VALIDATION:
        _validation(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.NO_FILM:
        _no_film(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.PRINTER_SEARCHING:
        _printer_searching(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.PRINTER_OFFLINE:
        _printer_offline(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.IMAGE_RECEIVED:
        _image_received(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.AWAITING_CONFIRM:
        _awaiting_confirm(image, draw, snapshot, fonts)
    elif snapshot.mode is UiMode.PRINTING:
        _printing(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.PRINT_COMPLETE:
        _print_complete(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.PAIRING:
        _pairing(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.PAIR_FAILED:
        _pair_failed(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.ERROR:
        _error(draw, snapshot, fonts)
    elif snapshot.mode is UiMode.BOOTING:
        _booting(draw, snapshot, fonts)
    else:
        _needs_pairing(draw, snapshot, fonts)

    return image


# ---------------------------------------------------------------------------
# New building-block helpers
# ---------------------------------------------------------------------------


def draw_status_bar(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    """Draw the single-line 30px status bar.

    Layout (left to right):
      [dot]  printer-name          film/battery  bridge-battery

    The mode name never appears here — it belongs only in the body title.
    """

    accent, _ = _snapshot_chrome(snapshot)
    font_small = fonts["small"]

    # Background
    draw.rectangle((0, 0, 239, STATUS_BAR_H - 1), fill=PANEL)

    # Status dot (6px filled circle, mode accent colour)
    dot_x, dot_y, dot_r = 8, STATUS_BAR_H // 2, 3
    draw.ellipse(
        (dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r),
        fill=accent,
    )

    # Printer name (left, after dot)
    if snapshot.paired_printer is not None:
        printer_name = snapshot.paired_printer.name
    else:
        printer_name = "No printer"

    # Right side: bridge battery (outermost) then film+printer-battery chip.
    # In SETTINGS mode, prepend the position counter (e.g. "3/10") so the user
    # always sees navigation context without taking a row out of the body.
    right_parts: list[str] = []
    power = bridge_power_header_text(snapshot)
    if power is not None:
        right_parts.append(power)

    film_battery = _status_bar_printer_chip(snapshot)
    if film_battery is not None:
        right_parts.insert(0, film_battery)

    if snapshot.mode is UiMode.SETTINGS and snapshot.settings_rows:
        selected = min(snapshot.selected_index, len(snapshot.settings_rows) - 1)
        right_parts.insert(0, f"{selected + 1}/{len(snapshot.settings_rows)}")

    right_text = "  ".join(right_parts) if right_parts else ""
    right_width = _text_width(draw, right_text, font_small) if right_text else 0
    right_x = 232 - right_width

    # Printer name — fitted to avoid overlap with right side
    name_max = max(0, right_x - 18 - 4)
    fitted_name = _fit_text_to_width(draw, printer_name, font_small, name_max)
    name_y = STATUS_BAR_H // 2 - 5  # vertically centred for 10px font
    _text(draw, 18, name_y, fitted_name, font_small, TEXT)

    if right_text:
        _text(draw, right_x, name_y, right_text, font_small, MUTED)


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
) -> None:
    """Draw the single-row hint bar at the bottom of the screen.

    ``hints`` is a (left, center, right) triple of glyph/label strings.
    Empty strings are not drawn. Center text is centered at x=120.
    """

    draw.rectangle((0, HINT_BAR_Y - 2, 239, 239), fill=PANEL)
    left, center, right = hints
    x_left = 8
    x_right = 232
    if left:
        _text(draw, x_left, HINT_BAR_Y, _fit_text_to_width(draw, left, font, 74), font, MUTED)
    if center:
        cw = _text_width(draw, center, font)
        _text(draw, 120 - cw // 2, HINT_BAR_Y, center, font, MUTED)
    if right:
        rw = _text_width(draw, right, font)
        fitted_right = _fit_text_to_width(draw, right, font, 74)
        _text(draw, x_right - rw, HINT_BAR_Y, fitted_right, font, MUTED)


def draw_settings_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    value: str,
    hint: str,
    *,
    selected: bool,
    font: Font,
) -> None:
    """Draw a flat 2-column settings row with a 3px GREEN left accent when selected."""

    bg = PANEL
    text_fill = TEXT if selected else MUTED
    draw.rectangle((14, y, 226, y + 19), fill=bg)
    # 3px green left accent on selected row
    accent_color = GREEN if selected else PANEL
    draw.rectangle((14, y, 17, y + 19), fill=accent_color)

    kind = _settings_row_kind(hint)
    marker, marker_fill = _settings_row_marker(kind, selected)

    label_max = 94
    _text(draw, 22, y + 3, _fit_text_to_width(draw, label, font, label_max), font, text_fill)

    marker_width = _text_width(draw, marker, font) if marker else 0
    marker_x = 218 - marker_width
    if marker:
        _text(draw, marker_x, y + 3, marker, font, marker_fill if not selected else TEXT)

    value_right = marker_x - 4 if marker else 218
    value_text = _fit_text_to_width(draw, value, font, max(0, value_right - 122))
    value_width = _text_width(draw, value_text, font)
    _text(draw, max(122, value_right - value_width), y + 3, value_text, font, text_fill)


# ---------------------------------------------------------------------------
# Mode renderers
# ---------------------------------------------------------------------------


def _booting(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    _center_lines(draw, ["Starting", "Checking printer"], 70, fonts["large"], TEXT)
    # No hint bar for BOOTING


def _ready(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    accepting = can_accept_images(snapshot)
    if not accepting:
        _validation(draw, snapshot, fonts)
        return

    _center_lines(draw, ["Ready", "to print"], 60, fonts["large"], TEXT)
    # Body: waiting for upload + FTP address
    _text(draw, 18, 120, "Waiting for upload", fonts["body"], TEXT)
    ftp_line = _ready_ftp_line(snapshot)
    _text(draw, 18, 140, ftp_line, fonts["small"], MUTED)
    _text(draw, 18, 156, "Next photo prints in order", fonts["small"], MUTED)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


def _validation(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    accepting = can_accept_images(snapshot)
    _center_lines(
        draw,
        ["Ready to print" if accepting else "Setup needed"],
        62,
        fonts["large"],
        TEXT,
    )
    causes = readiness_cause_texts(snapshot)
    if not causes:
        _text(draw, 18, 112, "FTP and printer ready", fonts["body"], TEXT)
        _text(draw, 18, 132, "Waiting for upload", fonts["small"], MUTED)
    else:
        _text(draw, 18, 112, "Next action", fonts["body"], TEXT)
        for index, cause in enumerate(causes[:3]):
            _text(draw, 18, 132 + index * 17, _ellipsize(cause, 31), fonts["small"], YELLOW)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


def _no_film(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    _center_lines(draw, ["Replace", "film pack"], 60, fonts["large"], TEXT)
    _text(draw, 18, 128, "No-film test is in Settings", fonts["small"], MUTED)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


def _printer_searching(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    message = snapshot.printer_status_message or "Keep printer awake"
    if message in {"Restart printer", "Close phone app"}:
        _center_lines(draw, ["Printer seen", "connect blocked"], 58, fonts["large"], TEXT)
        _text(draw, 18, 128, "Close phone app or phone BT", fonts["small"], YELLOW)
        _text(draw, 18, 146, "Power-cycle printer, then retry", fonts["small"], MUTED)
    elif message == "Printer seen; connecting":
        _center_lines(draw, ["Printer seen", "connecting"], 58, fonts["large"], TEXT)
        _text(draw, 18, 128, "Opening Bluetooth session", fonts["small"], TEXT)
        _text(draw, 18, 146, "If stuck, close phone app", fonts["small"], MUTED)
    elif message == "Saw other Instax":
        _center_lines(draw, ["Other printer", "seen"], 58, fonts["large"], TEXT)
        _text(draw, 18, 128, "Selected printer not visible", fonts["small"], TEXT)
        _text(draw, 18, 146, "Turn selected printer on", fonts["small"], YELLOW)
    elif message in {"Scanning: 0 printers", "No printer signal"}:
        _center_lines(draw, ["Looking for", "printer"], 58, fonts["large"], TEXT)
        _text(draw, 18, 128, "Turn printer on and keep awake", fonts["small"], TEXT)
        _text(draw, 18, 146, "Phone Bluetooth may grab it", fonts["small"], MUTED)
    else:
        _center_lines(draw, ["Searching"], 75, fonts["large"], TEXT)
        # Single action line — message is typically already actionable
        # ("Turn printer on" / "Keep printer awake"); a hardcoded prefix
        # would duplicate it (e.g. "Turn selected printer on" + "Turn printer on").
        _text(draw, 18, 128, _ellipsize(message, 31), fonts["body"], TEXT)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


def _printer_offline(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    message = snapshot.printer_status_message or "Printer offline"
    if message == "Checking printer":
        _center_lines(draw, ["Checking", "printer"], 62, fonts["large"], TEXT)
    elif message == "Hold K3 to re-pair":
        _center_lines(draw, ["Select", "printer"], 62, fonts["large"], TEXT)
        _text(draw, 18, 128, "Printer not found nearby", fonts["body"], YELLOW)
        hints = _mode_hints(snapshot)
        draw_hint_bar(draw, hints, fonts["hint"])
        return
    else:
        _center_lines(draw, ["Turn", "printer on"], 62, fonts["large"], TEXT)
    _text(draw, 18, 128, "Keep it awake near bridge", fonts["body"], TEXT)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


def _image_received(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    _center_lines(draw, ["Image", "received"], 55, fonts["large"], TEXT)
    if snapshot.last_image_name is not None:
        _text(draw, 18, 126, _ellipsize(snapshot.last_image_name, 25), fonts["body"], TEXT)
    _text(draw, 18, 148, "Received over FTP", fonts["small"], MUTED)
    _text(draw, 18, 164, film_status_text(snapshot), fonts["small"], MUTED)
    # No hint bar for IMAGE_RECEIVED (transitions automatically)


def _awaiting_confirm(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    title = snapshot.print_title or "Printing soon"
    detail = _physical_control_text(snapshot.print_detail or "Press K2 to cancel")
    if snapshot.preview_image is not None:
        draw.rounded_rectangle((18, 42, 222, 151), radius=4, fill=PANEL)
        preview = snapshot.preview_image
        x = 120 - preview.width // 2
        y = 96 - preview.height // 2
        canvas.paste(preview, (x, y))
        _text(draw, 18, 155, _ellipsize(title, 27), fonts["body"], TEXT)
        _text(draw, 18, 173, _ellipsize(detail, 31), fonts["small"], YELLOW)
        _text(draw, 18, 189, _ellipsize(preview_state_text(snapshot), 31), fonts["small"], MUTED)
    else:
        _center_lines(draw, [title], 62, fonts["large"], TEXT)
        if snapshot.last_image_name is not None:
            _text(draw, 18, 104, _ellipsize(snapshot.last_image_name, 25), fonts["body"], TEXT)
        _progress_bar(draw, 18, 128, snapshot.print_progress_percent, BLUE, fonts["small"])
        _text(draw, 18, 154, _ellipsize(detail, 31), fonts["small"], YELLOW)
        _text(draw, 18, 172, film_status_text(snapshot), fonts["small"], MUTED)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


def _printing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    title = snapshot.print_title or "Sending to printer"
    _center_lines(draw, [title], 58, fonts["large"], TEXT)
    detail = snapshot.print_detail or "Working"
    _text(draw, 18, 96, _ellipsize(detail, 31), fonts["body"], TEXT)
    _progress_bar(draw, 18, 122, snapshot.print_progress_percent, BLUE, fonts["small"])
    if snapshot.last_image_name is not None:
        _text(draw, 18, 150, _ellipsize(snapshot.last_image_name, 25), fonts["small"], MUTED)
    _text(draw, 18, 166, printer_model_text(snapshot), fonts["small"], MUTED)
    _text(draw, 18, 182, "Do not power off", fonts["small"], YELLOW)
    # No hint bar for PRINTING


def _print_complete(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    _center_lines(draw, ["Print", "sent"], 55, fonts["large"], TEXT)
    if snapshot.last_image_name is not None:
        _text(draw, 18, 126, _ellipsize(snapshot.last_image_name, 25), fonts["body"], TEXT)
    _text(draw, 18, 148, "Film should feed now", fonts["small"], MUTED)
    _text(draw, 18, 164, film_status_text(snapshot), fonts["small"], MUTED)
    # No hint bar for PRINT_COMPLETE (auto-returns home)


def _needs_pairing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    _center_lines(draw, ["No printer", "selected"], 58, fonts["large"], TEXT)
    _menu_item(draw, 122, "Find printer", selected=True, font=fonts["body"])
    _text(draw, 18, 162, "Turn on printer first", fonts["small"], MUTED)
    _text(draw, 18, 178, "Then press K1", fonts["small"], MUTED)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


def _settings(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    rows = snapshot.settings_rows
    font = fonts["small"]
    if not rows:
        _text(draw, 18, 58, "No settings available", fonts["body"], TEXT)
        draw_hint_bar(draw, _mode_hints(snapshot), fonts["hint"])
        return

    _font_scale, row_scale = _scale_for_snapshot(snapshot)
    row_height = max(1, round(_BASE_ROW_HEIGHT * row_scale))
    # Compute how many rows fit between status bar and hint bar (leaving 8px margin).
    body_height = HINT_BAR_Y - STATUS_BAR_H - 8
    visible_count = max(1, body_height // row_height)

    selected = min(snapshot.selected_index, len(rows) - 1)
    selected_row = rows[selected]

    # Determine bottom line content: toast takes priority, then per-row help text.
    toast_message = snapshot.settings_message
    help_text = selected_row.help if selected_row.help else ""
    if toast_message is not None:
        bottom_text = toast_message
        bottom_color = YELLOW
    elif help_text:
        bottom_text = help_text
        bottom_color = MUTED
    else:
        bottom_text = ""
        bottom_color = MUTED

    # Reserve the last visible row slot for the bottom line only when we have
    # something to show there, so it never overlaps with row content.
    bottom_shown = bool(bottom_text)
    if bottom_shown and visible_count > 1:
        visible_count -= 1

    start = min(max(0, selected - 4), max(0, len(rows) - visible_count))

    for offset, row in enumerate(rows[start : start + visible_count]):
        index = start + offset
        y = STATUS_BAR_H + 4 + offset * row_height
        draw_settings_row(
            draw,
            y,
            row.label,
            row.value,
            row.hint,
            selected=index == selected,
            font=font,
        )

    # Hint bar for settings (use label lines)
    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])

    # Bottom line occupies the slot below the last visible row (which we reserved
    # above by reducing visible_count). Toast is YELLOW; help text is MUTED.
    if bottom_shown:
        bottom_y = STATUS_BAR_H + 4 + visible_count * row_height
        fitted = _fit_text_to_width(draw, bottom_text, font, 220)
        _text(draw, 10, bottom_y, fitted, font, bottom_color)


def _pairing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    _center_lines(draw, ["Searching"], 70, fonts["large"], TEXT)
    _text(draw, 18, 128, "Keep printer awake", fonts["body"], TEXT)
    _text(draw, 18, 150, "Close phone app if it fails", fonts["small"], MUTED)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


def _pair_failed(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    _center_lines(draw, ["Find", "failed"], 50, fonts["large"], TEXT)
    message = snapshot.message or "No INSTAX printer found"
    for index, line in enumerate(_wrap_words(message, 24)[:2]):
        _text(draw, 18, 126 + index * 17, line, fonts["small"], TEXT)
    if len(_wrap_words(message, 24)) < 2:
        _text(draw, 18, 143, "Turn printer on first", fonts["small"], MUTED)
    _menu_item(draw, 162, "Try again", selected=True, font=fonts["body"])

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


def _error(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    fonts: dict[str, Font],
) -> None:
    title, detail, hint = error_copy_for_message(snapshot.message)
    _center_lines(draw, _wrap_words(title, 16)[:2], 50, fonts["large"], TEXT)
    for index, line in enumerate(_wrap_words(detail, 27)[:2]):
        _text(draw, 18, 126 + index * 17, line, fonts["small"], TEXT)
    if hint is not None:
        _text(draw, 18, 165, _ellipsize(hint, 31), fonts["small"], YELLOW)

    hints = _mode_hints(snapshot)
    draw_hint_bar(draw, hints, fonts["hint"])


# ---------------------------------------------------------------------------
# Hint data (replaces _footer / _footer_label_lines as the source of truth)
# ---------------------------------------------------------------------------


def _mode_hints(snapshot: UiSnapshot) -> tuple[str, str, str]:
    """Return (left, center, right) hint strings for a mode's hint bar."""

    lines = _footer_label_lines(snapshot)
    if not lines:
        return ("", "", "")
    # Use first line for the hint bar
    return lines[0]


# ---------------------------------------------------------------------------
# Legacy footer helpers — kept because tests import them
# ---------------------------------------------------------------------------


def _footer_label_lines(snapshot: UiSnapshot) -> tuple[tuple[str, str, str], ...]:
    if snapshot.mode is UiMode.BOOTING:
        return (("", "Starting", ""),)
    if snapshot.mode is UiMode.SETTINGS:
        return (
            ("Up/Dn", "K1 OK", "K3 Help"),
            ("Move", "Left Back", "K2 Back"),
        )
    if snapshot.mode is UiMode.NEEDS_PAIRING:
        return (("Up/Dn", "K1 Select", "Hold K3"),)
    if snapshot.mode is UiMode.PAIR_FAILED:
        return (("K1 Retry", "K2 Back", "K3 Retry"),)
    if snapshot.mode is UiMode.PAIRING:
        return (("", "Scanning", "K2 Back"),)
    if snapshot.mode is UiMode.AWAITING_CONFIRM:
        if snapshot.preview_tool == "crop":
            return (("4-way Pan", "K1 Print", "K2 Cancel"),)
        if snapshot.preview_tool == "rotate":
            return (("Left/Right", "K1 Print", "K2 Cancel"),)
        return (("Up/Dn Edit", "K1 Print", "K2 Cancel"),)
    if snapshot.mode is UiMode.PRINTING:
        return (("", "Printing", ""),)
    if snapshot.mode is UiMode.PRINT_COMPLETE:
        if snapshot.paired_printer is not None:
            return (("K1 Settings", "Done", "K3 FTP"),)
        return (("K1 Settings", "Done", "Hold K3"),)
    if snapshot.paired_printer is not None:
        return (("K1 Settings", "K2 Refresh", "K3 FTP"),)
    return (("K1 Settings", "K2 Refresh", "Hold K3"),)


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
) -> None:
    fill = BLUE if selected else PANEL
    text_fill = TEXT if selected else MUTED
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
) -> None:
    if percent is None:
        _text(draw, x, y + 2, "Progress: working...", font, MUTED)
        return
    bounded = max(0, min(100, percent))
    width = 204
    height = 12
    draw.rounded_rectangle((x, y, x + width, y + height), radius=3, fill=PANEL)
    if bounded > 0:
        filled = max(4, int(width * bounded / 100))
        draw.rounded_rectangle((x, y, x + filled, y + height), radius=3, fill=fill)
    label = f"{bounded}%"
    label_width = _text_width(draw, label, font)
    _text(draw, x + width - label_width, y + 16, label, font, MUTED)


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


def _text(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: Font,
    fill: str,
) -> None:
    draw.text((x, y), text, fill=fill, font=font)


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


def _font(size: int) -> Font:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: Font) -> int:
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
    return int(right - left)


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
    """Return a tiny charge-state marker for the top-bar battery percentage.

    A ``+`` flags charging; a discharging estimate is shown compactly when known. Kept short so it
    fits the title bar alongside the film/battery chip without crowding the 240x240 layout.
    """

    if snapshot.printer_is_charging:
        return "+"
    minutes = snapshot.printer_battery_minutes_remaining
    if minutes is None:
        return ""
    return f" {format_battery_life(minutes)}"


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


def _status_bar_printer_chip(snapshot: UiSnapshot) -> str | None:
    """Return the compact film/battery chip for the right side of the status bar.

    Shows nothing when no printer is selected or film status is unknown.
    Shows film count and optional battery when available.
    """

    if snapshot.paired_printer is None:
        return None
    if snapshot.film_remaining is None:
        return None
    film = f"{snapshot.film_remaining}/{snapshot.film_capacity}"
    if snapshot.printer_battery is not None:
        return f"{film}  {snapshot.printer_battery}%{top_bar_battery_state_text(snapshot)}"
    return film


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
