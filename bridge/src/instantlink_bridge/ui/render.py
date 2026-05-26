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


def render_snapshot(snapshot: UiSnapshot) -> Image.Image:
    """Render one UI frame."""

    image = Image.new("RGB", LCD_SIZE, BG)
    draw = ImageDraw.Draw(image)
    font_small = _font(11)
    font_body = _font(14)
    font_title = _font(18)
    font_large = _font(22)

    accent, title = _snapshot_chrome(snapshot)
    _header(
        draw,
        title,
        accent,
        top_bar_status_text(snapshot),
        bridge_power_header_text(snapshot),
        font_title,
        font_small,
    )

    if snapshot.mode is UiMode.READY:
        _ready(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.SETTINGS:
        _settings(draw, snapshot, font_body, font_small)
    elif snapshot.mode is UiMode.VALIDATION:
        _validation(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.NO_FILM:
        _no_film(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.PRINTER_SEARCHING:
        _printer_searching(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.PRINTER_OFFLINE:
        _printer_offline(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.IMAGE_RECEIVED:
        _image_received(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.AWAITING_CONFIRM:
        _awaiting_confirm(image, draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.PRINTING:
        _printing(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.PRINT_COMPLETE:
        _print_complete(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.PAIRING:
        _pairing(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.PAIR_FAILED:
        _pair_failed(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.ERROR:
        _error(draw, snapshot, font_large, font_body, font_small)
    elif snapshot.mode is UiMode.BOOTING:
        _center_lines(draw, ["Starting", "Checking printer"], 70, font_large, TEXT)
    else:
        _needs_pairing(draw, snapshot, font_large, font_body, font_small)

    _footer(draw, snapshot, font_small)
    return image


def _header(
    draw: ImageDraw.ImageDraw,
    title: str,
    accent: str,
    status: str | None,
    power: str | None,
    font_title: Font,
    font_small: Font,
) -> None:
    draw.rectangle((0, 0, 239, 36), fill=accent)
    title_max_width = 132 if power is not None else 220
    _text(
        draw,
        10,
        3 if status else 8,
        _fit_text_to_width(draw, _sentence_case(title), font_title, title_max_width),
        font_title,
        BLACK,
    )
    if power is not None:
        power = _fit_text_to_width(draw, power, font_small, 86)
        width = _text_width(draw, power, font_small)
        _text(draw, max(144, 230 - width), 6, power, font_small, BLACK)
    if status is not None:
        _text(
            draw,
            10,
            23,
            _fit_text_to_width(draw, status, font_small, 220),
            font_small,
            BLACK,
        )


def _ready(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    accepting = can_accept_images(snapshot)
    if not accepting:
        _validation(draw, snapshot, font_large, font_body, font_small)
        return
    _center_lines(draw, ["Ready", "to print"], 64, font_large, TEXT)
    _text(draw, 18, 128, "Waiting for upload", font_body, TEXT)
    _text(draw, 18, 150, "Next photo prints in order", font_small, MUTED)


def _validation(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    accepting = can_accept_images(snapshot)
    _center_lines(draw, ["Ready to print" if accepting else "Setup needed"], 62, font_large, TEXT)
    causes = readiness_cause_texts(snapshot)
    if not causes:
        _text(draw, 18, 112, "FTP and printer ready", font_body, TEXT)
        _text(draw, 18, 137, "Waiting for upload", font_small, MUTED)
        return
    _text(draw, 18, 112, "Next action", font_body, TEXT)
    for index, cause in enumerate(causes[:3]):
        _text(draw, 18, 137 + index * 17, _ellipsize(cause, 31), font_small, YELLOW)


def _no_film(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    _center_lines(draw, ["Replace", "film pack"], 62, font_large, TEXT)
    _text(draw, 18, 130, "No-film test is in Settings", font_small, MUTED)
    _text(draw, 18, 148, "KEY1 opens Settings", font_small, YELLOW)


def _printer_searching(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    message = snapshot.printer_status_message or "Keep printer awake"
    if message in {"Restart printer", "Close phone app"}:
        _center_lines(draw, ["Printer seen", "connect blocked"], 58, font_large, TEXT)
        _text(draw, 18, 130, "Close phone app or phone BT", font_small, YELLOW)
        _text(draw, 18, 148, "Power-cycle printer, then retry", font_small, MUTED)
        return
    if message == "Printer seen; connecting":
        _center_lines(draw, ["Printer seen", "connecting"], 58, font_large, TEXT)
        _text(draw, 18, 130, "Opening Bluetooth session", font_small, TEXT)
        _text(draw, 18, 148, "If stuck, close phone app", font_small, MUTED)
        return
    if message == "Saw other Instax":
        _center_lines(draw, ["Other printer", "seen"], 58, font_large, TEXT)
        _text(draw, 18, 130, "Selected printer not visible", font_small, TEXT)
        _text(draw, 18, 148, "Hold KEY3 to choose again", font_small, YELLOW)
        return
    if message in {"Scanning: 0 printers", "No printer signal"}:
        _center_lines(draw, ["Looking for", "printer"], 58, font_large, TEXT)
        _text(draw, 18, 130, "Turn printer on and keep awake", font_small, TEXT)
        _text(draw, 18, 148, "Phone Bluetooth may grab it", font_small, MUTED)
        return
    _center_lines(draw, ["Finding", "printer"], 62, font_large, TEXT)
    _text(draw, 18, 130, "Turn selected printer on", font_body, TEXT)
    _text(draw, 18, 152, _ellipsize(message, 31), font_small, MUTED)


def _printer_offline(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    message = snapshot.printer_status_message or "Printer offline"
    if message == "Checking printer":
        _center_lines(draw, ["Checking", "printer"], 62, font_large, TEXT)
    elif message == "Hold K3 to re-pair":
        _center_lines(draw, ["Select", "printer"], 62, font_large, TEXT)
        _text(draw, 18, 130, "Hold KEY3 to scan again", font_body, YELLOW)
        return
    else:
        _center_lines(draw, ["Turn", "printer on"], 62, font_large, TEXT)
    _text(draw, 18, 130, "Keep it awake near bridge", font_body, TEXT)
    _text(draw, 18, 152, "KEY2 refreshes status", font_small, MUTED)


def _image_received(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    _center_lines(draw, ["Image", "received"], 55, font_large, TEXT)
    if snapshot.last_image_name is not None:
        _text(draw, 18, 128, _ellipsize(snapshot.last_image_name, 25), font_body, TEXT)
    _text(draw, 18, 151, "Received over FTP", font_small, MUTED)
    _text(draw, 18, 168, film_status_text(snapshot), font_small, MUTED)


def _awaiting_confirm(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    title = snapshot.print_title or "Printing soon"
    detail = _physical_control_text(snapshot.print_detail or "Press KEY2 to cancel")
    if snapshot.preview_image is not None:
        draw.rounded_rectangle((18, 68, 222, 151), radius=4, fill=PANEL)
        preview = snapshot.preview_image
        x = 120 - preview.width // 2
        y = 109 - preview.height // 2
        canvas.paste(preview, (x, y))
        _text(draw, 18, 155, _ellipsize(title, 27), font_body, TEXT)
        _text(draw, 18, 173, _ellipsize(detail, 31), font_small, YELLOW)
        _text(draw, 18, 187, _ellipsize(preview_state_text(snapshot), 31), font_small, MUTED)
        return
    _center_lines(draw, [title], 66, font_large, TEXT)
    if snapshot.last_image_name is not None:
        _text(draw, 18, 108, _ellipsize(snapshot.last_image_name, 25), font_body, TEXT)
    _progress_bar(draw, 18, 134, snapshot.print_progress_percent, BLUE, font_small)
    _text(draw, 18, 158, _ellipsize(detail, 31), font_small, YELLOW)
    _text(draw, 18, 176, film_status_text(snapshot), font_small, MUTED)


def _printing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    title = snapshot.print_title or "Sending to printer"
    _center_lines(draw, [title], 58, font_large, TEXT)
    detail = snapshot.print_detail or "Working"
    _text(draw, 18, 98, _ellipsize(detail, 31), font_body, TEXT)
    _progress_bar(draw, 18, 126, snapshot.print_progress_percent, BLUE, font_small)
    if snapshot.last_image_name is not None:
        _text(draw, 18, 153, _ellipsize(snapshot.last_image_name, 25), font_small, MUTED)
    _text(draw, 18, 170, printer_model_text(snapshot), font_small, MUTED)
    _text(draw, 18, 186, "Do not power off", font_small, YELLOW)


def _print_complete(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    _center_lines(draw, ["Print", "sent"], 55, font_large, TEXT)
    if snapshot.last_image_name is not None:
        _text(draw, 18, 128, _ellipsize(snapshot.last_image_name, 25), font_body, TEXT)
    _text(draw, 18, 151, "Film should feed now", font_small, MUTED)
    _text(draw, 18, 168, film_status_text(snapshot), font_small, MUTED)


def _needs_pairing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    _center_lines(draw, ["No printer", "selected"], 58, font_large, TEXT)
    _menu_item(draw, 124, "Find printer", selected=True, font=font_body)
    _text(draw, 18, 164, "Turn on printer first", font_small, MUTED)
    _text(draw, 18, 178, "Then press KEY1", font_small, MUTED)


def _settings(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_body: Font,
    font_small: Font,
) -> None:
    rows = snapshot.settings_rows
    if not rows:
        _text(draw, 18, 58, "No settings available", font_body, TEXT)
        return
    visible_count = 5
    selected = min(snapshot.selected_index, len(rows) - 1)
    selected_row = rows[selected]
    message = snapshot.settings_message or selected_row.hint or "Up/Dn move KEY1 OK KEY2 Back"
    position = f"{selected + 1}/{len(rows)}"
    position_width = _text_width(draw, position, font_small)
    position_x = 222 - position_width
    _text(
        draw,
        18,
        42,
        _fit_text_to_width(
            draw,
            _physical_control_text(message),
            font_small,
            max(0, position_x - 24),
        ),
        font_small,
        MUTED,
    )
    _text(draw, position_x, 42, position, font_small, MUTED)
    start = min(max(0, selected - 2), max(0, len(rows) - visible_count))
    for offset, row in enumerate(rows[start : start + visible_count]):
        index = start + offset
        y = 58 + offset * 27
        _settings_row(
            draw,
            y,
            row.label,
            row.value,
            row.hint,
            selected=index == selected,
            font=font_small,
        )


def _settings_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    value: str,
    hint: str,
    *,
    selected: bool,
    font: Font,
) -> None:
    fill = BLUE if selected else PANEL
    text_fill = TEXT if selected else MUTED
    draw.rounded_rectangle((14, y, 226, y + 24), radius=4, fill=fill)
    kind = _settings_row_kind(hint)
    marker, marker_fill = _settings_row_marker(kind, selected)
    draw.rounded_rectangle((16, y + 3, 20, y + 21), radius=2, fill=marker_fill)
    prefix = ">" if selected else " "
    _text(draw, 24, y + 5, f"{prefix} {_fit_text_to_width(draw, label, font, 94)}", font, text_fill)
    marker_width = _text_width(draw, marker, font) if marker else 0
    marker_x = 216 - marker_width
    if marker:
        _text(draw, marker_x, y + 5, marker, font, marker_fill)
    value_right = marker_x - 6 if marker else 216
    value_text = _fit_text_to_width(draw, value, font, max(0, value_right - 130))
    value_width = _text_width(draw, value_text, font)
    _text(draw, max(130, value_right - value_width), y + 5, value_text, font, text_fill)


def _pairing(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    _center_lines(draw, ["Finding", "printer"], 55, font_large, TEXT)
    _text(draw, 18, 130, "Keep printer awake", font_body, TEXT)
    _text(draw, 18, 152, "Close phone app if it fails", font_small, MUTED)
    _text(draw, 18, 169, "KEY2 cancels scan", font_small, MUTED)


def _pair_failed(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    _center_lines(draw, ["Find", "failed"], 50, font_large, TEXT)
    message = snapshot.message or "No INSTAX printer found"
    for index, line in enumerate(_wrap_words(message, 24)[:2]):
        _text(draw, 18, 128 + index * 17, line, font_small, TEXT)
    if len(_wrap_words(message, 24)) < 2:
        _text(draw, 18, 145, "Turn printer on first", font_small, MUTED)
    _menu_item(draw, 164, "Try again", selected=True, font=font_body)


def _error(
    draw: ImageDraw.ImageDraw,
    snapshot: UiSnapshot,
    font_large: Font,
    font_body: Font,
    font_small: Font,
) -> None:
    title, detail, hint = error_copy_for_message(snapshot.message)
    _center_lines(draw, _wrap_words(title, 16)[:2], 50, font_large, TEXT)
    for index, line in enumerate(_wrap_words(detail, 27)[:2]):
        _text(draw, 18, 128 + index * 17, line, font_small, TEXT)
    if hint is not None:
        _text(draw, 18, 167, _ellipsize(hint, 31), font_small, YELLOW)


def _footer(draw: ImageDraw.ImageDraw, snapshot: UiSnapshot, font: Font) -> None:
    draw.rectangle((0, 199, 239, 239), fill=PANEL)
    lines = _footer_label_lines(snapshot)
    y_positions = (213,) if len(lines) == 1 else (207, 225)
    for labels, y in zip(lines, y_positions, strict=True):
        _footer_label_row(draw, labels, y, font)


def _footer_label_lines(snapshot: UiSnapshot) -> tuple[tuple[str, str, str], ...]:
    if snapshot.mode is UiMode.BOOTING:
        return (("", "Starting", ""),)
    if snapshot.mode is UiMode.SETTINGS:
        return (
            ("Up/Dn", "KEY1 OK", "KEY3 Help"),
            ("Move", "Left Back", "KEY2 Back"),
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
            return (("KEY1 Settings", "Done", "KEY3 FTP"),)
        return (("KEY1 Settings", "Done", "Hold KEY3"),)
    if snapshot.paired_printer is not None:
        return (("KEY1 Settings", "KEY2 Refresh", "KEY3 FTP"),)
    return (("KEY1 Settings", "KEY2 Refresh", "Hold KEY3"),)


def _footer_label_row(
    draw: ImageDraw.ImageDraw,
    labels: tuple[str, str, str],
    y: int,
    font: Font,
) -> None:
    x_positions = (7, 80, 161)
    max_widths = (70, 76, 76)
    for x, label, max_width in zip(x_positions, labels, max_widths, strict=True):
        _text(draw, x, y, _fit_text_to_width(draw, label, font, max_width), font, MUTED)


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
        suffix = " charging" if snapshot.printer_is_charging else ""
        return f"Printer battery: {snapshot.printer_battery}%{suffix}"
    return snapshot.printer_status_message


def printer_compact_status_text(snapshot: UiSnapshot) -> str:
    """Return film and battery in one line for the small LCD."""

    if snapshot.printer_battery is None:
        return film_status_text(snapshot)
    return f"{film_status_text(snapshot)}  Printer battery: {snapshot.printer_battery}%"


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
        return "USB debug"
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
        return f"{film} {snapshot.printer_battery}%"
    return film


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
        snapshot.paired_printer is not None
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
        return "Bridge error", "Check logs", "KEY2 refreshes status"
    normalized = message.lower()
    if "pair printer first" in normalized or "select printer first" in normalized:
        return "No printer selected", "Open Printer settings", "KEY3 starts scan"
    if "printer offline" in normalized:
        return "Printer offline", "Turn printer on", "KEY2 refreshes status"
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
    return "Bridge error", message, "KEY2 refreshes status"


def ftp_mode_label(snapshot: UiSnapshot) -> str:
    """Return the current user-facing FTP receive mode."""

    if snapshot.camera_transport_message is not None:
        if snapshot.camera_transport_message.startswith(("Admin USB", "USB debug")):
            return "USB debug"
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
        return "USB debug connected"
    return "No FTP Wi-Fi"


def ftp_mode_hint_text(snapshot: UiSnapshot) -> str:
    """Return one short hint for the non-active receive modes."""

    if snapshot.hotspot_host is not None:
        return "Same Wi-Fi adv in Advanced"
    if snapshot.wifi_host is not None:
        return "Bridge Wi-Fi in Settings"
    if snapshot.usb_connected:
        return "USB debug in Network"
    return "Open Upload FTP setup"


def usb_ftp_status_text(snapshot: UiSnapshot) -> str:
    """Return the user-facing USB admin link status."""

    if (
        snapshot.camera_transport_message is not None
        and snapshot.camera_transport_message.startswith(("Admin USB", "USB debug"))
    ):
        return snapshot.camera_transport_message.replace("Admin USB", "USB debug")
    if snapshot.camera_receive_ready and snapshot.usb_connected:
        return f"USB debug {snapshot.ftp_host}"
    if snapshot.usb_connected:
        return "USB debug connected"
    return "USB debug off"


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
