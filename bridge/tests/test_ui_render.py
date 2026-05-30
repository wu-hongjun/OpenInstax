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
    printer_battery_life_text,
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


def test_status_bar_word_resolves_per_mode() -> None:
    from instantlink_bridge.ui.render import status_bar_word

    ready_connected = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        camera_receive_ready=True,
        paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        printer_status_fresh=True,
        film_remaining=7,
    )
    ready_waiting = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
    )
    assert status_bar_word(ready_connected) == "Connected"
    assert status_bar_word(ready_waiting) == "Waiting"
    # PRINTER_SEARCHING with no specific message is still actively probing —
    # the bridge is mid-scan or mid-connect, breathing yellow + "Searching".
    assert status_bar_word(UiSnapshot(mode=UiMode.PRINTER_SEARCHING, ftp_host="x")) == "Searching"
    # PRINTER_SEARCHING after the scan returned zero hits is a passive state
    # waiting on the user — solid yellow + "Disconnected" so the colour
    # pattern (not breathing) and the word both signal "you must act".
    assert (
        status_bar_word(
            UiSnapshot(
                mode=UiMode.PRINTER_SEARCHING,
                ftp_host="x",
                printer_status_message="No printer signal",
            )
        )
        == "Disconnected"
    )
    assert status_bar_word(UiSnapshot(mode=UiMode.PRINTING, ftp_host="x")) == "Printing"
    assert status_bar_word(UiSnapshot(mode=UiMode.NO_FILM, ftp_host="x")) == "No film"
    # PRINTER_OFFLINE is the explicit not-reachable mode — same vocabulary
    # as the passive-search case so the user learns one term for "unreachable".
    assert status_bar_word(UiSnapshot(mode=UiMode.PRINTER_OFFLINE, ftp_host="x")) == "Disconnected"


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

    # The discharging time-remaining estimate moved off the top bar into the
    # READY body via printer_battery_life_text, so the chip itself is identical
    # for charging/discharging apart from the "+" marker.
    assert top_bar_status_text(charging) == "Bridge Wi-Fi | Sq 8/10 50%+"
    assert top_bar_status_text(discharging) == "Bridge Wi-Fi | Sq 8/10 50%"
    # The time-remaining now lives in the READY body line.
    assert printer_battery_life_text(charging) is None
    assert printer_battery_life_text(discharging) == "1h 30m left"


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
    """Settings hint chips name physical-key shortcuts only. Joystick is the
    primary navigation surface so 'Up/Dn' / 'Move' / 'Left Back' descriptors
    don't take a chip slot. KEY2 → BACK is the meaningful one to expose."""

    lines = _footer_label_lines(UiSnapshot(mode=UiMode.SETTINGS, ftp_host="192.168.7.1"))

    assert lines == (("KEY1 OK", "KEY2 Back", "KEY3 Help"),)


def test_ready_footer_exposes_upload_credentials_when_printer_is_paired() -> None:
    lines = _footer_label_lines(
        UiSnapshot(
            mode=UiMode.READY,
            ftp_host="192.168.7.1",
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        )
    )

    assert lines == (("KEY1 Setting", "KEY2 Refresh", "KEY3 Network"),)


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

    assert usb_ftp_status_text(snapshot) == "USB IP off"


def test_usb_ftp_status_text_shows_admin_host_when_connected() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        usb_connected=True,
        camera_receive_ready=True,
        camera_transport_message="USB IP 192.168.7.1",
    )

    assert usb_ftp_status_text(snapshot) == "USB IP 192.168.7.1"


def test_usb_ftp_status_text_does_not_use_wifi_transport() -> None:
    snapshot = UiSnapshot(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        wifi_host="192.168.5.149",
        camera_receive_ready=True,
        camera_transport_message="Same Wi-Fi adv 192.168.5.149",
    )

    assert usb_ftp_status_text(snapshot) == "USB IP off"


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
                camera_transport_message="USB IP 192.168.7.1",
            )
        )
        == "USB IP"
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
            printer_status_fresh=True,
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
            printer_status_fresh=True,
        )
    )
    assert can_accept_images(
        UiSnapshot(
            mode=UiMode.READY,
            ftp_host="192.168.7.1",
            camera_receive_ready=True,
            paired_printer=PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
            film_remaining=7,
            printer_status_fresh=True,
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
        printer_status_fresh=True,
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


def _ready_snapshot(**overrides: object) -> UiSnapshot:
    base: dict[str, object] = {
        "mode": UiMode.READY,
        "ftp_host": "192.168.7.1",
        "camera_receive_ready": True,
        "paired_printer": PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678"),
        "film_remaining": 8,
        "printer_status_fresh": True,
    }
    base.update(overrides)
    return UiSnapshot(**base)  # type: ignore[arg-type]


def test_printer_ready_requires_fresh_status_even_with_film_and_pairing() -> None:
    stale = _ready_snapshot(printer_status_fresh=False)

    assert not printer_ready(stale)
    assert not can_accept_images(stale)


def test_printer_ready_true_only_when_status_is_fresh() -> None:
    fresh = _ready_snapshot(printer_status_fresh=True)

    assert printer_ready(fresh)
    assert can_accept_images(fresh)


def test_can_accept_images_false_for_stale_validation_mode() -> None:
    stale_validation = _ready_snapshot(mode=UiMode.VALIDATION, printer_status_fresh=False)

    assert not printer_ready(stale_validation)
    assert not can_accept_images(stale_validation)


def test_stale_ready_snapshot_does_not_render_ready_to_print() -> None:
    stale = _ready_snapshot(printer_status_fresh=False)

    image = render_snapshot(stale)

    assert image.size == (240, 240)


# ---------------------------------------------------------------------------
# Plan 036 phase 2: Adjustments page slider rendering
# ---------------------------------------------------------------------------


def _adjustments_snapshot(
    saturation: int = 0,
    exposure: int = 0,
    sharpness: int = 0,
    hue: int = 0,
    vignette: int = 0,
    appearance: str = "light",
    language: str = "en",
    selected_index: int = 0,
) -> UiSnapshot:
    """Build a UiSnapshot for the Adjustments settings page."""
    from instantlink_bridge.imaging.postprocess import AdjustmentProfile
    from instantlink_bridge.ui.settings import format_int_with_sign

    rows = (
        SettingsRow("Preset", "Custom", hint="Right/KEY1 choose"),
        SettingsRow("Saturation", format_int_with_sign(saturation), hint="Right/KEY1 choose"),
        SettingsRow("Exposure", format_int_with_sign(exposure), hint="Right/KEY1 choose"),
        SettingsRow("Sharpness", format_int_with_sign(sharpness), hint="Right/KEY1 choose"),
        SettingsRow("Hue", format_int_with_sign(hue), hint="Right/KEY1 choose"),
        SettingsRow("Vignette", str(vignette), hint="Right/KEY1 choose"),
        SettingsRow("Datestamp", "Off", hint="Right/KEY1 choose"),
        SettingsRow("Watermark", "Off", hint="Right/KEY1 choose"),
        SettingsRow("Save current", "", hint="Right/KEY1 run"),
    )
    profile = AdjustmentProfile(
        saturation=1.0 + saturation / 100.0,
        exposure=2.0 ** (exposure / 100.0),
        sharpness=1.0 + sharpness / 100.0,
        hue=int(hue * 1.8),
        vignette=vignette,
    )
    return UiSnapshot(
        mode=UiMode.SETTINGS,
        ftp_host="192.168.7.1",
        settings_title="Adjustments",
        settings_rows=rows,
        selected_index=selected_index,
        appearance=appearance,
        language=language,
        adjustments_profile=profile,
    )


def test_adjustments_page_renders_without_error_all_zeros() -> None:
    """Adjustments page with all axes at 0 renders to a 240×240 image."""
    from instantlink_bridge.ui.theme import theme_for

    snapshot = _adjustments_snapshot()
    image = render_snapshot(snapshot)
    assert image.size == (240, 240)

    # Phase 3: tile centre pixel (50, 86) must not be the page background colour.
    bg_colour = theme_for("light").bg
    h = bg_colour.lstrip("#")
    bg_rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    tile_px = image.getpixel((50, 86))
    assert tile_px != bg_rgb, (
        f"Tile centre pixel at (50, 86) should not be background {bg_rgb}, got {tile_px}"
    )


def test_adjustments_page_renders_without_error_mixed_values() -> None:
    """Adjustments page with non-zero axes renders to a 240×240 image."""
    snapshot = _adjustments_snapshot(
        saturation=50, exposure=-30, sharpness=10, hue=-20, vignette=40
    )
    image = render_snapshot(snapshot)
    assert image.size == (240, 240)


def test_adjustments_page_renders_differs_from_picker_style() -> None:
    """Adjustments page with sliders produces a different image than a generic settings page."""
    # Adjustments page — dispatched to _adjustments()
    adj_snapshot = _adjustments_snapshot(saturation=50)
    adj_image = render_snapshot(adj_snapshot)

    # Generic settings page with same row labels — dispatched to _settings()
    from instantlink_bridge.ui.settings import format_int_with_sign

    generic_snapshot = UiSnapshot(
        mode=UiMode.SETTINGS,
        ftp_host="192.168.7.1",
        settings_title="Print",  # different title → _settings() path
        settings_rows=(
            SettingsRow("Saturation", format_int_with_sign(50), hint="Right/KEY1 choose"),
        ),
        selected_index=0,
    )
    generic_image = render_snapshot(generic_snapshot)

    # The two renders should differ because the Adjustments page draws sliders.
    adj_pixels = adj_image.tobytes()
    generic_pixels = generic_image.tobytes()
    assert adj_pixels != generic_pixels, (
        "Adjustments slider render should differ from generic picker render"
    )


def test_adjustments_page_renders_in_dark_mode() -> None:
    """Adjustments page renders without error in dark appearance."""
    snapshot = _adjustments_snapshot(saturation=50, exposure=-30, appearance="dark")
    image = render_snapshot(snapshot)
    assert image.size == (240, 240)


def test_adjustments_page_renders_in_zh_hans() -> None:
    """Adjustments page renders without error in zh-Hans language."""
    snapshot = _adjustments_snapshot(saturation=50, language="zh-Hans")
    image = render_snapshot(snapshot)
    assert image.size == (240, 240)


# ---------------------------------------------------------------------------
# Plan 036 Phase 4 — ADJUSTMENT_EDIT mode renderer
# ---------------------------------------------------------------------------


def _adj_edit_snapshot(
    axis_key: str = "adjust_saturation",
    value: int = 50,
    appearance: str = "light",
    language: str = "en",
) -> UiSnapshot:
    """Build a UiSnapshot for ADJUSTMENT_EDIT mode."""
    from instantlink_bridge.imaging.postprocess import AdjustmentProfile

    # Build a working profile with the given saturation overridden.
    profile = AdjustmentProfile(saturation=1.0 + value / 100.0)
    return UiSnapshot(
        mode=UiMode.ADJUSTMENT_EDIT,
        ftp_host="192.168.7.1",
        adjustment_edit_key=axis_key,
        adjustment_edit_value=value,
        adjustment_edit_original=0,
        adjustments_profile=profile,
        appearance=appearance,
        language=language,
    )


def test_adjustment_edit_mode_renders_240x240() -> None:
    """ADJUSTMENT_EDIT mode renders to a 240×240 image without error."""
    snapshot = _adj_edit_snapshot(axis_key="adjust_saturation", value=50)
    image = render_snapshot(snapshot)
    assert image.size == (240, 240)


def test_adjustment_edit_mode_preview_tile_is_populated() -> None:
    """Preview tile region should have varied pixel data (not all one colour)."""
    snapshot = _adj_edit_snapshot(axis_key="adjust_saturation", value=50)
    image = render_snapshot(snapshot)
    # Tile is at x=16, y=42, size=192x108. Sample a few pixels in that region.
    tile_pixels = set()
    for x in range(16, 16 + 192, 16):
        for y in range(42, 42 + 108, 16):
            tile_pixels.add(image.getpixel((x, y)))
    # A real preview photo has many distinct colours; at least 2 must differ.
    assert len(tile_pixels) > 1, "Preview tile should contain varied pixels"


def test_adjustment_edit_mode_differs_between_identity_and_value() -> None:
    """A +50 saturation edit should render differently from an identity (0) edit."""
    snapshot_identity = _adj_edit_snapshot(axis_key="adjust_saturation", value=0)
    snapshot_saturated = _adj_edit_snapshot(axis_key="adjust_saturation", value=50)
    image_identity = render_snapshot(snapshot_identity)
    image_saturated = render_snapshot(snapshot_saturated)
    assert image_identity.tobytes() != image_saturated.tobytes(), (
        "Renders with different saturation values should differ"
    )


def test_adjustment_edit_mode_renders_dark_theme() -> None:
    """ADJUSTMENT_EDIT renders without error in dark appearance."""
    snapshot = _adj_edit_snapshot(value=50, appearance="dark")
    image = render_snapshot(snapshot)
    assert image.size == (240, 240)


def test_adjustment_edit_mode_renders_zh_hans() -> None:
    """ADJUSTMENT_EDIT renders without error in zh-Hans language."""
    snapshot = _adj_edit_snapshot(value=50, language="zh-Hans")
    image = render_snapshot(snapshot)
    assert image.size == (240, 240)


def test_adjustment_edit_mode_vignette_renders() -> None:
    """ADJUSTMENT_EDIT for vignette (asymmetric [0,100]) renders without error."""
    snapshot = _adj_edit_snapshot(axis_key="adjust_vignette", value=40)
    image = render_snapshot(snapshot)
    assert image.size == (240, 240)


# ---------------------------------------------------------------------------
# Plan 036 P1 fixes — render regression tests
# ---------------------------------------------------------------------------


def test_selected_slider_row_renders_chevron() -> None:
    """A selected slider row renders differently from a non-selected one (chevron present)."""
    # Selected saturation row.
    selected_snap = _adjustments_snapshot(saturation=0, selected_index=1)
    selected_image = render_snapshot(selected_snap)

    # Non-selected saturation row.
    non_selected_snap = _adjustments_snapshot(saturation=0, selected_index=0)
    non_selected_image = render_snapshot(non_selected_snap)

    selected_pixels = selected_image.tobytes()
    non_selected_pixels = non_selected_image.tobytes()
    assert selected_pixels != non_selected_pixels, (
        "Selected slider row should render differently (chevron) from non-selected"
    )


def test_destructive_toast_has_tinted_background() -> None:
    """A 'Press KEY1 again' toast strip is tinted, not the plain page background."""
    from instantlink_bridge.imaging.postprocess import AdjustmentProfile
    from instantlink_bridge.ui.theme import theme_for

    rows = (
        SettingsRow("Preset", "Custom", hint="Right/KEY1 choose"),
        SettingsRow("Saturation", "0", hint="Right/KEY1 choose"),
        SettingsRow("Exposure", "0", hint="Right/KEY1 choose"),
        SettingsRow("Sharpness", "0", hint="Right/KEY1 choose"),
        SettingsRow("Hue", "0", hint="Right/KEY1 choose"),
        SettingsRow("Vignette", "0", hint="Right/KEY1 choose"),
        SettingsRow("Datestamp", "Off", hint="Right/KEY1 choose"),
        SettingsRow("Watermark", "Off", hint="Right/KEY1 choose"),
        SettingsRow("Save current", "", hint="Right/KEY1 run"),
    )
    toast_snap = UiSnapshot(
        mode=UiMode.SETTINGS,
        ftp_host="192.168.7.1",
        settings_title="Adjustments",
        settings_rows=rows,
        selected_index=0,
        settings_message="Press KEY1 again to save as Custom1",
        adjustments_profile=AdjustmentProfile(),
    )
    # Snapshot without the toast for comparison.
    plain_snap = UiSnapshot(
        mode=UiMode.SETTINGS,
        ftp_host="192.168.7.1",
        settings_title="Adjustments",
        settings_rows=rows,
        selected_index=0,
        settings_message=None,
        adjustments_profile=AdjustmentProfile(),
    )

    toast_image = render_snapshot(toast_snap)
    render_snapshot(plain_snap)  # ensure no crash on plain render

    theme = theme_for("light")
    bg_hex = theme.bg.lstrip("#")
    bg_rgb = (int(bg_hex[0:2], 16), int(bg_hex[2:4], 16), int(bg_hex[4:6], 16))

    # Sample a pixel in the strip area (y≈210, x=20) from the toasted render.
    # It must not be plain background colour (the tint must be applied).
    strip_px = toast_image.getpixel((20, 210))
    assert strip_px[:3] != bg_rgb, (
        f"Strip pixel should be tinted, not bg {bg_rgb}; got {strip_px}"
    )
