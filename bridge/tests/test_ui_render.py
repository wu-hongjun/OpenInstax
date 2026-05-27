from __future__ import annotations

from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.ui.models import PairedPrinter, SettingsRow, UiMode, UiSnapshot
from instantlink_bridge.ui.render import (
    _fit_text_to_width,
    _font,
    _footer_label_lines,
    _physical_control_text,
    _settings_row_kind,
    _text_width,
    active_ftp_status_text,
    bridge_power_header_text,
    camera_link_text,
    can_accept_images,
    error_copy_for_message,
    film_status_text,
    ftp_mode_hint_text,
    ftp_mode_label,
    home_wifi_ftp_status_text,
    hotspot_ftp_status_text,
    printer_compact_status_text,
    printer_detail_text,
    printer_model_text,
    printer_readiness_text,
    printer_ready,
    readiness_cause_texts,
    render_snapshot,
    top_bar_status_text,
    usb_ftp_status_text,
    wifi_ftp_status_text,
    wifi_preference_mismatch,
)


def test_render_pairing_prompt_is_square_lcd_size() -> None:
    image = render_snapshot(UiSnapshot(mode=UiMode.NEEDS_PAIRING, ftp_host="192.168.7.1"))

    assert image.size == (240, 240)


def test_render_ready_screen_is_square_lcd_size() -> None:
    image = render_snapshot(
        UiSnapshot(
            mode=UiMode.READY,
            ftp_host="192.168.7.1",
            wifi_host="192.168.5.149",
            usb_connected=True,
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            printer_model=PrinterModel.SQUARE,
            film_remaining=8,
        )
    )

    assert image.size == (240, 240)


def test_render_validation_screen_is_square_lcd_size() -> None:
    image = render_snapshot(
        UiSnapshot(
            mode=UiMode.VALIDATION,
            ftp_host="192.168.7.1",
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            printer_status_message="Looking for printer",
        )
    )

    assert image.size == (240, 240)


def test_render_settings_screen_is_square_lcd_size() -> None:
    image = render_snapshot(
        UiSnapshot(
            mode=UiMode.SETTINGS,
            ftp_host="192.168.7.1",
            selected_index=2,
            settings_rows=(
                SettingsRow("Find printer", "INSTAX-1234"),
                SettingsRow("Connection", "Bridge Wi-Fi"),
                SettingsRow("Printer type", "Square"),
                SettingsRow("Image fit", "Crop"),
                SettingsRow("JPEG quality", "100"),
                SettingsRow("Auto print", "Off"),
            ),
        )
    )

    assert image.size == (240, 240)


def test_render_printer_offline_screen_is_square_lcd_size() -> None:
    image = render_snapshot(
        UiSnapshot(
            mode=UiMode.PRINTER_OFFLINE,
            ftp_host="192.168.7.1",
            wifi_host="192.168.5.149",
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            printer_model=PrinterModel.WIDE,
            printer_status_message="Turn printer on; retrying",
        )
    )

    assert image.size == (240, 240)


def test_render_printer_searching_screen_is_square_lcd_size() -> None:
    image = render_snapshot(
        UiSnapshot(
            mode=UiMode.PRINTER_SEARCHING,
            ftp_host="192.168.7.1",
            wifi_host="192.168.5.149",
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            printer_model=PrinterModel.MINI,
            printer_status_message="Looking for printer",
        )
    )

    assert image.size == (240, 240)


def test_render_print_flow_screens_are_square_lcd_size() -> None:
    for mode in (UiMode.AWAITING_CONFIRM, UiMode.PRINTING, UiMode.PRINT_COMPLETE):
        image = render_snapshot(
            UiSnapshot(
                mode=mode,
                ftp_host="192.168.7.1",
                usb_connected=True,
                last_image_name="DSC01234.JPG",
                paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
                printer_model=PrinterModel.SQUARE,
                film_remaining=8,
            )
        )

        assert image.size == (240, 240)


def test_render_preview_screen_with_image_is_square_lcd_size() -> None:
    from PIL import Image

    image = render_snapshot(
        UiSnapshot(
            mode=UiMode.AWAITING_CONFIRM,
            ftp_host="192.168.7.1",
            last_image_name="DSC01234.JPG",
            preview_image=Image.new("RGB", (100, 80), (20, 90, 160)),
            preview_tool="crop",
            preview_zoom=1.25,
            preview_rotation_degrees=90,
            print_title="Print in 5.0s",
            print_detail="Crop: joystick  K3 tool",
        )
    )

    assert image.size == (240, 240)


def test_render_print_progress_screen_is_square_lcd_size() -> None:
    image = render_snapshot(
        UiSnapshot(
            mode=UiMode.PRINTING,
            ftp_host="192.168.7.1",
            last_image_name="DSC01234.HIF",
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            printer_model=PrinterModel.MINI,
            print_title="Sending 45%",
            print_detail="52/115 chunks  100 KB",
            print_progress_percent=45,
        )
    )

    assert image.size == (240, 240)


def test_render_no_film_screen_is_square_lcd_size() -> None:
    image = render_snapshot(
        UiSnapshot(
            mode=UiMode.NO_FILM,
            ftp_host="192.168.7.1",
            wifi_host="192.168.5.149",
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            printer_model=PrinterModel.MINI,
            film_remaining=0,
        )
    )

    assert image.size == (240, 240)


def test_film_status_text_shows_remaining_pack_count() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        film_remaining=7,
    )

    assert film_status_text(snapshot) == "Film: 7/10"


def test_film_status_text_shows_unknown_when_unreadable() -> None:
    snapshot = UiSnapshot(mode=UiMode.READY, ftp_host="192.168.7.1")

    assert film_status_text(snapshot) == "Film: unknown"


def test_printer_detail_text_prefers_battery_when_available() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        printer_battery=81,
        printer_is_charging=True,
        printer_status_message="Printer offline",
    )

    assert printer_detail_text(snapshot) == "Printer battery: 81% charging"


def test_printer_detail_text_shows_battery_life_estimate_when_discharging() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        printer_battery=60,
        printer_is_charging=False,
        printer_battery_minutes_remaining=125,
    )

    assert printer_detail_text(snapshot) == "Printer battery: 60%  2h 5m left"


def test_printer_detail_text_omits_estimate_when_unknown() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        printer_battery=60,
        printer_is_charging=False,
        printer_battery_minutes_remaining=None,
    )

    assert printer_detail_text(snapshot) == "Printer battery: 60%"


def test_printer_detail_text_charging_takes_priority_over_estimate() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        printer_battery=60,
        printer_is_charging=True,
        printer_battery_minutes_remaining=125,
    )

    assert printer_detail_text(snapshot) == "Printer battery: 60% charging"


def test_top_bar_status_shows_charge_marker_and_estimate() -> None:
    charging = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        camera_receive_ready=True,
        camera_transport_message="Bridge FTP 192.168.8.1",
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        printer_model=PrinterModel.SQUARE,
        film_remaining=8,
        printer_battery=50,
        printer_is_charging=True,
    )
    discharging = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        camera_receive_ready=True,
        camera_transport_message="Bridge FTP 192.168.8.1",
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        printer_model=PrinterModel.SQUARE,
        film_remaining=8,
        printer_battery=50,
        printer_is_charging=False,
        printer_battery_minutes_remaining=90,
    )

    assert top_bar_status_text(charging) == "Bridge Wi-Fi | Sq 8/10 50%+"
    assert top_bar_status_text(discharging) == "Bridge Wi-Fi | Sq 8/10 50% 1h 30m"


def test_printer_compact_status_text_includes_battery_when_available() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        film_remaining=8,
        printer_battery=50,
    )

    assert printer_compact_status_text(snapshot) == "Film: 8/10  Printer battery: 50%"


def test_top_bar_status_consolidates_camera_printer_and_film() -> None:
    ready = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        camera_receive_ready=True,
        camera_transport_message="Bridge FTP 192.168.8.1",
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        printer_model=PrinterModel.SQUARE,
        film_remaining=8,
        printer_battery=50,
    )
    searching = UiSnapshot(
        mode=UiMode.PRINTER_SEARCHING,
        ftp_host="192.168.7.1",
        hotspot_host="192.168.8.1",
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        printer_status_message="No printer signal",
    )

    assert top_bar_status_text(ready) == "Bridge Wi-Fi | Sq 8/10 50%"
    assert top_bar_status_text(searching) == "Bridge Wi-Fi starting | No printer signal"


def test_bridge_power_header_text_hides_no_telemetry_or_shows_battery_status() -> None:
    x306 = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        bridge_power_status="Battery case",
    )
    warning = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        bridge_battery_percent=18,
        bridge_power_alert="warning",
    )

    assert bridge_power_header_text(x306) is None
    assert bridge_power_header_text(warning) == "Bridge low 18%"


def test_render_text_fit_shortens_to_pixel_width() -> None:
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (240, 240)))
    font = _font(11)

    fitted = _fit_text_to_width(
        draw,
        "KEY1/Right changes a very long settings row value",
        font,
        78,
    )

    assert _text_width(draw, fitted, font) <= 78
    assert fitted.endswith(".")


def test_visible_control_text_uses_physical_labels() -> None:
    assert _physical_control_text("R/K1 Open K2 Back K3 Help") == (
        "Right/KEY1 Open KEY2 Back KEY3 Help"
    )
    assert _physical_control_text("Crop: joystick  K3 tool") == ("Crop: 4-way pan  KEY3 tool")
    assert _physical_control_text("Move joystick") == "Move Up/Dn"


def test_settings_row_kind_is_inferred_from_hint() -> None:
    assert _settings_row_kind("Right/KEY1 open") == "open"
    assert _settings_row_kind("Right/KEY1 info") == "info"
    assert _settings_row_kind("Right/KEY1 run") == "run"
    assert _settings_row_kind("Right/KEY1 choose") == "choose"


def test_settings_footer_includes_key2_back() -> None:
    lines = _footer_label_lines(UiSnapshot(mode=UiMode.SETTINGS, ftp_host="192.168.7.1"))

    assert ("Move", "Left Back", "KEY2 Back") in lines


def test_ready_footer_exposes_upload_credentials_when_printer_is_paired() -> None:
    lines = _footer_label_lines(
        UiSnapshot(
            mode=UiMode.READY,
            ftp_host="192.168.7.1",
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        )
    )

    assert lines == (("KEY1 Settings", "KEY2 Refresh", "KEY3 FTP"),)


def test_settings_status_message_stays_in_settings_body_not_top_bar() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.SETTINGS,
        ftp_host="192.168.7.1",
        settings_message="Choose category",
    )

    assert top_bar_status_text(snapshot) is None


def test_pair_failed_footer_uses_retry_and_back_controls() -> None:
    lines = _footer_label_lines(UiSnapshot(mode=UiMode.PAIR_FAILED, ftp_host="192.168.7.1"))

    assert lines == (("KEY1 Retry", "KEY2 Back", "KEY3 Retry"),)


def test_crop_preview_footer_uses_all_direction_pan_hint() -> None:
    lines = _footer_label_lines(
        UiSnapshot(mode=UiMode.AWAITING_CONFIRM, ftp_host="192.168.7.1", preview_tool="crop")
    )

    assert lines == (("4-way Pan", "KEY1 Print", "KEY2 Cancel"),)


def test_printer_model_text_shows_detected_type() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        printer_model=PrinterModel.MINI_LINK3,
    )

    assert printer_model_text(snapshot) == "Type: Mini Link 3"


def test_printer_model_text_shows_detecting_when_unknown() -> None:
    snapshot = UiSnapshot(mode=UiMode.PRINTER_SEARCHING, ftp_host="192.168.7.1")

    assert printer_model_text(snapshot) == "Type: detecting"


def test_usb_ftp_status_text_reports_admin_usb_disconnected() -> None:
    snapshot = UiSnapshot(mode=UiMode.READY, ftp_host="192.168.7.1")

    assert usb_ftp_status_text(snapshot) == "USB debug off"


def test_usb_ftp_status_text_shows_admin_host_when_connected() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        usb_connected=True,
        camera_receive_ready=True,
        camera_transport_message="USB debug 192.168.7.1",
    )

    assert usb_ftp_status_text(snapshot) == "USB debug 192.168.7.1"


def test_usb_ftp_status_text_does_not_use_wifi_transport() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        wifi_host="192.168.5.149",
        camera_receive_ready=True,
        camera_transport_message="Same Wi-Fi adv 192.168.5.149",
    )

    assert usb_ftp_status_text(snapshot) == "USB debug off"


def test_hotspot_ftp_status_text_shows_ap_mode_state() -> None:
    active = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        hotspot_host="192.168.8.1",
        hotspot_ftp_host="192.168.8.1",
    )
    inactive = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        hotspot_ftp_host="192.168.8.1",
    )

    assert hotspot_ftp_status_text(active) == "Bridge FTP 192.168.8.1"
    assert hotspot_ftp_status_text(inactive) == "Bridge Wi-Fi off 192.168.8.1"


def test_ftp_hint_points_to_alternate_wifi_or_admin_usb() -> None:
    active_peer = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        wifi_host="192.168.5.149",
    )
    active_hotspot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        hotspot_host="192.168.8.1",
    )

    assert ftp_mode_hint_text(active_peer) == "Bridge Wi-Fi in Settings"
    assert ftp_mode_hint_text(active_hotspot) == "Same Wi-Fi adv in Advanced"
    assert (
        ftp_mode_hint_text(
            UiSnapshot(
                mode=UiMode.READY,
                ftp_host="192.168.7.1",
                wifi_host="192.168.5.149",
                usb_connected=True,
            )
        )
        == "Bridge Wi-Fi in Settings"
    )


def test_home_wifi_ftp_status_text_uses_actual_address_and_flags_preference_mismatch() -> None:
    matching = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        wifi_host="192.168.5.7",
        preferred_wifi_host="192.168.5.7",
    )
    mismatched = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        wifi_host="192.168.5.149",
        preferred_wifi_host="192.168.5.7",
    )
    disconnected = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        preferred_wifi_host="192.168.5.7",
    )

    assert home_wifi_ftp_status_text(matching) == "Same Wi-Fi adv 192.168.5.7"
    assert wifi_ftp_status_text(matching) == "Same Wi-Fi adv 192.168.5.7"
    assert not wifi_preference_mismatch(matching)
    assert home_wifi_ftp_status_text(mismatched) == "Same Wi-Fi adv 192.168.5.149"
    assert wifi_preference_mismatch(mismatched)
    assert home_wifi_ftp_status_text(disconnected) == "Same Wi-Fi adv prefer 192.168.5.7"
    assert not wifi_preference_mismatch(disconnected)


def test_ftp_mode_label_names_bridge_wifi_same_wifi_and_admin_usb() -> None:
    assert (
        ftp_mode_label(
            UiSnapshot(
                mode=UiMode.READY,
                ftp_host="192.168.7.1",
                camera_transport_message="USB debug 192.168.7.1",
            )
        )
        == "USB debug"
    )
    assert (
        ftp_mode_label(
            UiSnapshot(mode=UiMode.READY, ftp_host="192.168.7.1", hotspot_host="192.168.8.1")
        )
        == "Bridge Wi-Fi"
    )
    peer = UiSnapshot(mode=UiMode.READY, ftp_host="192.168.7.1", wifi_host="192.168.5.149")
    assert ftp_mode_label(peer) == "Same Wi-Fi adv"
    assert active_ftp_status_text(peer) == "Same Wi-Fi adv 192.168.5.149"
    assert (
        ftp_mode_label(UiSnapshot(mode=UiMode.VALIDATION, ftp_host="192.168.7.1")) == "No FTP Wi-Fi"
    )


def test_can_accept_images_requires_usb_or_wifi_path() -> None:
    assert not can_accept_images(UiSnapshot(mode=UiMode.READY, ftp_host="192.168.7.1"))
    assert not can_accept_images(
        UiSnapshot(mode=UiMode.READY, ftp_host="192.168.7.1", usb_connected=True)
    )
    assert not can_accept_images(
        UiSnapshot(
            mode=UiMode.NO_FILM,
            ftp_host="192.168.7.1",
            usb_connected=True,
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            film_remaining=0,
        )
    )
    assert can_accept_images(
        UiSnapshot(
            mode=UiMode.READY,
            ftp_host="192.168.7.1",
            camera_receive_ready=True,
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            film_remaining=0,
            allow_print_without_film=True,
        )
    )
    assert can_accept_images(
        UiSnapshot(
            mode=UiMode.READY,
            ftp_host="192.168.7.1",
            wifi_host="192.168.5.149",
            camera_receive_ready=True,
            camera_transport_message="Same Wi-Fi adv 192.168.5.149",
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            film_remaining=7,
        )
    )
    assert can_accept_images(
        UiSnapshot(
            mode=UiMode.READY,
            ftp_host="192.168.7.1",
            camera_receive_ready=True,
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            film_remaining=7,
        )
    )


def test_readiness_texts_show_camera_and_printer_state() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        usb_connected=True,
        camera_receive_ready=True,
        camera_transport_message="Bridge FTP 192.168.8.1",
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        film_remaining=6,
    )

    assert camera_link_text(snapshot) == "FTP: Bridge FTP 192.168.8.1"
    assert printer_readiness_text(snapshot) == "Printer: ready, Film 6/10"
    assert printer_ready(snapshot)
    assert readiness_cause_texts(snapshot) == []


def test_readiness_cause_texts_explain_not_ready_states() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        film_remaining=0,
    )

    assert camera_link_text(snapshot) == "FTP: no FTP Wi-Fi"
    assert printer_readiness_text(snapshot) == "Printer: no film"
    assert readiness_cause_texts(snapshot) == [
        "Choose FTP Wi-Fi",
        "Replace film pack",
    ]

    test_snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        camera_receive_ready=True,
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        film_remaining=0,
        allow_print_without_film=True,
    )
    assert printer_readiness_text(test_snapshot) == "Printer: test mode, Film 0/10"
    assert readiness_cause_texts(test_snapshot) == []


def test_readiness_texts_use_explicit_camera_health_messages() -> None:
    ready_snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        camera_receive_ready=True,
        camera_transport_message="Bridge FTP camera lease",
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        film_remaining=6,
    )
    blocked_snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        camera_status_message="Use playback FTP",
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        film_remaining=6,
    )

    assert camera_link_text(ready_snapshot) == "FTP: Bridge FTP camera lease"
    assert readiness_cause_texts(blocked_snapshot) == ["Use playback FTP"]


def test_printer_specific_error_copy_is_actionable() -> None:
    assert error_copy_for_message("Battery low") == (
        "Printer battery low",
        "Charge printer first",
        "Retry after charge",
    )
    assert error_copy_for_message("Cover open") == (
        "Cover open",
        "Close printer cover",
        "Retry when latched",
    )
    assert error_copy_for_message("Printer busy") == (
        "Printer busy",
        "Wait for Instax",
        "Retry in a moment",
    )
    assert error_copy_for_message("Printer type unknown") == (
        "Printer type unknown",
        "Set Printer type",
        "Settings > Printer",
    )
    assert error_copy_for_message("printer battery too low")[0] == "Printer battery low"
    assert error_copy_for_message("printer cover is open")[0] == "Cover open"
    assert error_copy_for_message("printer is busy")[0] == "Printer busy"
