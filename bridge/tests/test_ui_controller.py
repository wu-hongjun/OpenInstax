from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import pytest

from instantlink_bridge.ble.client import DiscoveredPrinter
from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.camera.ftp import ReceivedImage
from instantlink_bridge.config import (
    BridgeConfig,
    FtpConfig,
    FtpReceiveMode,
    PowerBackend,
    PowerConfig,
    PrinterConfig,
    WorkflowConfig,
    load_config,
)
from instantlink_bridge.imaging.pipeline import FitMode, ImagePipelineError, PrintEdit
from instantlink_bridge.net.health import (
    ConnectionHealth,
    DnsmasqLease,
    FtpActivity,
    build_connection_health,
)
from instantlink_bridge.power.battery_estimator import BatteryLifeEstimator
from instantlink_bridge.power.monitor import (
    BatteryAlert,
    IdleStage,
    IdleState,
    PowerEvent,
    PowerEventKind,
)
from instantlink_bridge.power.pisugar import BatteryState
from instantlink_bridge.printing import PrintProgress, PrintStage
from instantlink_bridge.system_info import SystemInfo
from instantlink_bridge.ui.controller import (
    OFFLINE_MESSAGE_AFTER_MISSES,
    RENDER_TICK_S,
    RESTART_PRINTER_RETRY_S,
    SILENT_LINK_RECOVERY_COOLDOWN_S,
    BridgeUi,
    _auto_rebond_key,
    _wifi_mode_for_ftp_receive_mode,
    bridge_power_status_text,
    camera_status_message_for_health,
    camera_transport_message_for_health,
    printer_unavailable_message,
)
from instantlink_bridge.ui.input import NullInput
from instantlink_bridge.ui.models import PairedPrinter, UiAction, UiMode, UiSnapshot
from instantlink_bridge.ui.render import can_accept_images
from instantlink_bridge.ui.settings import (
    HANDLED_SETTING_KEYS,
    SECTION_HEADER_KEYS,
    SETTING_HELP_TEXT,
    SETTINGS_BY_PAGE,
    SettingKey,
    SettingsPage,
    WifiMode,
)
from instantlink_bridge.ui.status import (
    PrinterStatusSnapshot,
    PrinterStatusUnavailableError,
    PrinterStatusUnavailableReason,
    scanner_diagnostics,
)


def test_camera_health_messages_report_no_receive_mode_for_auto_without_paths() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        usb_carrier=False,
        usb_ipv4_addresses=[],
        wifi_ipv4_addresses=[],
    )

    assert camera_status_message_for_health(health) == "No FTP Wi-Fi"
    assert camera_transport_message_for_health(health) == "No FTP Wi-Fi"


def test_camera_health_messages_report_usb_cable_warning_for_wired_mode() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        usb_carrier=False,
        usb_ipv4_addresses=[],
        wifi_ipv4_addresses=[],
    )

    assert camera_status_message_for_health(health, FtpReceiveMode.WIRED) == "USB IP off"
    assert camera_transport_message_for_health(health, FtpReceiveMode.WIRED) == "USB IP off"


def test_camera_health_messages_report_peer_subnet_conflict() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        expected_hotspot_ipv4="192.168.8.1",
        usb_carrier=False,
        usb_ipv4_addresses=[],
        wifi_ipv4_addresses=["192.168.7.42"],
    )

    assert camera_status_message_for_health(health) == "Same-Wi-Fi subnet conflict"
    assert camera_transport_message_for_health(health) == "Same-Wi-Fi subnet conflict"
    assert (
        camera_status_message_for_health(health, FtpReceiveMode.PEER)
        == "Same-Wi-Fi subnet conflict"
    )
    assert (
        camera_transport_message_for_health(health, FtpReceiveMode.PEER)
        == "Same-Wi-Fi subnet conflict"
    )


def test_camera_health_messages_report_usb_cable_warning() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        usb_carrier=True,
        usb_ipv4_addresses=[],
        wifi_ipv4_addresses=[],
    )

    assert camera_status_message_for_health(health) == "No FTP Wi-Fi"
    assert camera_transport_message_for_health(health) == "No FTP Wi-Fi"
    assert camera_status_message_for_health(health, FtpReceiveMode.WIRED) == "USB IP missing"
    assert camera_transport_message_for_health(health, FtpReceiveMode.WIRED) == "USB IP missing"


def test_camera_health_messages_auto_prefers_wireless_over_admin_usb() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        usb_carrier=True,
        usb_ipv4_addresses=["192.168.7.1"],
        wifi_ipv4_addresses=["192.168.5.149"],
        leases=[DnsmasqLease(1200, "aa:bb:cc:dd:ee:01", "192.168.7.10", "camera", None)],
    )

    assert camera_status_message_for_health(health) == "Same Wi-Fi adv ready"
    assert camera_transport_message_for_health(health) == "Same Wi-Fi adv 192.168.5.149"


def test_camera_health_messages_ignore_stale_wired_dhcp_without_link() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        usb_carrier=False,
        usb_ipv4_addresses=[],
        wifi_ipv4_addresses=[],
        leases=[DnsmasqLease(1200, "aa:bb:cc:dd:ee:01", "192.168.7.10", "camera", None)],
    )

    assert camera_status_message_for_health(health) == "No FTP Wi-Fi"
    assert camera_transport_message_for_health(health) == "No FTP Wi-Fi"


def test_camera_health_messages_report_recent_wireless_ftp_activity() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        usb_carrier=False,
        usb_ipv4_addresses=[],
        wifi_ipv4_addresses=["192.168.5.149"],
        ftp_activity=FtpActivity(last_connected_at=995, last_remote_ip="192.168.5.20"),
    )

    assert camera_status_message_for_health(health) == "FTP active 192.168.5.20"
    assert camera_transport_message_for_health(health) == "Same Wi-Fi adv 192.168.5.149"


def test_camera_health_messages_auto_ignores_admin_usb_when_wireless_is_ready() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        expected_hotspot_ipv4="192.168.8.1",
        usb_carrier=True,
        usb_ipv4_addresses=["192.168.7.1"],
        wifi_ipv4_addresses=["192.168.8.1", "192.168.5.149"],
        leases=[DnsmasqLease(1200, "aa:bb:cc:dd:ee:01", "192.168.7.10", "camera", None)],
        ftp_activity=FtpActivity(last_connected_at=995, last_remote_ip="192.168.5.20"),
    )

    assert camera_status_message_for_health(health) == "FTP active 192.168.5.20"
    assert camera_transport_message_for_health(health) == "Same Wi-Fi adv 192.168.5.149"


def test_camera_health_messages_report_hotspot_ftp_before_home_wifi() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        expected_hotspot_ipv4="192.168.8.1",
        usb_carrier=False,
        usb_ipv4_addresses=[],
        wifi_ipv4_addresses=["192.168.5.149", "192.168.8.1"],
    )

    assert camera_status_message_for_health(health) == "Bridge Wi-Fi ready"
    assert camera_transport_message_for_health(health) == "Bridge FTP 192.168.8.1"


def test_camera_health_messages_honor_selected_ftp_receive_mode() -> None:
    health = build_connection_health(
        checked_at=1000,
        expected_usb_ipv4="192.168.7.1",
        expected_hotspot_ipv4="192.168.8.1",
        usb_carrier=True,
        usb_ipv4_addresses=["192.168.7.1"],
        wifi_ipv4_addresses=["192.168.5.149"],
    )

    assert camera_status_message_for_health(health, FtpReceiveMode.WIRED) == "USB IP only"
    assert (
        camera_transport_message_for_health(health, FtpReceiveMode.PEER)
        == "Same Wi-Fi adv 192.168.5.149"
    )
    assert camera_status_message_for_health(health, FtpReceiveMode.HOTSPOT) == "Bridge Wi-Fi off"


def test_wired_ftp_mode_does_not_disable_wifi() -> None:
    assert _wifi_mode_for_ftp_receive_mode(FtpReceiveMode.WIRED) is None


def test_printer_unavailable_message_uses_scanner_diagnostics() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")

    no_candidates = PrinterStatusUnavailableError(
        "not advertising",
        diagnostics=scanner_diagnostics(selected, []),
    )
    other_instax = PrinterStatusUnavailableError(
        "not advertising",
        diagnostics=scanner_diagnostics(
            selected,
            [DiscoveredPrinter(address="AA:BB:CC:00:00:01", name="INSTAX-OTHER")],
        ),
    )
    visible = PrinterStatusUnavailableError(
        "not advertising",
        diagnostics=scanner_diagnostics(
            selected,
            [DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")],
        ),
    )

    assert printer_unavailable_message(no_candidates) == "No printer signal"
    assert printer_unavailable_message(other_instax) == "Saw other Instax"
    assert printer_unavailable_message(visible) == "Printer seen; connecting"


def test_bridge_power_status_text_handles_x306_no_telemetry() -> None:
    state = BatteryState(
        available=False,
        model="SupTronics X306 18650 UPS",
        error="no host telemetry",
    )

    assert bridge_power_status_text(state, BatteryAlert.UNAVAILABLE) == "Battery case"


def test_all_visible_settings_keys_are_explicitly_handled() -> None:
    visible_keys = {key for keys in SETTINGS_BY_PAGE.values() for key in keys}

    assert visible_keys <= HANDLED_SETTING_KEYS
    assert visible_keys <= set(SETTING_HELP_TEXT)


@pytest.mark.asyncio
async def test_await_print_confirmation_blocks_when_film_is_empty(tmp_path: Path) -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(model=PrinterModel.SQUARE)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(
        mode=UiMode.READY,
        paired_printer=PairedPrinter(address="AA:BB", name="INSTAX-1"),
        film_remaining=0,
    )

    edit = await ui.await_print_confirmation(
        ReceivedImage(tmp_path / "empty.jpg", "192.168.7.10"),
        timeout_s=0.01,
    )

    assert edit is None
    assert display.snapshots[-1].mode is UiMode.NO_FILM


@pytest.mark.asyncio
async def test_await_print_confirmation_can_ignore_empty_film(tmp_path: Path) -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(workflow=WorkflowConfig(allow_print_without_film=True)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(
        mode=UiMode.READY,
        paired_printer=PairedPrinter(address="AA:BB", name="INSTAX-1"),
        film_remaining=0,
    )

    edit = await ui.await_print_confirmation(
        ReceivedImage(tmp_path / "empty.jpg", "192.168.7.10"),
        timeout_s=0.0,
    )

    assert edit == PrintEdit()
    assert display.snapshots == []


@pytest.mark.asyncio
async def test_await_print_confirmation_zero_seconds_prints_without_preview(tmp_path: Path) -> None:
    image_path = tmp_path / "print.jpg"
    image_path.write_bytes(b"not opened for zero second mode")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(model=PrinterModel.SQUARE)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    edit = await ui.await_print_confirmation(
        ReceivedImage(image_path, "192.168.7.10"),
        timeout_s=0.0,
    )

    assert edit == PrintEdit()
    assert not any(snapshot.mode is UiMode.AWAITING_CONFIRM for snapshot in display.snapshots)


@pytest.mark.asyncio
async def test_preview_detects_and_persists_printer_model_before_prepare(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "wide.jpg"
    Image.new("RGB", (1800, 1200), (20, 90, 160)).save(image_path, format="JPEG")
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    pairer = _FakePairer([printer])
    status_provider = _FakeStatusProvider(
        snapshot=PrinterStatusSnapshot(
            film_remaining=7,
            battery=48,
            is_charging=False,
            model=PrinterModel.WIDE,
            message="Ready",
        )
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=pairer,
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.READY, paired_printer=printer)

    task = asyncio.create_task(
        ui.await_print_confirmation(
            ReceivedImage(image_path, "192.168.8.59"),
            timeout_s=None,
        )
    )
    await _wait_for_preview_image(display)

    assert status_provider.fetch_calls == 1
    assert ui._snapshot.printer_model is PrinterModel.WIDE
    assert ui._snapshot.paired_printer is not None
    assert ui._snapshot.paired_printer.model is PrinterModel.WIDE
    assert pairer.saved_selected == replace(printer, model=PrinterModel.WIDE)

    await ui._handle_action(UiAction.SELECT)
    assert await task == PrintEdit()


@pytest.mark.asyncio
async def test_preview_reports_offline_when_model_detection_cannot_reach_printer(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "offline.jpg"
    image_path.write_bytes(b"not opened when printer is offline")
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        error=PrinterStatusUnavailableError(
            "not advertising",
            diagnostics=scanner_diagnostics(printer, []),
        )
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.READY, paired_printer=printer)

    with pytest.raises(ImagePipelineError, match="printer offline"):
        await ui.await_print_confirmation(
            ReceivedImage(image_path, "192.168.8.59"),
            timeout_s=None,
        )


@pytest.mark.asyncio
async def test_manual_preview_allows_zoom_rotate_and_print(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "print.jpg"
    Image.new("RGB", (1200, 900), (20, 90, 160)).save(image_path, format="JPEG")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(model=PrinterModel.SQUARE)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    task = asyncio.create_task(
        ui.await_print_confirmation(
            ReceivedImage(image_path, "192.168.7.10"),
            timeout_s=None,
        )
    )
    await _wait_for_mode(display, UiMode.AWAITING_CONFIRM)
    await _wait_for_preview_image(display)

    assert display.snapshots[-1].mode is UiMode.AWAITING_CONFIRM
    assert display.snapshots[-1].preview_image is not None

    await ui._handle_action(UiAction.UP)
    await ui._handle_action(UiAction.HELP)
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.HELP)
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.SELECT)

    edit = await task

    assert edit == PrintEdit(rotate_degrees=90, zoom=1.25, offset_x=0.2, offset_y=0.0)
    assert display.snapshots[-1].preview_tool == "rotate"


@pytest.mark.asyncio
async def test_manual_preview_can_cancel(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "print.jpg"
    Image.new("RGB", (1200, 900), (20, 90, 160)).save(image_path, format="JPEG")
    pairer = _FakePairer([])
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(model=PrinterModel.SQUARE)),
        display=display,
        input_device=NullInput(),
        pairer=pairer,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    task = asyncio.create_task(
        ui.await_print_confirmation(
            ReceivedImage(image_path, "192.168.7.10"),
            timeout_s=None,
        )
    )
    await _wait_for_mode(display, UiMode.AWAITING_CONFIRM)
    await ui._handle_action(UiAction.BACK)

    assert await task is None
    assert _latest_mode(display) is UiMode.NEEDS_PAIRING
    assert pairer.list_calls == 0


@pytest.mark.parametrize("rebuild_fails", [False, True])
@pytest.mark.asyncio
async def test_late_preview_rebuild_cannot_overwrite_printing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rebuild_fails: bool,
) -> None:
    from PIL import Image

    received = ReceivedImage(tmp_path / "print.jpg", "192.168.7.10")
    initial_image = Image.new("RGB", (100, 80), (20, 90, 160))
    stale_image = Image.new("RGB", (100, 80), (230, 40, 40))
    rebuild_started = asyncio.Event()
    release_rebuild = asyncio.Event()

    async def build_preview(
        _received: ReceivedImage,
        edit: PrintEdit,
    ) -> Image.Image | None:
        if edit == PrintEdit():
            return initial_image
        rebuild_started.set()
        await release_rebuild.wait()
        if rebuild_fails:
            raise ImagePipelineError("late failure")
        return stale_image

    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(model=PrinterModel.SQUARE)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    monkeypatch.setattr(ui, "_build_preview_image", build_preview)

    task = asyncio.create_task(ui.await_print_confirmation(received, timeout_s=0.1))
    await _wait_for_preview_image(display)
    action_task = asyncio.create_task(ui._handle_action(UiAction.UP))
    await asyncio.wait_for(rebuild_started.wait(), timeout=0.5)

    assert await asyncio.wait_for(task, timeout=0.5) == PrintEdit(zoom=1.25)

    await ui.printing_started(received)
    release_rebuild.set()
    await asyncio.wait_for(action_task, timeout=0.5)

    assert display.snapshots[-1].mode is UiMode.PRINTING
    assert display.snapshots[-1].preview_image is None
    assert display.snapshots[-1].message != "Preview failed"


@pytest.mark.asyncio
async def test_late_preview_rebuild_cannot_overwrite_newer_preview(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from PIL import Image

    first_received = ReceivedImage(tmp_path / "first.jpg", "192.168.7.10")
    second_received = ReceivedImage(tmp_path / "second.jpg", "192.168.7.10")
    first_image = Image.new("RGB", (100, 80), (20, 90, 160))
    second_image = Image.new("RGB", (100, 80), (20, 160, 80))
    stale_image = Image.new("RGB", (100, 80), (230, 40, 40))
    old_rebuild_started = asyncio.Event()
    release_old_rebuild = asyncio.Event()

    async def build_preview(
        received: ReceivedImage,
        edit: PrintEdit,
    ) -> Image.Image | None:
        if received == first_received and edit == PrintEdit():
            return first_image
        if received == first_received:
            old_rebuild_started.set()
            await release_old_rebuild.wait()
            return stale_image
        if received == second_received:
            return second_image
        raise AssertionError(f"unexpected preview build for {received.path}")

    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(model=PrinterModel.SQUARE)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    monkeypatch.setattr(ui, "_build_preview_image", build_preview)

    first_task = asyncio.create_task(ui.await_print_confirmation(first_received, timeout_s=0.1))
    await _wait_for_preview_image(display)
    old_action_task = asyncio.create_task(ui._handle_action(UiAction.UP))
    await asyncio.wait_for(old_rebuild_started.wait(), timeout=0.5)

    assert await asyncio.wait_for(first_task, timeout=0.5) == PrintEdit(zoom=1.25)

    second_task = asyncio.create_task(ui.await_print_confirmation(second_received, timeout_s=None))
    await _wait_for_preview_image_name(display, "second.jpg")
    assert display.snapshots[-1].preview_image is second_image

    release_old_rebuild.set()
    await asyncio.wait_for(old_action_task, timeout=0.5)

    assert display.snapshots[-1].last_image_name == "second.jpg"
    assert display.snapshots[-1].preview_image is second_image

    await ui._handle_action(UiAction.SELECT)
    assert await asyncio.wait_for(second_task, timeout=0.5) == PrintEdit()


@pytest.mark.asyncio
async def test_manual_preview_does_not_redraw_while_idle(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "print.jpg"
    Image.new("RGB", (1200, 900), (20, 90, 160)).save(image_path, format="JPEG")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(model=PrinterModel.SQUARE)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    task = asyncio.create_task(
        ui.await_print_confirmation(
            ReceivedImage(image_path, "192.168.7.10"),
            timeout_s=None,
        )
    )
    await _wait_for_mode(display, UiMode.AWAITING_CONFIRM)
    await _wait_for_preview_image(display)
    preview_render_count = len(display.snapshots)

    await asyncio.sleep(0.4)
    await ui._handle_action(UiAction.SELECT)

    assert await task == PrintEdit()
    assert len(display.snapshots) == preview_render_count


def test_background_printer_search_does_not_interrupt_preview() -> None:
    from PIL import Image

    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.AWAITING_CONFIRM, paired_printer=printer)
    ui._snapshot = replace(
        ui._snapshot,
        preview_image=Image.new("RGB", (100, 80), (20, 90, 160)),
        print_title="Preview",
    )

    ui._apply_printer_searching(printer, "No printer signal")

    assert ui._snapshot.mode is UiMode.AWAITING_CONFIRM
    assert ui._snapshot.printer_status_message == "No printer signal"
    assert display.snapshots == []


def test_background_printer_status_does_not_redraw_preview() -> None:
    from PIL import Image

    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.AWAITING_CONFIRM, paired_printer=printer)
    ui._snapshot = replace(
        ui._snapshot,
        preview_image=Image.new("RGB", (100, 80), (20, 90, 160)),
        print_title="Preview",
    )

    ui._apply_printer_status(
        printer,
        PrinterStatusSnapshot(
            film_remaining=8,
            battery=35,
            is_charging=False,
            model=None,
            message="Ready",
        ),
    )

    assert ui._snapshot.mode is UiMode.AWAITING_CONFIRM
    assert ui._snapshot.film_remaining == 8
    assert ui._snapshot.printer_battery == 35
    assert display.snapshots == []


@pytest.mark.asyncio
async def test_background_network_status_does_not_redraw_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    def fake_detect_camera_link_health(**_kwargs: object) -> ConnectionHealth:
        return build_connection_health(
            checked_at=1000,
            expected_usb_ipv4="192.168.7.1",
            usb_carrier=False,
            usb_ipv4_addresses=[],
            wifi_ipv4_addresses=["192.168.5.149"],
        )

    monkeypatch.setattr(
        "instantlink_bridge.ui.controller.detect_camera_link_health",
        fake_detect_camera_link_health,
    )
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.AWAITING_CONFIRM, paired_printer=printer)
    ui._snapshot = replace(
        ui._snapshot,
        preview_image=Image.new("RGB", (100, 80), (20, 90, 160)),
        print_title="Preview",
    )

    await ui._refresh_network_status()

    assert ui._snapshot.mode is UiMode.AWAITING_CONFIRM
    assert ui._snapshot.wifi_host == "192.168.5.149"
    assert display.snapshots == []


@pytest.mark.asyncio
async def test_print_progress_updates_lcd_snapshot() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTING)

    await ui.print_progress(PrintProgress(PrintStage.SENDING, "Sending 45%", "52/115 chunks", 45))

    assert display.snapshots[-1].print_title == "Sending 45%"
    assert display.snapshots[-1].print_detail == "52/115 chunks"
    assert display.snapshots[-1].print_progress_percent == 45


@pytest.mark.asyncio
async def test_print_failed_keeps_error_modal_visible() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTING)

    await ui.print_failed("Printer offline")

    assert display.snapshots[-1].mode is UiMode.ERROR
    assert display.snapshots[-1].message == "Printer offline"


@pytest.mark.asyncio
async def test_print_complete_resumes_printer_status_before_return_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("instantlink_bridge.ui.controller.RETURN_HOME_DELAY_S", 0.01)
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        snapshot=PrinterStatusSnapshot(
            film_remaining=7,
            battery=35,
            is_charging=False,
            model=None,
            message="Ready",
        )
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._camera_receive_ready = True
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTING, paired_printer=printer)

    await ui.print_complete(ReceivedImage(tmp_path / "print.jpg", "192.168.7.10"))
    await asyncio.wait_for(status_provider.fetch_started.wait(), timeout=1)
    await asyncio.sleep(0.02)

    assert ui._status_task is not None
    assert display.snapshots[-1].mode is UiMode.READY
    assert display.snapshots[-1].film_remaining == 7
    await ui.pause_printer_status()

    ui._cancel_image_reset()
    await ui._cancel_status_refresh()


def test_printer_status_with_unknown_film_shows_validation_not_searching() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)

    ui._apply_printer_status(
        printer,
        PrinterStatusSnapshot(
            film_remaining=None,
            battery=35,
            is_charging=False,
            model=None,
            message="Checking film",
        ),
    )

    assert display.snapshots[-1].mode is UiMode.VALIDATION
    assert display.snapshots[-1].printer_status_message == "Checking film"


@pytest.mark.asyncio
async def test_status_poll_feeds_battery_estimator_and_publishes_estimate() -> None:
    # A successful status sample must drive the battery estimator and surface a smoothed
    # minutes-remaining value on the rendered snapshot once enough samples accumulate.
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    clock = {"now": 0.0}
    snapshots = iter(
        [
            PrinterStatusSnapshot(
                film_remaining=8,
                battery=battery,
                is_charging=False,
                model=PrinterModel.SQUARE,
            )
            # 60% draining ~10%/hour over six 5-minute ticks.
            for battery in (60, 59, 59, 58, 57, 57)
        ]
    )

    class _DrainProvider:
        def __init__(self) -> None:
            self.fetch_calls = 0

        async def fetch(self, _printer: PairedPrinter) -> PrinterStatusSnapshot:
            self.fetch_calls += 1
            return next(snapshots)

        async def close(self) -> None:
            return None

    provider = _DrainProvider()
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._battery_estimator = BatteryLifeEstimator(
        clock=lambda: clock["now"],
        min_samples=3,
        min_window_s=120.0,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)

    for _ in range(6):
        assert await ui._refresh_printer_status_in_background(printer)
        clock["now"] += 300.0

    assert provider.fetch_calls == 6
    assert ui._snapshot.printer_battery == 57
    assert ui._snapshot.printer_is_charging is False
    minutes = ui._snapshot.printer_battery_minutes_remaining
    assert minutes is not None
    # ~57% / ~10%/h ~= 5.7h ~= 340 min; allow wide slack for fit + smoothing.
    assert 200 <= minutes <= 600


@pytest.mark.asyncio
async def test_status_poll_reports_no_estimate_while_charging() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        snapshot=PrinterStatusSnapshot(
            film_remaining=8,
            battery=40,
            is_charging=True,
            model=PrinterModel.SQUARE,
        )
    )
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)

    assert await ui._refresh_printer_status_in_background(printer)

    assert ui._snapshot.printer_is_charging is True
    assert ui._snapshot.printer_battery_minutes_remaining is None


@pytest.mark.asyncio
async def test_repeated_absent_printer_status_keeps_auto_searching() -> None:
    # A printer that is simply not advertising (but not yet classified stale) must keep
    # auto-scanning in PRINTER_SEARCHING rather than flipping to the manual re-pair screen.
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        error=PrinterStatusUnavailableError(
            "not advertising",
            diagnostics=scanner_diagnostics(printer, []),
        )
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)

    for _ in range(OFFLINE_MESSAGE_AFTER_MISSES):
        assert not await ui._refresh_printer_status_in_background(printer)

    assert display.snapshots[-1].mode is UiMode.PRINTER_SEARCHING
    assert display.snapshots[-1].printer_status_message == "No printer signal"
    # No backoff: the offline retry stays at the configured search period even past the threshold.
    assert ui._printer_status_retry_delay(False) == ui._config.printer.search_interval_s

    # Even far past the threshold an absent (non-stale) printer keeps auto-searching.
    for _ in range(20):
        assert not await ui._refresh_printer_status_in_background(printer)
    assert display.snapshots[-1].mode is UiMode.PRINTER_SEARCHING


@pytest.mark.asyncio
async def test_stale_selected_printer_offers_manual_repair() -> None:
    # A selected printer that scans confirm is gone (stale) is the one case re-pair applies:
    # escalate to the manual PRINTER_OFFLINE affordance once past the miss threshold.
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        error=PrinterStatusUnavailableError(
            "stale or powered off",
            diagnostics=scanner_diagnostics(printer, []),
            reason=PrinterStatusUnavailableReason.STALE_SELECTED,
        )
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)

    for _ in range(OFFLINE_MESSAGE_AFTER_MISSES):
        assert not await ui._refresh_printer_status_in_background(printer)

    assert display.snapshots[-1].mode is UiMode.PRINTER_OFFLINE
    assert display.snapshots[-1].printer_status_message == "Hold K3 to re-pair"


@pytest.mark.asyncio
async def test_cancel_status_refresh_does_not_block_on_in_flight_worker() -> None:
    # A status fetch that never returns models the shielded, cancel-resistant BLE worker.
    # _cancel_status_refresh must return immediately rather than awaiting that worker, otherwise
    # every input would park the action loop for seconds.
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _BlockingStatusProvider()
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)

    await ui._schedule_printer_status_refresh()
    poll_task = ui._status_task
    assert poll_task is not None
    await asyncio.wait_for(status_provider.fetch_started.wait(), timeout=1)

    await asyncio.wait_for(ui._cancel_status_refresh(), timeout=0.2)

    # The reference is dropped without awaiting and the worker is told to stop ignoring results.
    assert ui._status_task is None
    assert not status_provider.released.is_set()

    # Let the cancelled poll task tear itself down (its finally closes the provider).
    status_provider.release()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(poll_task, timeout=1)
    assert status_provider.close_calls >= 1


@pytest.mark.asyncio
async def test_searching_text_keeps_updating_across_repeated_misses() -> None:
    # The live "searching/connecting" copy must keep refreshing on every attempt so the LCD never
    # freezes on a stale frame while auto-scanning continues.
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(error=TimeoutError("connect timed out"))
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)

    messages: list[str | None] = []
    for _ in range(OFFLINE_MESSAGE_AFTER_MISSES):
        assert not await ui._refresh_printer_status_in_background(printer)
        ui._show_printer_searching_if_retrying(printer, "Looking for printer")
        messages.append(ui._snapshot.printer_status_message)

    # Each attempt stays in PRINTER_SEARCHING and surfaces live copy (connecting -> restart),
    # never the manual re-pair screen.
    assert all(mode is UiMode.PRINTER_SEARCHING for mode in (display.snapshots[-1].mode,))
    assert messages[0] == "Printer seen; connecting"
    assert messages[-1] == "Restart printer"
    assert "Hold K3 to re-pair" not in messages


@pytest.mark.asyncio
async def test_render_tick_re_renders_latest_snapshot() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=_FakeStatusProvider(),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    tick_task = asyncio.create_task(ui._run_render_tick())
    try:
        # Mutate the snapshot without calling _render; the tick must converge the LCD to it.
        # PRINTER_OFFLINE is a SOLID indicator (no breath) so the short-circuit applies; the
        # complementary breathing case is asserted by
        # test_render_tick_keeps_animating_breathing_indicator below.
        ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_OFFLINE, paired_printer=printer)
        for _ in range(50):
            if display.snapshots and display.snapshots[-1].mode is UiMode.PRINTER_OFFLINE:
                break
            await asyncio.sleep(0.01)
        assert display.snapshots, "render tick never rendered"
        assert display.snapshots[-1].mode is UiMode.PRINTER_OFFLINE
        render_count = len(display.snapshots)
        # An unchanged snapshot must not be re-rendered (short-circuit keeps the tick cheap).
        await asyncio.sleep(RENDER_TICK_S * 2)
        assert len(display.snapshots) == render_count
    finally:
        tick_task.cancel()
        with suppress(asyncio.CancelledError):
            await tick_task


@pytest.mark.asyncio
async def test_render_tick_keeps_animating_breathing_indicator() -> None:
    """When the status indicator is breathing, the tick must re-render so the
    time-modulated tint advances even though the snapshot itself is identical."""

    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=_FakeStatusProvider(),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    tick_task = asyncio.create_task(ui._run_render_tick())
    try:
        # PRINTER_SEARCHING resolves to a breathing yellow indicator.
        ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)
        for _ in range(50):
            if display.snapshots and display.snapshots[-1].mode is UiMode.PRINTER_SEARCHING:
                break
            await asyncio.sleep(0.01)
        assert display.snapshots, "render tick never rendered the breathing state"
        baseline = len(display.snapshots)
        # Two more tick periods must produce at least one extra render — the breath
        # curve advances even though the snapshot is bit-identical.
        await asyncio.sleep(RENDER_TICK_S * 3)
        assert len(display.snapshots) > baseline
    finally:
        tick_task.cancel()
        with suppress(asyncio.CancelledError):
            await tick_task


def test_offline_status_retry_delay_uses_configured_search_period_without_backoff() -> None:
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(search_interval_s=5.0)),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([]),
        status_provider=_FakeStatusProvider(),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    # The retry delay is the total cadence PERIOD (the poll loop subtracts each attempt's elapsed
    # time). It equals the configured search interval with no backoff, regardless of miss count.
    for misses in (0, OFFLINE_MESSAGE_AFTER_MISSES, OFFLINE_MESSAGE_AFTER_MISSES + 50):
        ui._printer_status_misses = misses
        assert ui._printer_status_retry_delay(False) == 5.0

    # Changing the configured search period applies live.
    for period in (15.0, 30.0, 60.0):
        ui._config = replace(
            ui._config, printer=replace(ui._config.printer, search_interval_s=period)
        )
        assert ui._printer_status_retry_delay(False) == period


def test_retry_delay_is_immediate_right_after_a_connected_drop() -> None:
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(search_interval_s=30.0)),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([]),
        status_provider=_FakeStatusProvider(),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    # Just dropped from a connected state: re-search immediately, not after the 30s period.
    ui._printer_was_online = True
    assert ui._printer_status_retry_delay(False) == 0.0

    # Once searching (no longer "was online"), fall back to the configured search period.
    ui._printer_was_online = False
    assert ui._printer_status_retry_delay(False) == 30.0


def test_offline_status_retry_delay_preserves_restart_special_case() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=_FakeStatusProvider(),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(
        mode=UiMode.PRINTER_SEARCHING,
        paired_printer=printer,
        printer_status_message="Restart printer",
    )
    ui._printer_status_misses = OFFLINE_MESSAGE_AFTER_MISSES + 20

    # Restart-printer recovery copy keeps its dedicated cadence regardless of miss count.
    assert ui._printer_status_retry_delay(False) == RESTART_PRINTER_RETRY_S


@pytest.mark.asyncio
async def test_repeated_unavailable_printer_status_rate_limits_warning_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        error=PrinterStatusUnavailableError(
            "not advertising",
            diagnostics=scanner_diagnostics(printer, []),
        )
    )
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)
    caplog.set_level(logging.WARNING, logger="instantlink_bridge.ui.controller")

    for _ in range(3):
        assert not await ui._refresh_printer_status_in_background(printer)

    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("ui.printer_status_unavailable")
    ]
    assert len(warning_messages) == 1


@pytest.mark.asyncio
async def test_repeated_visible_printer_connect_failures_ask_for_restart() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(error=RuntimeError("service discovery failed"))
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)

    for _ in range(3):
        assert not await ui._refresh_printer_status_in_background(printer)

    assert display.snapshots[-1].mode is UiMode.PRINTER_SEARCHING
    assert display.snapshots[-1].printer_status_message == "Restart printer"
    assert ui._printer_status_retry_delay(False) == 5.0


def test_printer_search_retry_keeps_specific_diagnostic_message() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(
        mode=UiMode.PRINTER_SEARCHING,
        paired_printer=printer,
        printer_status_message="No printer signal",
    )

    ui._show_printer_searching_if_retrying(printer, "Looking for printer")

    assert display.snapshots == []
    assert ui._snapshot.printer_status_message == "No printer signal"


@pytest.mark.asyncio
async def test_settings_menu_persists_adjusted_image_fit(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[printer]\nfit = "crop"\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 0 DOWNs lands on Print; SELECT enters the Print hub.
    await ui._handle_action(UiAction.SELECT)
    # Print hub: 0 Printer  1 Adjustments  2 Transform  3 Auto print.
    # Navigate into Transform (row 2) where Image fit lives.
    # Option B: navigate directly to Transform sub-page for row content test.
    ui._show_settings(page=SettingsPage.TRANSFORM)
    # TRANSFORM: 0 Image fit  1 JPEG quality.
    # 0 DOWNs lands on Image fit (already selected at index 0).
    await ui._handle_action(UiAction.RIGHT)
    assert _printer_fit(ui) is FitMode.CROP
    assert display.snapshots[-1].settings_title == "Image fit"
    assert [row.label for row in display.snapshots[-1].settings_rows] == [
        "Auto",
        "Crop",
        "Contain",
        "Stretch",
    ]
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.SELECT)

    assert _printer_fit(ui) is FitMode.CONTAIN
    assert load_config(config_path).printer.fit is FitMode.CONTAIN
    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].settings_message == "Saved"


@pytest.mark.asyncio
async def test_settings_save_failure_keeps_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_write_config(_config: BridgeConfig, _path: Path) -> None:
        raise PermissionError("read-only config directory")

    monkeypatch.setattr("instantlink_bridge.ui.controller.write_config", fail_write_config)

    config_path = tmp_path / "config.toml"
    config_path.write_text("[printer]\nkeepalive_interval_s = 10\n", encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    # Option B: navigate directly to AUTO_PRINT sub-page; this test's intent
    # is that a config-write failure keeps the runtime value intact, not that
    # the navigation path to Keepalive is exercised.
    ui._show_settings(page=SettingsPage.AUTO_PRINT)
    # AUTO_PRINT: 0 Auto print  1 No-film test  2 Advanced  3 Keepalive  4 Search rate.
    # Three DOWN presses from Auto print lands on Keepalive (index 3).
    for _ in range(3):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.SELECT)

    assert ui.config.printer.keepalive_interval_s == 10.0
    assert load_config(config_path).printer.keepalive_interval_s == 10.0
    assert display.snapshots[-1].settings_message == "Config not writable"
    assert display.snapshots[-1].settings_rows[3].label == "Keepalive"
    assert display.snapshots[-1].settings_rows[3].value == "10s"


@pytest.mark.asyncio
async def test_printer_keepalive_setting_configures_status_provider() -> None:
    status_provider = _FakeStatusProvider()
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(printer=PrinterConfig(keepalive_interval_s=10)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._configure_printer_keepalive()
    updated = replace(
        ui.config,
        printer=replace(ui.config.printer, keepalive_interval_s=15),
    )
    assert await ui._set_config(updated, message="Saved")

    assert status_provider.keepalive_interval_calls == [10.0, 15.0]


@pytest.mark.asyncio
async def test_upload_ftp_help_describes_sender_wifi() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 1 DOWN lands on Network; SELECT enters it.
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    # NETWORK row 0 = Wi-Fi Mode; row 1 = SSID ("Bridge Wi-Fi name to join from camera").
    # 1 DOWN moves to SSID before pressing HELP.
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.HELP)

    assert display.snapshots[-1].settings_title == "Network"
    assert display.snapshots[-1].settings_message == "Bridge Wi-Fi name to join from camera"


@pytest.mark.asyncio
async def test_settings_ftp_receive_mode_selects_bridge_wifi_from_advanced_mode() -> None:
    calls: list[WifiMode] = []
    applied_ftp_configs: list[FtpConfig] = []

    async def set_wifi_mode(mode: WifiMode) -> str:
        calls.append(mode)
        return "ok"

    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(ftp=FtpConfig(mode=FtpReceiveMode.WIRED)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=set_wifi_mode,
        ftp_config_applied_callback=applied_ftp_configs.append,
    )

    await ui._handle_action(UiAction.SELECT)

    # MAIN: Print (0), Network (1), System (2). Accessibility folded into
    # System after plan 035 phase 1 follow-up — Appearance / Text size /
    # Language now sit under System next to the bridge-state rows.
    assert [row.label for row in display.snapshots[-1].settings_rows] == [
        "Print",
        "Network",
        "System",
    ]

    # 1 DOWN lands on Network; SELECT enters it.
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    # NETWORK page row order: 0 Camera link  1 SSID  2 Wi-Fi PIN  3 FTP host
    #   4 FTP user  5 FTP PIN  6 Bluetooth  7 Same Wi-Fi adv  8 USB IP
    #   9 Reset credentials.
    # Camera link is at row 0; RIGHT opens the picker immediately.
    await ui._handle_action(UiAction.RIGHT)

    assert calls == []
    assert _ftp_mode(ui) is FtpReceiveMode.WIRED
    # Picker title now matches the row label ("Camera link", per plan 037
    # polish #8), and the options use the shorter mode names so the user
    # doesn't read both "Bridge Wi-Fi" AND a hotspot/client distinction.
    assert display.snapshots[-1].settings_title == "Camera link"
    assert [row.label for row in display.snapshots[-1].settings_rows] == [
        "Hotspot",
        "Client",
    ]
    await ui._handle_action(UiAction.SELECT)

    assert calls == [WifiMode.HOTSPOT]
    assert _ftp_mode(ui) is FtpReceiveMode.HOTSPOT
    assert display.snapshots[-1].settings_title == "Network"
    assert display.snapshots[-1].settings_rows[1].label == "SSID"
    assert display.snapshots[-1].settings_rows[3].label == "FTP host"
    assert display.snapshots[-1].settings_rows[3].value == "192.168.8.1"
    assert display.snapshots[-1].settings_rows[4].label == "FTP user"
    assert display.snapshots[-1].settings_rows[5].label == "FTP PIN"
    assert display.snapshots[-1].settings_rows[0].label == "Camera link"
    # The "Bridge Wi-Fi ready" status string lives in the runtime-health
    # transport-message channel and is intentionally NOT renamed alongside
    # the picker/row labels — it describes a network state, not the menu.
    assert display.snapshots[-1].settings_message == "Bridge Wi-Fi ready"
    assert [config.mode for config in applied_ftp_configs] == [FtpReceiveMode.HOTSPOT]


@pytest.mark.asyncio
async def test_settings_rows_show_action_specific_hints() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)

    assert display.snapshots[-1].settings_rows[0].hint == "Right/KEY1 open"

    # Option B: navigate directly to AUTO_PRINT sub-page to check
    # Keepalive's hint — the test intent is the hint value, not the path.
    # AUTO_PRINT: 0 Auto print  1 No-film test  2 Advanced  3 Keepalive  4 Search rate.
    ui._show_settings(page=SettingsPage.AUTO_PRINT)
    # Three DOWN presses lands on Keepalive (index 3, an adjustable picker row).
    for _ in range(3):
        await ui._handle_action(UiAction.DOWN)

    assert display.snapshots[-1].settings_rows[3].label == "Keepalive"
    assert display.snapshots[-1].settings_rows[3].hint == "Right/KEY1 choose"


@pytest.mark.asyncio
async def test_settings_main_page_uses_stable_category_prompt() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)

    assert display.snapshots[-1].settings_title == "Settings"
    assert display.snapshots[-1].settings_message is None
    assert all(row.value == "" for row in display.snapshots[-1].settings_rows)
    # MAIN row 0 = Print; help text describes pairing and print options.
    assert display.snapshots[-1].settings_rows[0].help == "Pairing and photo/print options"

    await ui._handle_action(UiAction.DOWN)

    # MAIN row 1 = Network.
    assert display.snapshots[-1].settings_rows[display.snapshots[-1].selected_index].label == (
        "Network"
    )
    assert display.snapshots[-1].settings_message is None
    assert display.snapshots[-1].settings_rows[1].help == "Wi-Fi, FTP credentials, Bluetooth, USB"

    await ui._handle_action(UiAction.HELP)

    assert display.snapshots[-1].settings_message == "KEY1 opens category"


@pytest.mark.asyncio
async def test_settings_main_help_message_uses_physical_controls() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.HELP)

    assert display.snapshots[-1].settings_message == "KEY1 opens category"


@pytest.mark.asyncio
async def test_key3_help_explains_selected_settings_row() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 0 DOWNs; SELECT enters Print (row 0).
    await ui._handle_action(UiAction.SELECT)
    # Option B: navigate directly to TRANSFORM sub-page to verify JPEG quality
    # help text — the test intent is the help content, not the navigation path.
    # TRANSFORM: 0 Image fit  1 JPEG quality.
    ui._show_settings(page=SettingsPage.TRANSFORM)
    # 1 DOWN lands on JPEG quality (index 1).
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.HELP)

    assert display.snapshots[-1].settings_title == "Transform"
    assert display.snapshots[-1].settings_rows[1].label == "JPEG quality"
    assert display.snapshots[-1].settings_message == (
        "Trade-off: higher = bigger, sharper. Current: 100"
    )


@pytest.mark.asyncio
async def test_key3_hold_in_settings_shows_help_not_pairing() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.PAIR)

    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].settings_message == "KEY1 opens category"


@pytest.mark.asyncio
async def test_settings_left_backs_out_without_activating_selected_row() -> None:
    calls: list[WifiMode] = []

    async def set_wifi_mode(mode: WifiMode) -> str:
        calls.append(mode)
        return "ok"

    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=set_wifi_mode,
    )

    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.LEFT)

    assert calls == []
    assert display.snapshots[-1].mode is UiMode.NEEDS_PAIRING


@pytest.mark.asyncio
async def test_settings_left_returns_from_subpage_to_main() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 2 DOWNs lands on System; SELECT enters it; LEFT returns to MAIN.
    for _ in range(2):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.LEFT)

    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].settings_title == "Settings"
    # MAIN row 0 = Print.
    assert display.snapshots[-1].settings_rows[0].label == "Print"
    assert display.snapshots[-1].settings_message is None
    assert display.snapshots[-1].settings_rows[0].help == "Pairing and photo/print options"


@pytest.mark.asyncio
async def test_settings_menu_shows_ftp_credentials() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(ftp=FtpConfig(username="ib", password="12345678")),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 1 DOWN lands on Network; SELECT enters it.
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    # NETWORK: 0 Wi-Fi Mode  1 SSID  2 Wi-Fi PIN  3 FTP host  4 FTP user  5 FTP PIN ...
    rows = display.snapshots[-1].settings_rows
    assert rows[4].label == "FTP user"
    assert rows[4].value == "ib"
    assert rows[5].label == "FTP PIN"
    assert rows[5].value == "12345678"


@pytest.mark.asyncio
async def test_settings_menu_shows_hotspot_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ssid_path = tmp_path / "hotspot.ssid"
    psk_path = tmp_path / "hotspot.psk"
    ssid_path.write_text("InstantLink-T123\n", encoding="utf-8")
    psk_path.write_text("12345678\n", encoding="utf-8")
    monkeypatch.setenv("INSTANTLINK_BRIDGE_HOTSPOT_SSID_FILE", str(ssid_path))
    monkeypatch.setenv("INSTANTLINK_BRIDGE_HOTSPOT_PSK_FILE", str(psk_path))

    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 1 DOWN lands on Network; SELECT enters it.
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    # NETWORK: 0 Wi-Fi Mode  1 SSID  2 Wi-Fi PIN  3 FTP host ...
    rows = display.snapshots[-1].settings_rows
    assert rows[1].label == "SSID"
    assert rows[1].value == "InstantLink-T123"
    assert rows[2].label == "Wi-Fi PIN"
    assert rows[2].value == "12345678"
    assert rows[3].label == "FTP host"
    assert rows[3].value == "192.168.8.1"


@pytest.mark.asyncio
async def test_settings_print_page_can_toggle_no_film_test() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 0 DOWNs; SELECT enters Print (row 0).
    await ui._handle_action(UiAction.SELECT)
    # Option B: navigate directly to AUTO_PRINT sub-page to toggle
    # No-film test — the test intent is toggling the value, not the path.
    # AUTO_PRINT: 0 Auto print  1 No-film test  2 Advanced  3 Keepalive  4 Search rate.
    ui._show_settings(page=SettingsPage.AUTO_PRINT)
    # 1 DOWN lands on No-film test (index 1).
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.RIGHT)
    assert not ui.config.workflow.allow_print_without_film
    assert display.snapshots[-1].settings_title == "No-film test"
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.SELECT)

    assert ui.config.workflow.allow_print_without_film
    assert display.snapshots[-1].settings_rows[1].label == "No-film test"
    assert display.snapshots[-1].settings_rows[1].value == "On"


@pytest.mark.asyncio
async def test_settings_network_page_shows_connection_info() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._usb_connected = True
    ui._camera_transport_message = "USB IP 192.168.7.1"
    ui._wifi_host = "192.168.5.149"
    ui._hotspot_host = "192.168.8.1"
    ui._snapshot = ui._build_snapshot(
        mode=UiMode.READY,
        paired_printer=PairedPrinter(
            address="AA:BB:CC:DD:EE:FF",
            name="INSTAX-12345678",
        ),
        film_remaining=3,
    )

    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 1 DOWN lands on Network; SELECT enters it.
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    # NETWORK: 0 Wi-Fi Mode  1 SSID  2 Wi-Fi PIN  3 FTP host  4 FTP user
    #   5 FTP PIN  6 Diagnostics  7 Bluetooth  8 Same Wi-Fi adv  9 USB IP
    #   10 Reset credentials. The Diagnostics divider at index 6 separates
    # the camera-setup credentials block from the read-only status rows
    # (plan 034 item 9).
    rows = display.snapshots[-1].settings_rows
    assert rows[7].label == "Bluetooth"
    assert rows[7].value == "connected"
    assert rows[8].label == "Same Wi-Fi adv"
    assert rows[8].value == "192.168.5.149"
    assert rows[9].label == "USB IP"
    assert rows[9].value == "192.168.7.1"


@pytest.mark.asyncio
async def test_settings_network_page_reports_admin_usb_without_camera_wording() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._usb_connected = True
    ui._camera_transport_message = "USB IP missing"

    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 1 DOWN lands on Network; SELECT enters it.
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    # NETWORK (with Diagnostics divider at 6): 0 Wi-Fi Mode  1 SSID
    #   2 Wi-Fi PIN  3 FTP host  4 FTP user  5 FTP PIN  6 Diagnostics
    #   7 Bluetooth  8 Same Wi-Fi adv  9 USB IP  10 Reset credentials.
    rows = display.snapshots[-1].settings_rows
    assert rows[9].label == "USB IP"
    assert rows[9].value == "no IP"


@pytest.mark.asyncio
async def test_settings_system_refresh_stays_in_settings() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 2 DOWNs lands on System; SELECT enters it.
    await ui._handle_action(UiAction.SELECT)
    for _ in range(2):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    snapshot_count = len(display.snapshots)
    while display.snapshots[-1].settings_rows[display.snapshots[-1].selected_index].label != (
        "Refresh status"
    ):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].settings_title == "System"
    assert display.snapshots[-1].settings_message == "No printer saved"
    assert all(snapshot.mode is UiMode.SETTINGS for snapshot in display.snapshots[snapshot_count:])


@pytest.mark.asyncio
async def test_settings_printer_reset_ble_link_stays_in_printer_settings() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        snapshot=PrinterStatusSnapshot(
            film_remaining=3,
            battery=90,
            is_charging=False,
            model=PrinterModel.SQUARE,
        )
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(
        mode=UiMode.READY,
        paired_printer=printer,
        film_remaining=2,
        printer_battery=88,
    )

    # Option A: exercise the full navigation path because this test's value
    # is in verifying the nav chain MAIN → Print hub → Printer → Reconnect.
    await ui._handle_action(UiAction.SELECT)
    # MAIN: Print (row 0). SELECT enters the Print hub.
    await ui._handle_action(UiAction.SELECT)
    # Print hub: Printer (row 0). SELECT enters the Printer sub-page.
    await ui._handle_action(UiAction.SELECT)
    # PRINTER (paired): 0 Serial  1 Pair/Re-pair  2 Reconnect  3 Forget  4 Printer type.
    # Two DOWN presses lands on Reconnect (RESET_PRINTER_LINK, index 2).
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.DOWN)
    # Reset BLE is now a two-press destructive confirm; the first SELECT only
    # primes the confirmation toast, the second actually closes the cached
    # session and schedules a fresh poll.
    await ui._handle_action(UiAction.SELECT)
    assert status_provider.close_cached_calls == 0
    assert display.snapshots[-1].settings_message == "Press KEY1 again to RESET BLE link"
    await ui._handle_action(UiAction.SELECT)
    await asyncio.sleep(0)

    assert status_provider.close_cached_calls == 1
    assert ui._status_task is not None
    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].settings_title == "Printer"
    assert display.snapshots[-1].settings_message == "BLE link reset"
    assert display.snapshots[-1].paired_printer == replace(printer, model=PrinterModel.SQUARE)
    await ui._cancel_status_refresh()


@pytest.mark.asyncio
async def test_settings_about_page_shows_device_and_versions() -> None:
    """Versions (Python/BlueZ/OS) + identity rows now live behind System →
    About so the System page itself stays operational. This test exercises
    the navigation chain and asserts the About page presents the expected
    rows in order, plus a BACK from About returns to System (not MAIN).
    """

    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
        system_info=SystemInfo(
            device_id="IB-1234ABCD",
            app_version="0.1.0",
            python_version="3.11.9",
            bluez_version="5.82",
            os_version="Debian GNU/Linux 13 (trixie)",
        ),
    )

    # MAIN: Print (0), Network (1), System (2).
    # 2 DOWNs lands on System; SELECT enters the System page.
    await ui._handle_action(UiAction.SELECT)
    for _ in range(2):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    assert display.snapshots[-1].settings_title == "System"

    # System rows after plan 037 phase 1 (X306 backend default — Battery row
    # hidden, Idle row dropped, Personalisation divider non-selectable):
    # 0 Idle poweroff  1 Refresh status  [skip 2 Personalisation header]
    # 3 Appearance  4 Text size  5 Language  6 About. Five DOWNs lands on
    # About (header is skipped, so the visited indices are 1,3,4,5,6).
    for _ in range(5):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    assert display.snapshots[-1].settings_title == "About"

    rows = display.snapshots[-1].settings_rows
    assert [row.label for row in rows[:5]] == [
        "Device ID",
        "App version",
        "Python",
        "BlueZ",
        "OS",
    ]
    assert rows[0].value == "IB-1234ABCD"
    assert rows[1].value == "0.1.0"
    assert rows[2].value == "3.11.9"
    assert rows[3].value == "5.82"

    await ui._handle_action(UiAction.HELP)
    assert display.snapshots[-1].settings_message == "Unique ID; used by the desktop app"

    # BACK from About goes to its parent (System), not all the way to MAIN.
    await ui._handle_action(UiAction.BACK)
    assert display.snapshots[-1].settings_title == "System"


@pytest.mark.asyncio
async def test_settings_system_page_can_toggle_idle_poweroff(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[power]\nidle_poweroff_enabled = false\n", encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    # MAIN: Print (0), Network (1), System (2), Accessibility (3).
    # 2 DOWNs lands on System; SELECT enters it.
    await ui._handle_action(UiAction.SELECT)
    for _ in range(2):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    while display.snapshots[-1].settings_rows[display.snapshots[-1].selected_index].label != (
        "Idle poweroff"
    ):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.SELECT)

    assert ui.config.power.idle_poweroff_enabled
    assert load_config(config_path).power.idle_poweroff_enabled
    # Plan 037 phase 1, #2: the row value now reads "After 10 min" / "Off"
    # rather than the generic On/Off picker label so it tells the user how
    # long the idle window actually is.
    assert display.snapshots[-1].settings_rows[display.snapshots[-1].selected_index].value == (
        "After 10 min"
    )


@pytest.mark.asyncio
async def test_forget_printer_requires_second_confirmation() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    pairer = _FakePairer([printer])
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=pairer,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.READY, paired_printer=printer)

    # MAIN → Print hub → Printer sub-page (plan 035 phase 1).
    # Print hub rows: 0 Printer  1 Adjustments  2 Transform  3 Auto print.
    # SELECT enters Print hub, second SELECT enters the Printer sub-page
    # (row 0), which holds the pairing rows that used to live on the flat
    # Print page.
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.SELECT)
    # PRINTER sub-page (paired): 0 SERIAL  1 RE-PAIR  2 RECONNECT  3 FORGET
    #                            4 PRINTER TYPE. Three DOWNs lands on FORGET.
    for _ in range(3):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.RIGHT)

    assert not pairer.forgot
    # Confirm wording is now explicit about which key + which verb so the
    # user knows K1 is the destructive press.
    assert display.snapshots[-1].settings_message == "Press KEY1 again to FORGET printer"

    await ui._handle_action(UiAction.RIGHT)

    assert pairer.forgot
    assert display.snapshots[-1].settings_message == "Printer forgotten"
    assert display.snapshots[-1].paired_printer is None


@pytest.mark.asyncio
async def test_forget_and_repair_confirms_then_forgets_and_starts_scan() -> None:
    """The state-aware "Re-pair" action is the atomic recovery flow.

    PAIR_PRINTER renders as "Pair" when nothing is saved and "Re-pair" when
    a printer is bonded; the latter routes through the destructive
    Forget+scan confirm that used to live on its own FORGET_AND_REPAIR row.
    First SELECT primes the destructive confirm toast; second SELECT both
    wipes the saved printer (and BlueZ bond) AND kicks off a fresh pairing
    scan in one shot so the user lands in the picker without re-navigating
    back into Settings.
    """

    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    pairer = _FakePairer([printer])
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=pairer,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.READY, paired_printer=printer)

    # MAIN → Print hub → Printer sub-page: 0 Serial  1 Re-pair  ...
    # Three SELECTs drill in (open Settings, open Print hub, open Printer
    # sub-page). One DOWN past Serial lands on Re-pair (the consolidated
    # state-aware pair/re-pair row).
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    # First press just primes the confirm — neither destructive step runs yet.
    assert not pairer.forgot
    assert display.snapshots[-1].settings_message == "Press KEY1 again to FORGET and re-pair"

    await ui._handle_action(UiAction.SELECT)

    # Second press atomically forgets and enters the pairing flow.
    assert pairer.forgot
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTING)

    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.BACK)
    await ui._handle_action(UiAction.PAIR)

    assert display.snapshots == []
    assert ui._snapshot.mode is UiMode.PRINTING


@pytest.mark.asyncio
async def test_stale_pairing_result_is_ignored() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._pairing_generation = 2
    ui._snapshot = ui._build_snapshot(mode=UiMode.PAIRING)

    await ui._pair_in_background(generation=1, previous_printer=None)

    assert display.snapshots == []
    assert ui._snapshot.mode is UiMode.PAIRING


@pytest.mark.asyncio
async def test_network_refresh_does_not_record_power_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def record_activity() -> None:
        nonlocal calls
        calls += 1

    def fake_detect_camera_link_health(**_kwargs: object) -> ConnectionHealth:
        return build_connection_health(
            checked_at=1000,
            expected_usb_ipv4="192.168.7.1",
            usb_carrier=True,
            usb_ipv4_addresses=["192.168.7.1"],
            wifi_ipv4_addresses=[],
        )

    monkeypatch.setattr(
        "instantlink_bridge.ui.controller.detect_camera_link_health",
        fake_detect_camera_link_health,
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
        power_activity_callback=record_activity,
    )

    await ui._refresh_network_status()

    assert calls == 0


@pytest.mark.asyncio
async def test_bluetooth_settings_do_not_claim_connected_while_searching() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(
        mode=UiMode.PRINTER_SEARCHING,
        paired_printer=printer,
        film_remaining=8,
        printer_status_message="No printer signal",
    )

    ui._show_settings(page=SettingsPage.NETWORK)

    # NETWORK (with Diagnostics divider at 6): 0 Wi-Fi Mode  1 SSID
    #   2 Wi-Fi PIN  3 FTP host  4 FTP user  5 FTP PIN  6 Diagnostics
    #   7 Bluetooth  8 Same Wi-Fi adv  9 USB IP  10 Reset credentials.
    assert display.snapshots[-1].settings_rows[7].label == "Bluetooth"
    assert display.snapshots[-1].settings_rows[7].value == "searching"


@pytest.mark.asyncio
async def test_power_events_update_settings_and_display_idle_stage() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.SYSTEM)

    await ui.apply_power_event(
        PowerEvent(
            kind=PowerEventKind.BATTERY_UPDATE,
            created_at_monotonic=1.0,
            battery=BatteryState(
                available=False,
                model="SupTronics X306 18650 UPS",
                error="no host telemetry",
            ),
            battery_alert=BatteryAlert.UNAVAILABLE,
        )
    )
    await ui.apply_power_event(
        PowerEvent(
            kind=PowerEventKind.IDLE_STAGE_CHANGED,
            created_at_monotonic=2.0,
            idle_state=IdleState(
                stage=IdleStage.SCREEN_OFF,
                idle_seconds=90.0,
                last_activity_monotonic=0.0,
            ),
        )
    )

    assert display.snapshots[-1].settings_title == "System"
    # The Power row was removed: with no host telemetry on the X306, every
    # power source ("Battery case", USB-C, pogo, data USB) collapsed to the
    # same string. Battery (charge %) and Idle stay; Power should be gone.
    assert all(row.label != "Power" for row in display.snapshots[-1].settings_rows)
    assert display.idle_stages == ["screen_off"]


@pytest.mark.asyncio
async def test_pair_failure_keeps_existing_printer_selection() -> None:
    selected = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    pairer = _FakePairer([])
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=pairer,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PAIRING, paired_printer=selected)

    await ui._pair_in_background()

    assert not pairer.forgot
    assert display.snapshots[-1].mode is UiMode.PAIR_FAILED
    assert display.snapshots[-1].paired_printer == selected


@pytest.mark.asyncio
async def test_key3_press_on_pair_failed_starts_pairing() -> None:
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PAIR_FAILED)

    await ui._handle_action(UiAction.HELP)

    assert display.snapshots[-1].mode is UiMode.PAIRING


@pytest.mark.asyncio
async def test_key3_press_with_paired_printer_opens_upload_ftp_credentials() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._hotspot_host = "192.168.8.1"
    ui._snapshot = ui._build_snapshot(
        mode=UiMode.READY,
        paired_printer=printer,
        film_remaining=8,
    )

    await ui._handle_action(UiAction.HELP)

    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].settings_title == "Network"
    assert display.snapshots[-1].settings_message == "Wi-Fi + FTP credentials"
    # NETWORK: 0 Wi-Fi Mode  1 SSID  2 Wi-Fi PIN  3 FTP host  4 FTP user  5 FTP PIN ...
    assert [row.label for row in display.snapshots[-1].settings_rows[1:6]] == [
        "SSID",
        "Wi-Fi PIN",
        "FTP host",
        "FTP user",
        "FTP PIN",
    ]


@pytest.mark.asyncio
async def test_first_printer_pairing_opens_upload_ftp_credentials() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=_FakeStatusProvider(
            snapshot=PrinterStatusSnapshot(
                film_remaining=8,
                battery=90,
                is_charging=False,
                model=PrinterModel.SQUARE,
                message="Ready",
            )
        ),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PAIRING)

    await ui._pair_in_background(previous_printer=None)

    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].settings_title == "Network"
    assert display.snapshots[-1].settings_message == "Enter these on sender"
    await ui._cancel_status_refresh()


def test_background_printer_search_does_not_leave_settings() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)
    ui._show_settings()

    ui._apply_printer_searching(printer, "No printer signal")

    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].printer_status_message == "No printer signal"


def test_background_printer_status_does_not_leave_settings() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)
    ui._show_settings()

    ui._apply_printer_status(
        printer,
        PrinterStatusSnapshot(
            film_remaining=8,
            battery=35,
            is_charging=False,
            model=None,
            message="Ready",
        ),
    )

    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].film_remaining == 8


def test_empty_film_test_mode_keeps_ready_status() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(workflow=WorkflowConfig(allow_print_without_film=True)),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)

    ui._apply_printer_status(
        printer,
        PrinterStatusSnapshot(
            film_remaining=0,
            battery=35,
            is_charging=False,
            model=None,
            message="No film",
        ),
    )

    assert display.snapshots[-1].mode is UiMode.READY
    assert display.snapshots[-1].allow_print_without_film


def _stale_bond_error(printer: PairedPrinter) -> PrinterStatusUnavailableError:
    return PrinterStatusUnavailableError(
        "printer status unavailable",
        diagnostics=scanner_diagnostics(printer, []),
        reason=PrinterStatusUnavailableReason.NOT_ADVERTISING,
        status_message="Retrying printer",
        stale_bond_suspected=True,
    )


def _not_found_error(printer: PairedPrinter) -> PrinterStatusUnavailableError:
    return PrinterStatusUnavailableError(
        "printer is not advertising",
        diagnostics=scanner_diagnostics(printer, []),
        reason=PrinterStatusUnavailableReason.NOT_ADVERTISING,
        status_message="Turn printer on",
        stale_bond_suspected=False,
    )


def _silent_link_not_found_error(printer: PairedPrinter) -> PrinterStatusUnavailableError:
    """A not-found failure from the FFI advertisement scan (the silent-link deadlock case)."""

    return PrinterStatusUnavailableError(
        "printer is not advertising",
        diagnostics=scanner_diagnostics(printer, []),
        reason=PrinterStatusUnavailableReason.NOT_ADVERTISING,
        status_message="Turn printer on",
        printer_not_found=True,
    )


async def _drain_silent_link_recovery(ui: BridgeUi) -> None:
    task = ui._silent_link_recovery_task
    if task is not None:
        await task
    # The recovery schedules a fresh status poll; stop it so the test does not leak a task.
    await ui._cancel_status_refresh()


def _make_auto_rebond_ui(
    printer: PairedPrinter,
    pairer: _FakePairer,
    status_provider: _FakeStatusProvider,
) -> BridgeUi:
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=pairer,
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)
    return ui


async def _drain_auto_rebond(ui: BridgeUi) -> None:
    task = ui._auto_rebond_task
    if task is not None:
        await task
    # The rebond schedules a fresh status poll; stop it so the test does not leak a task.
    await ui._cancel_status_refresh()


@pytest.mark.asyncio
async def test_auto_rebond_triggers_on_late_stage_write_failure() -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    status_provider = _FakeStatusProvider(error=_stale_bond_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    # threshold=1: the first stale-bond signature triggers the rebond immediately.
    assert not await ui._refresh_printer_status_in_background(printer)
    await _drain_auto_rebond(ui)

    assert pairer.removed_bonds == [printer]
    assert status_provider.close_cached_calls >= 1


@pytest.mark.asyncio
async def test_auto_rebond_skips_non_signature_failures() -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    status_provider = _FakeStatusProvider(error=_not_found_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    # A non-signature (not-found) failure never triggers a rebond and keeps the streak at 0.
    assert not await ui._refresh_printer_status_in_background(printer)
    assert ui._auto_rebond_task is None
    assert ui._auto_rebond_signature_streak == 0
    assert pairer.removed_bonds == []

    # A stale-bond signature then triggers immediately (threshold=1).
    status_provider._error = _stale_bond_error(printer)
    assert not await ui._refresh_printer_status_in_background(printer)
    await _drain_auto_rebond(ui)
    assert pairer.removed_bonds == [printer]


@pytest.mark.asyncio
async def test_auto_rebond_does_not_trigger_for_not_found_failures() -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    status_provider = _FakeStatusProvider(error=_not_found_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    for _ in range(4):
        assert not await ui._refresh_printer_status_in_background(printer)

    assert ui._auto_rebond_task is None
    assert pairer.removed_bonds == []


@pytest.mark.asyncio
async def test_silent_link_recovery_disconnects_on_not_found() -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    status_provider = _FakeStatusProvider(error=_silent_link_not_found_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    # An FFI not-found failure drops the silent BlueZ link so the printer re-advertises.
    assert not await ui._refresh_printer_status_in_background(printer)
    await _drain_silent_link_recovery(ui)

    assert pairer.disconnected_links == [printer]
    # It must not remove the bond (that is the stale-bond path, not this one).
    assert pairer.removed_bonds == []


@pytest.mark.asyncio
async def test_silent_link_recovery_skips_non_not_found_failures() -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    # A stale-bond signature is not a not-found failure: silent-link recovery must stay out of it.
    status_provider = _FakeStatusProvider(error=_stale_bond_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    assert not await ui._refresh_printer_status_in_background(printer)
    assert ui._silent_link_recovery_task is None
    assert pairer.disconnected_links == []
    await _drain_auto_rebond(ui)


@pytest.mark.asyncio
async def test_silent_link_recovery_no_op_when_no_connected_link() -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    # Printer is genuinely off: no connected link, so disconnect reports it did nothing.
    pairer.link_is_connected = False
    status_provider = _FakeStatusProvider(error=_silent_link_not_found_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    assert not await ui._refresh_printer_status_in_background(printer)
    task = ui._silent_link_recovery_task
    assert task is not None
    await task

    # The disconnect was attempted but found nothing connected; no status refresh is scheduled.
    assert pairer.disconnected_links == [printer]
    assert ui._status_task is None


@pytest.mark.asyncio
async def test_silent_link_recovery_cooldown_prevents_second_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    status_provider = _FakeStatusProvider(error=_silent_link_not_found_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    clock = {"now": 1000.0}
    monkeypatch.setattr(ui, "_monotonic", lambda: clock["now"])

    assert not await ui._refresh_printer_status_in_background(printer)
    await _drain_silent_link_recovery(ui)
    assert pairer.disconnected_links == [printer]

    # Within the cooldown window: a recurring not-found must not disconnect again.
    clock["now"] = 1000.0 + SILENT_LINK_RECOVERY_COOLDOWN_S - 1.0
    assert not await ui._refresh_printer_status_in_background(printer)
    assert ui._silent_link_recovery_task is None or ui._silent_link_recovery_task.done()
    assert pairer.disconnected_links == [printer]

    # After the cooldown elapses, recovery is allowed again.
    clock["now"] = 1000.0 + SILENT_LINK_RECOVERY_COOLDOWN_S + 1.0
    assert not await ui._refresh_printer_status_in_background(printer)
    await _drain_silent_link_recovery(ui)
    assert pairer.disconnected_links == [printer, printer]


@pytest.mark.asyncio
async def test_auto_rebond_cooldown_prevents_second_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    status_provider = _FakeStatusProvider(error=_stale_bond_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    clock = {"now": 1000.0}
    monkeypatch.setattr(ui, "_monotonic", lambda: clock["now"])

    assert not await ui._refresh_printer_status_in_background(printer)
    assert not await ui._refresh_printer_status_in_background(printer)
    await _drain_auto_rebond(ui)
    assert pairer.removed_bonds == [printer]

    # Signature recurs within the cooldown window: must not remove the bond again.
    clock["now"] = 1000.0 + 30.0
    assert not await ui._refresh_printer_status_in_background(printer)
    assert not await ui._refresh_printer_status_in_background(printer)
    await _drain_auto_rebond(ui)
    assert pairer.removed_bonds == [printer]


@pytest.mark.asyncio
async def test_auto_rebond_keeps_printer_selected() -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    status_provider = _FakeStatusProvider(error=_stale_bond_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    assert not await ui._refresh_printer_status_in_background(printer)
    assert not await ui._refresh_printer_status_in_background(printer)
    await _drain_auto_rebond(ui)

    # Only the BlueZ bond is removed; the user's selection is preserved.
    assert pairer.removed_bonds == [printer]
    assert not pairer.forgot
    assert await pairer.list_paired() == [printer]
    assert ui._snapshot.paired_printer == printer


@pytest.mark.asyncio
async def test_successful_status_resets_auto_rebond_counters() -> None:
    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    status_provider = _FakeStatusProvider(error=_stale_bond_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    # Simulate the counters a prior rebond leaves behind (a pending streak + a recorded
    # cooldown), without depending on the fire-and-forget rebond task's rescheduled poll.
    ui._auto_rebond_signature_streak = 1
    ui._last_auto_rebond_at[_auto_rebond_key(printer)] = ui._monotonic()

    # A successful status clears the streak and the per-device cooldown so a later stale bond
    # can rebond again.
    status_provider._error = None
    status_provider._snapshot = PrinterStatusSnapshot(
        film_remaining=5,
        battery=80,
        is_charging=False,
        model=PrinterModel.SQUARE,
        message="Ready",
    )
    assert await ui._refresh_printer_status_in_background(printer)
    assert ui._auto_rebond_signature_streak == 0
    assert _auto_rebond_key(printer) not in ui._last_auto_rebond_at


@pytest.mark.asyncio
async def test_proactive_bond_reset_on_drop_when_disconnected() -> None:
    """A dropped link with Connected=no proactively resets the bond (docs/plans/031)."""

    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    pairer.link_is_connected = False  # link is actually down
    status_provider = _FakeStatusProvider()
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    await ui._maybe_reset_bond_on_drop(printer)

    # It used the proactive primitive (not the reactive remove_bluez_bond directly) and removed.
    assert pairer.reset_bond_calls == [printer]
    assert pairer.removed_bonds == [printer]
    # Selection is preserved; this is a fresh-pair reset, not a forget.
    assert not pairer.forgot
    assert await pairer.list_paired() == [printer]


@pytest.mark.asyncio
async def test_proactive_bond_reset_skips_when_still_connected() -> None:
    """Never reset the bond mid-session: a live link must not be torn down (docs/plans/031)."""

    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    pairer.link_is_connected = True  # link is still up
    status_provider = _FakeStatusProvider()
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    await ui._maybe_reset_bond_on_drop(printer)

    # The gate is consulted but no bond is removed while connected.
    assert pairer.reset_bond_calls == [printer]
    assert pairer.removed_bonds == []


@pytest.mark.asyncio
async def test_proactive_bond_reset_guard_prevents_overlap() -> None:
    """A reset already in flight is not duplicated by a concurrent drop edge (docs/plans/031)."""

    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    pairer.link_is_connected = False
    status_provider = _FakeStatusProvider()
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    # Simulate an in-flight reset task; the guard must short-circuit a new attempt.
    started = asyncio.Event()
    release = asyncio.Event()

    async def _blocking_reset() -> None:
        started.set()
        await release.wait()

    ui._proactive_bond_reset_task = asyncio.create_task(_blocking_reset())
    await started.wait()

    await ui._maybe_reset_bond_on_drop(printer)
    assert pairer.reset_bond_calls == []

    release.set()
    await ui._proactive_bond_reset_task


@pytest.mark.asyncio
async def test_poll_loop_resets_bond_only_on_connected_to_failed_edge() -> None:
    """The poll loop fires the reset on the online->offline edge, not on a steady-failed state."""

    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _FakePairer([printer])
    pairer.link_is_connected = False
    status_provider = _FakeStatusProvider(error=_not_found_error(printer))
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    # Steady-failed (was offline -> still offline): no edge, no reset.
    ui._printer_was_online = False
    online = await ui._refresh_printer_status_in_background(printer)
    if ui._printer_was_online and not online:
        await ui._maybe_reset_bond_on_drop(printer)
    ui._printer_was_online = online
    assert pairer.reset_bond_calls == []

    # Connected -> failed edge: the link just dropped, so the bond is reset.
    ui._printer_was_online = True
    online = await ui._refresh_printer_status_in_background(printer)
    if ui._printer_was_online and not online:
        await ui._maybe_reset_bond_on_drop(printer)
    ui._printer_was_online = online
    assert pairer.reset_bond_calls == [printer]
    assert pairer.removed_bonds == [printer]


@pytest.mark.asyncio
async def test_proactive_bond_reset_unsupported_pairer_is_noop() -> None:
    """A pairer without reset_bond_if_disconnected is a safe no-op (dormant auto_rebond stays)."""

    printer = PairedPrinter(address="INSTANTLINK:1N034655", name="INSTAX-1N034655")
    pairer = _LegacyPairer([printer])
    status_provider = _FakeStatusProvider()
    ui = _make_auto_rebond_ui(printer, pairer, status_provider)

    await ui._maybe_reset_bond_on_drop(printer)

    assert ui._proactive_bond_reset_task is None


class _LegacyPairer:
    """A pairer predating the proactive reset primitive (docs/plans/031)."""

    def __init__(self, printers: list[PairedPrinter]) -> None:
        self._printers = printers

    async def list_paired(self) -> list[PairedPrinter]:
        return self._printers

    async def pair_first_available(self) -> PairedPrinter:
        return self._printers[0]

    def save_selected(self, printer: PairedPrinter) -> None:
        return

    async def forget_selected(self) -> None:
        return


class _FakeDisplay:
    def __init__(self) -> None:
        self.snapshots: list[UiSnapshot] = []
        self.idle_stages: list[str] = []

    def render(self, snapshot: UiSnapshot) -> None:
        self.snapshots.append(snapshot)

    def set_idle_stage(self, stage: str) -> None:
        self.idle_stages.append(stage)

    def close(self) -> None:
        return


async def _wait_for_mode(display: _FakeDisplay, mode: UiMode) -> None:
    for _ in range(20):
        if display.snapshots and display.snapshots[-1].mode is mode:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for mode {mode}")


async def _wait_for_preview_image(display: _FakeDisplay) -> None:
    for _ in range(50):
        if display.snapshots and display.snapshots[-1].preview_image is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for preview image")


async def _wait_for_preview_image_name(display: _FakeDisplay, image_name: str) -> None:
    for _ in range(50):
        if (
            display.snapshots
            and display.snapshots[-1].last_image_name == image_name
            and display.snapshots[-1].preview_image is not None
        ):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for preview image {image_name}")


def _same_fake_printer_identity(left: PairedPrinter, right: PairedPrinter) -> bool:
    return left.address.upper() == right.address.upper() or left.name == right.name


def _latest_mode(display: _FakeDisplay) -> UiMode:
    return display.snapshots[-1].mode


def _printer_fit(ui: BridgeUi) -> FitMode:
    return ui.config.printer.fit


def _ftp_mode(ui: BridgeUi) -> FtpReceiveMode:
    return ui.config.ftp.mode


class _FakePairer:
    def __init__(self, printers: list[PairedPrinter]) -> None:
        self._printers = printers
        self.forgot = False
        self.list_calls = 0
        self.saved_selected: PairedPrinter | None = None
        self.removed_bonds: list[PairedPrinter] = []
        self.disconnected_links: list[PairedPrinter] = []
        # Proactive bond reset attempts (docs/plans/031); the recorded bool is whether it removed.
        self.reset_bond_calls: list[PairedPrinter] = []
        # When True, disconnect_bluez_link reports it dropped a connected link (the deadlock case)
        # and reset_bond_if_disconnected skips removal (a live link must never be reset).
        self.link_is_connected = True

    async def list_paired(self) -> list[PairedPrinter]:
        self.list_calls += 1
        return self._printers

    async def pair_first_available(self) -> PairedPrinter:
        if not self._printers:
            raise AssertionError("no fake printer")
        return self._printers[0]

    def save_selected(self, printer: PairedPrinter) -> None:
        self.saved_selected = printer
        self._printers = [
            printer if _same_fake_printer_identity(existing, printer) else existing
            for existing in self._printers
        ]

    async def forget_selected(self) -> None:
        self.forgot = True

    async def remove_bluez_bond(self, printer: PairedPrinter) -> None:
        self.removed_bonds.append(printer)

    async def reset_bond_if_disconnected(self, printer: PairedPrinter) -> bool:
        self.reset_bond_calls.append(printer)
        # Mirror the real pairer: only remove the bond when the link is actually down.
        if self.link_is_connected:
            return False
        self.removed_bonds.append(printer)
        return True

    async def disconnect_bluez_link(self, printer: PairedPrinter) -> bool:
        self.disconnected_links.append(printer)
        return self.link_is_connected


class _FakeStatusProvider:
    def __init__(
        self,
        *,
        snapshot: PrinterStatusSnapshot | None = None,
        error: Exception | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._error = error
        self.fetch_started = asyncio.Event()
        self.fetch_calls = 0
        self.close_calls = 0
        self.close_cached_calls = 0
        self.keepalive_interval_calls: list[float | None] = []

    async def fetch(self, _printer: PairedPrinter) -> PrinterStatusSnapshot:
        self.fetch_calls += 1
        self.fetch_started.set()
        if self._error is not None:
            raise self._error
        if self._snapshot is None:
            raise AssertionError("fake status snapshot was not configured")
        return self._snapshot

    async def close(self) -> None:
        self.close_calls += 1

    async def close_cached_session(self) -> None:
        self.close_cached_calls += 1

    async def configure_keepalive(self, interval_s: float | None) -> None:
        self.keepalive_interval_calls.append(interval_s)


class _BlockingStatusProvider:
    """Status provider whose fetch blocks until released, modelling the shielded BLE worker."""

    def __init__(self) -> None:
        self.fetch_started = asyncio.Event()
        self.released = asyncio.Event()
        self.close_calls = 0

    async def fetch(self, _printer: PairedPrinter) -> PrinterStatusSnapshot:
        self.fetch_started.set()
        await self.released.wait()
        raise AssertionError("fetch should be cancelled before completing")

    def release(self) -> None:
        self.released.set()

    async def close(self) -> None:
        self.close_calls += 1


async def _unused_wifi_mode_setter(mode: WifiMode) -> str:
    raise AssertionError(f"unexpected Wi-Fi mode change: {mode}")


@pytest.mark.asyncio
async def test_successful_status_marks_snapshot_fresh_and_stamps_clock() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        snapshot=PrinterStatusSnapshot(
            film_remaining=8,
            battery=70,
            is_charging=False,
            model=PrinterModel.SQUARE,
            message="Ready",
        )
    )
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.READY, paired_printer=printer)
    assert not ui._snapshot.printer_status_fresh

    assert await ui._refresh_printer_status_in_background(printer)

    assert ui._snapshot.printer_status_fresh
    assert ui._last_printer_status_ok_at != float("-inf")
    assert can_accept_images(replace(ui._snapshot, camera_receive_ready=True))


@pytest.mark.asyncio
async def test_render_tick_downgrades_freshness_after_ttl_then_success_restores() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        snapshot=PrinterStatusSnapshot(
            film_remaining=8,
            battery=70,
            is_charging=False,
            model=PrinterModel.SQUARE,
            message="Ready",
        )
    )
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.READY, paired_printer=printer)

    fake_now = {"t": 1000.0}
    ui._monotonic = lambda: fake_now["t"]  # type: ignore[method-assign]
    ttl = ui._printer_status_fresh_ttl_s()

    assert await ui._refresh_printer_status_in_background(printer)
    assert ui._snapshot.printer_status_fresh

    tick_task = asyncio.create_task(ui._run_render_tick())
    try:
        # Within the TTL the snapshot stays fresh.
        fake_now["t"] = 1000.0 + ttl - 1.0
        await asyncio.sleep(RENDER_TICK_S * 2)
        assert ui._snapshot.printer_status_fresh

        # Past the TTL with no new success the tick downgrades readiness.
        fake_now["t"] = 1000.0 + ttl + 1.0
        for _ in range(50):
            if not ui._snapshot.printer_status_fresh:
                break
            await asyncio.sleep(RENDER_TICK_S)
        assert not ui._snapshot.printer_status_fresh

        # A subsequent successful status restores freshness.
        assert await ui._refresh_printer_status_in_background(printer)
        assert ui._snapshot.printer_status_fresh
    finally:
        tick_task.cancel()
        with suppress(asyncio.CancelledError):
            await tick_task


@pytest.mark.asyncio
async def test_ready_downgrades_when_no_status_succeeds_for_ttl() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    status_provider = _FakeStatusProvider(
        snapshot=PrinterStatusSnapshot(
            film_remaining=8,
            battery=70,
            is_charging=False,
            model=PrinterModel.SQUARE,
            message="Ready",
        )
    )
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=status_provider,
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = ui._build_snapshot(mode=UiMode.READY, paired_printer=printer)

    fake_now = {"t": 5000.0}
    ui._monotonic = lambda: fake_now["t"]  # type: ignore[method-assign]
    ttl = ui._printer_status_fresh_ttl_s()

    # Connected -> ready.
    assert await ui._refresh_printer_status_in_background(printer)
    assert ui._snapshot.mode is UiMode.READY
    ready_with_camera = replace(ui._snapshot, camera_receive_ready=True)
    assert can_accept_images(ready_with_camera)

    # No successful status for longer than the TTL: even though the cached mode is still READY and
    # film_remaining is still set, the printer-off display must leave "ready to print".
    fake_now["t"] = 5000.0 + ttl + 1.0
    ui._snapshot = replace(ui._snapshot, printer_status_fresh=ui._printer_status_is_fresh())
    assert not ui._snapshot.printer_status_fresh
    assert not can_accept_images(replace(ui._snapshot, camera_receive_ready=True))


@pytest.mark.asyncio
async def test_apply_printer_searching_clears_freshness() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=_FakeStatusProvider(),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._snapshot = replace(
        ui._build_snapshot(mode=UiMode.READY, paired_printer=printer, film_remaining=8),
        printer_status_fresh=True,
    )

    ui._apply_printer_searching(printer, "Looking for printer")

    assert ui._snapshot.mode is UiMode.PRINTER_SEARCHING
    assert not ui._snapshot.printer_status_fresh


@pytest.mark.asyncio
async def test_close_cached_printer_session_resets_freshness_clock() -> None:
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([printer]),
        status_provider=_FakeStatusProvider(),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._last_printer_status_ok_at = 1234.0

    await ui._close_cached_printer_session()

    assert ui._last_printer_status_ok_at == float("-inf")


@pytest.mark.asyncio
async def test_image_queue_changed_updates_snapshot_depth() -> None:
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    initial_mode = ui.snapshot.mode

    await ui.image_queue_changed(depth=2, max_size=100)

    assert ui.snapshot.image_queue_depth == 2
    assert ui.snapshot.mode is initial_mode
    assert not ui._printer_status_is_fresh()


@pytest.mark.asyncio
async def test_reset_credentials_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ssid_path = tmp_path / "hotspot.ssid"
    psk_path = tmp_path / "hotspot.psk"
    ssid_path.write_text("InstantLink-ORIG\n", encoding="utf-8")
    psk_path.write_text("11111111\n", encoding="utf-8")
    monkeypatch.setenv("INSTANTLINK_BRIDGE_HOTSPOT_SSID_FILE", str(ssid_path))
    monkeypatch.setenv("INSTANTLINK_BRIDGE_HOTSPOT_PSK_FILE", str(psk_path))

    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    # Navigate to Upload FTP page and select the Reset credentials row (last row)
    await ui._handle_action(UiAction.SELECT)  # open settings
    await ui._handle_action(UiAction.DOWN)  # move to Upload FTP
    await ui._handle_action(UiAction.SELECT)  # open Upload FTP page

    # Scroll to the last row (Reset credentials)
    from instantlink_bridge.ui.settings import (
        SECTION_HEADER_KEYS,
        SETTINGS_BY_PAGE,
        SettingsPage,
    )

    camera_keys = SETTINGS_BY_PAGE[SettingsPage.NETWORK]
    # Plan 037 phase 1: NETWORK_DIAGNOSTICS_HEADER is non-selectable and the
    # UP/DOWN nav skips over it, so the loop to walk to the last row is one
    # press shorter per header that sits between the start and the end.
    header_count = sum(1 for k in camera_keys if k in SECTION_HEADER_KEYS)
    for _ in range(len(camera_keys) - 1 - header_count):
        await ui._handle_action(UiAction.DOWN)

    # First SELECT should show confirmation prompt, not execute
    await ui._handle_action(UiAction.SELECT)

    # New copy: "Press KEY1 again to RESET Wi-Fi/FTP credentials" — match
    # the pattern shared with other destructive-confirm toasts.
    assert "press key1 again to reset" in (display.snapshots[-1].settings_message or "").lower()
    assert ssid_path.read_text(encoding="utf-8") == "InstantLink-ORIG\n"
    assert psk_path.read_text(encoding="utf-8") == "11111111\n"
    assert ui._pending_credential_reset is True


@pytest.mark.asyncio
async def test_reset_credentials_second_select_executes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ssid_path = tmp_path / "hotspot.ssid"
    psk_path = tmp_path / "hotspot.psk"
    ssid_path.write_text("InstantLink-ORIG\n", encoding="utf-8")
    psk_path.write_text("11111111\n", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text('[ftp]\npassword = "11111111"\n', encoding="utf-8")
    monkeypatch.setenv("INSTANTLINK_BRIDGE_HOTSPOT_SSID_FILE", str(ssid_path))
    monkeypatch.setenv("INSTANTLINK_BRIDGE_HOTSPOT_PSK_FILE", str(psk_path))

    applied_ftp: list[object] = []

    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
        ftp_config_applied_callback=lambda cfg: applied_ftp.append(cfg),
    )

    # Navigate to Upload FTP page and go to Reset credentials row
    await ui._handle_action(UiAction.SELECT)  # open settings
    await ui._handle_action(UiAction.DOWN)  # Upload FTP
    await ui._handle_action(UiAction.SELECT)  # open page

    from instantlink_bridge.ui.settings import (
        SECTION_HEADER_KEYS,
        SETTINGS_BY_PAGE,
        SettingsPage,
    )

    camera_keys = SETTINGS_BY_PAGE[SettingsPage.NETWORK]
    # Plan 037 phase 1: NETWORK_DIAGNOSTICS_HEADER is non-selectable and the
    # UP/DOWN nav skips over it, so the loop to walk to the last row is one
    # press shorter per header that sits between the start and the end.
    header_count = sum(1 for k in camera_keys if k in SECTION_HEADER_KEYS)
    for _ in range(len(camera_keys) - 1 - header_count):
        await ui._handle_action(UiAction.DOWN)

    # First SELECT: confirmation prompt
    await ui._handle_action(UiAction.SELECT)
    assert ui._pending_credential_reset is True

    # Second SELECT: execute reset
    await ui._handle_action(UiAction.SELECT)

    new_ssid = ssid_path.read_text(encoding="utf-8").strip()
    new_psk = psk_path.read_text(encoding="utf-8").strip()

    assert new_ssid.startswith("InstantLink-")
    assert new_ssid != "InstantLink-ORIG"
    assert len(new_psk) == 8
    assert new_psk != "11111111"
    assert ui._config.ftp.password != "11111111"
    assert len(ui._config.ftp.password) == 8
    assert len(applied_ftp) >= 1
    assert ui._pending_credential_reset is False


@pytest.mark.asyncio
async def test_reset_credentials_other_key_cancels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ssid_path = tmp_path / "hotspot.ssid"
    psk_path = tmp_path / "hotspot.psk"
    ssid_path.write_text("InstantLink-ORIG\n", encoding="utf-8")
    psk_path.write_text("11111111\n", encoding="utf-8")
    monkeypatch.setenv("INSTANTLINK_BRIDGE_HOTSPOT_SSID_FILE", str(ssid_path))
    monkeypatch.setenv("INSTANTLINK_BRIDGE_HOTSPOT_PSK_FILE", str(psk_path))

    display = _FakeDisplay()
    ui = BridgeUi(
        BridgeConfig(),
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    # Navigate to Upload FTP page and Reset credentials row
    await ui._handle_action(UiAction.SELECT)  # open settings
    await ui._handle_action(UiAction.DOWN)  # Upload FTP
    await ui._handle_action(UiAction.SELECT)  # open page

    from instantlink_bridge.ui.settings import (
        SECTION_HEADER_KEYS,
        SETTINGS_BY_PAGE,
        SettingsPage,
    )

    camera_keys = SETTINGS_BY_PAGE[SettingsPage.NETWORK]
    # Plan 037 phase 1: NETWORK_DIAGNOSTICS_HEADER is non-selectable and the
    # UP/DOWN nav skips over it, so the loop to walk to the last row is one
    # press shorter per header that sits between the start and the end.
    header_count = sum(1 for k in camera_keys if k in SECTION_HEADER_KEYS)
    for _ in range(len(camera_keys) - 1 - header_count):
        await ui._handle_action(UiAction.DOWN)

    # First SELECT: arm confirmation
    await ui._handle_action(UiAction.SELECT)
    assert ui._pending_credential_reset is True

    # DOWN clears the flag and no files are written
    await ui._handle_action(UiAction.DOWN)

    assert ui._pending_credential_reset is False
    assert ssid_path.read_text(encoding="utf-8") == "InstantLink-ORIG\n"
    assert psk_path.read_text(encoding="utf-8") == "11111111\n"


# ---------------------------------------------------------------------------
# Plan 036 Phase 4 — Focused adjustment-edit mode
# ---------------------------------------------------------------------------


def _make_adj_ui(tmp_path: Path, *, preset: str = "Custom", saturation: int = 0) -> BridgeUi:
    """Build a BridgeUi positioned on the Adjustments sub-page."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[adjustments]\npreset = "{preset}"\nsaturation = {saturation}\n',
        encoding="utf-8",
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    # Navigate directly to the Adjustments page via internal helper to avoid
    # a long chain of action dispatches.
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    return ui


@pytest.mark.asyncio
async def test_adjustments_slider_row_select_enters_edit_mode(tmp_path: Path) -> None:
    """SELECT on a slider row in Custom preset → enters ADJUSTMENT_EDIT mode."""
    from instantlink_bridge.ui.settings import SettingKey

    ui = _make_adj_ui(tmp_path, preset="Custom", saturation=0)
    # Adjustments page rows: 0=Preset 1=Saturation 2=Exposure 3=Sharpness 4=Hue
    # 5=Vignette 6=Datestamp 7=Datestamp format 8=Watermark 9=Save current.
    # Navigate to Saturation (row 1).
    await ui._handle_action(UiAction.DOWN)  # → index 1 (Saturation)
    await ui._handle_action(UiAction.SELECT)

    assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT
    assert ui._snapshot.adjustment_edit_key == SettingKey.ADJUST_SATURATION
    assert ui._snapshot.adjustment_edit_value == 0
    assert ui._adjustment_edit_key is SettingKey.ADJUST_SATURATION


@pytest.mark.asyncio
async def test_adjustment_edit_up_nudges_plus_5(tmp_path: Path) -> None:
    """UP in ADJUSTMENT_EDIT increments working value by 5 (up = more)."""
    ui = _make_adj_ui(tmp_path, saturation=0)
    await ui._handle_action(UiAction.DOWN)  # Saturation row
    await ui._handle_action(UiAction.SELECT)  # enter edit

    await ui._handle_action(UiAction.UP)

    assert ui._snapshot.adjustment_edit_value == 5
    assert ui._adjustment_edit_value == 5


@pytest.mark.asyncio
async def test_adjustment_edit_down_nudges_minus_5(tmp_path: Path) -> None:
    """DOWN in ADJUSTMENT_EDIT decrements working value by 5 (down = less)."""
    ui = _make_adj_ui(tmp_path, saturation=0)
    await ui._handle_action(UiAction.DOWN)  # Saturation row
    await ui._handle_action(UiAction.SELECT)

    await ui._handle_action(UiAction.DOWN)

    assert ui._snapshot.adjustment_edit_value == -5


@pytest.mark.asyncio
async def test_adjustment_edit_left_nudges_minus_25(tmp_path: Path) -> None:
    """LEFT in ADJUSTMENT_EDIT decrements working value by 25."""
    ui = _make_adj_ui(tmp_path, saturation=0)
    await ui._handle_action(UiAction.DOWN)  # Saturation row
    await ui._handle_action(UiAction.SELECT)

    await ui._handle_action(UiAction.LEFT)

    assert ui._snapshot.adjustment_edit_value == -25


@pytest.mark.asyncio
async def test_adjustment_edit_right_nudges_plus_25(tmp_path: Path) -> None:
    """RIGHT in ADJUSTMENT_EDIT increments working value by 25."""
    ui = _make_adj_ui(tmp_path, saturation=0)
    await ui._handle_action(UiAction.DOWN)  # Saturation row
    await ui._handle_action(UiAction.SELECT)

    await ui._handle_action(UiAction.RIGHT)

    assert ui._snapshot.adjustment_edit_value == 25


@pytest.mark.asyncio
async def test_adjustment_edit_value_clamped_to_range(tmp_path: Path) -> None:
    """Nudging past the max is clamped at the axis max (100 for saturation)."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Custom"\nsaturation = 100\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    await ui._handle_action(UiAction.DOWN)  # Saturation row
    await ui._handle_action(UiAction.SELECT)

    # Already at max; RIGHT (+25) should stay at 100.
    await ui._handle_action(UiAction.RIGHT)

    assert ui._snapshot.adjustment_edit_value == 100


@pytest.mark.asyncio
async def test_adjustment_edit_vignette_clamped_at_zero(tmp_path: Path) -> None:
    """DOWN (-5) on vignette at 0 stays at 0 (range is [0, 100], not symmetric)."""
    from instantlink_bridge.ui.settings import SettingsPage

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Custom"\nvignette = 0\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    # Adjustments page: 0=Preset 1=Sat 2=Exp 3=Sharp 4=Hue 5=Vignette.
    for _ in range(5):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT
    await ui._handle_action(UiAction.DOWN)  # -5, but floor is 0

    assert ui._snapshot.adjustment_edit_value == 0


@pytest.mark.asyncio
async def test_adjustment_edit_select_commits_to_config(tmp_path: Path) -> None:
    """KEY1 (SELECT) in edit mode writes the value to config and disk."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Custom"\nsaturation = 0\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    await ui._handle_action(UiAction.DOWN)  # Saturation row
    await ui._handle_action(UiAction.SELECT)  # enter edit

    await ui._handle_action(UiAction.RIGHT)  # +25
    await ui._handle_action(UiAction.RIGHT)  # +25 → 50
    await ui._handle_action(UiAction.SELECT)  # commit

    assert ui._config.adjustments.saturation == 50
    assert load_config(config_path).adjustments.saturation == 50
    assert ui._snapshot.mode is UiMode.SETTINGS
    assert ui._snapshot.settings_message == "Saved"


@pytest.mark.asyncio
async def test_adjustment_edit_back_reverts_without_commit(tmp_path: Path) -> None:
    """KEY2 (BACK) in edit mode discards changes; config is unchanged."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Custom"\nsaturation = 0\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    await ui._handle_action(UiAction.DOWN)  # Saturation row
    await ui._handle_action(UiAction.SELECT)  # enter edit

    await ui._handle_action(UiAction.RIGHT)  # +25 (working value = 25, not committed)
    await ui._handle_action(UiAction.BACK)  # cancel

    assert ui._config.adjustments.saturation == 0
    assert load_config(config_path).adjustments.saturation == 0
    assert ui._snapshot.mode is UiMode.SETTINGS


@pytest.mark.asyncio
async def test_adjustment_edit_help_preserves_working_value(tmp_path: Path) -> None:
    """KEY3 (HELP) in edit mode shows help WITHOUT discarding the working
    value or exiting the edit mode. Regression for the plan-036 audit
    finding: the previous implementation called `_show_settings(...)`
    which forced mode → SETTINGS and recomputed adjustments_profile from
    the committed config, silently dropping the user's in-progress edit
    with no warning. KEY3 must now leave both `_adjustment_edit_value`
    and the snapshot's edit mode + live preview intact."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[adjustments]\npreset = "Default"\nsaturation = 0\n',
        encoding="utf-8",
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    await ui._handle_action(UiAction.DOWN)  # navigate to Saturation
    await ui._handle_action(UiAction.SELECT)  # enter edit mode

    await ui._handle_action(UiAction.RIGHT)  # +25 → working value 25
    assert ui._adjustment_edit_value == 25
    assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT

    await ui._handle_action(UiAction.HELP)

    # KEY3 must NOT exit edit mode or revert the working value. The help
    # text appears in the bottom strip via settings_message, but mode +
    # value survive.
    assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT
    assert ui._adjustment_edit_value == 25
    assert ui._snapshot.settings_message is not None

    # A subsequent SELECT commits the working value, not the original 0.
    await ui._handle_action(UiAction.SELECT)
    assert ui._config.adjustments.saturation == 25
    assert load_config(config_path).adjustments.saturation == 25


@pytest.mark.asyncio
async def test_adjustment_edit_preset_row_does_not_enter_edit(tmp_path: Path) -> None:
    """SELECT on the Preset row opens the preset picker, not edit mode."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Custom"\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    # Row 0 is Preset — already selected.
    await ui._handle_action(UiAction.SELECT)

    # Mode stays SETTINGS (picker opens, not ADJUSTMENT_EDIT).
    assert ui._snapshot.mode is UiMode.SETTINGS
    assert ui._snapshot.settings_title == "Preset"
    assert ui._adjustment_edit_key is None


# ---------------------------------------------------------------------------
# Plan 037 Phase 3 — Overlay toggles (datestamp / watermark) share the focused
#                    ADJUSTMENT_EDIT mode with sliders so the user sees the
#                    rendered overlay before committing.
# ---------------------------------------------------------------------------


def _make_overlay_ui(
    tmp_path: Path,
    *,
    datestamp: bool = False,
    watermark: bool = False,
) -> BridgeUi:
    """Build a BridgeUi positioned on the Adjustments page with overlay flags."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[adjustments]\n"
        'preset = "Custom"\n'
        f"datestamp = {str(datestamp).lower()}\n"
        f"watermark = {str(watermark).lower()}\n",
        encoding="utf-8",
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    return ui


async def _navigate_to_datestamp(ui: BridgeUi) -> None:
    """Walk the cursor down to the Datestamp row (index 6)."""
    for _ in range(6):
        await ui._handle_action(UiAction.DOWN)


async def _navigate_to_watermark(ui: BridgeUi) -> None:
    """Walk the cursor down to the Watermark row (index 8; plan 037 phase 4
    inserted ADJUST_DATESTAMP_FORMAT between Datestamp and Watermark)."""
    for _ in range(8):
        await ui._handle_action(UiAction.DOWN)


@pytest.mark.asyncio
async def test_toggle_activate_enters_edit_mode_not_picker(tmp_path: Path) -> None:
    """KEY1 on Datestamp enters ADJUSTMENT_EDIT (plan 037), not the bool picker."""
    from instantlink_bridge.ui.settings import SettingKey

    ui = _make_overlay_ui(tmp_path, datestamp=False)
    await _navigate_to_datestamp(ui)
    await ui._handle_action(UiAction.SELECT)

    assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT
    assert ui._adjustment_edit_key is SettingKey.ADJUST_DATESTAMP
    assert ui._settings_picker_key is None


@pytest.mark.asyncio
async def test_toggle_key1_commits_flipped_value_and_exits(tmp_path: Path) -> None:
    """UP flips working value; KEY1 commits and exits to SETTINGS."""

    ui = _make_overlay_ui(tmp_path, watermark=False)
    await _navigate_to_watermark(ui)
    await ui._handle_action(UiAction.SELECT)  # enter edit, working=0
    await ui._handle_action(UiAction.UP)  # flip to 1
    assert ui._adjustment_edit_value == 1
    # Not committed yet — config still shows False.
    assert ui._config.adjustments.watermark is False

    await ui._handle_action(UiAction.SELECT)  # KEY1 commits

    assert ui._snapshot.mode is UiMode.SETTINGS
    assert ui._config.adjustments.watermark is True
    assert ui._adjustment_edit_key is None


@pytest.mark.asyncio
async def test_toggle_key2_cancels_without_commit(tmp_path: Path) -> None:
    """KEY2 (BACK) reverts: working flip is discarded, config unchanged."""

    ui = _make_overlay_ui(tmp_path, watermark=True)
    await _navigate_to_watermark(ui)
    await ui._handle_action(UiAction.SELECT)  # enter, working=1
    await ui._handle_action(UiAction.UP)  # flip to 0

    await ui._handle_action(UiAction.BACK)  # KEY2 cancel

    assert ui._snapshot.mode is UiMode.SETTINGS
    assert ui._config.adjustments.watermark is True  # unchanged
    assert ui._adjustment_edit_key is None


@pytest.mark.asyncio
async def test_toggle_up_flips_without_commit(tmp_path: Path) -> None:
    """UP in toggle edit mode flips the working value without writing config."""

    ui = _make_overlay_ui(tmp_path, watermark=False)
    await _navigate_to_watermark(ui)
    await ui._handle_action(UiAction.SELECT)  # working=0

    await ui._handle_action(UiAction.UP)

    assert ui._adjustment_edit_value == 1
    assert ui._snapshot.adjustment_edit_value == 1
    assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT
    assert ui._config.adjustments.watermark is False  # NOT committed


@pytest.mark.asyncio
async def test_toggle_select_commits_unchanged_value(tmp_path: Path) -> None:
    """KEY1 immediately after entering edit mode commits the current value as-is."""

    ui = _make_overlay_ui(tmp_path, watermark=True)
    await _navigate_to_watermark(ui)
    await ui._handle_action(UiAction.SELECT)  # working=1

    await ui._handle_action(UiAction.SELECT)  # commit unchanged

    assert ui._snapshot.mode is UiMode.SETTINGS
    assert ui._config.adjustments.watermark is True  # still on, written


@pytest.mark.asyncio
async def test_toggle_down_left_right_all_flip(tmp_path: Path) -> None:
    """DOWN / LEFT / RIGHT in toggle edit mode also flip the working value."""

    for action in (UiAction.DOWN, UiAction.LEFT, UiAction.RIGHT):
        ui = _make_overlay_ui(tmp_path, datestamp=False)
        await _navigate_to_datestamp(ui)
        await ui._handle_action(UiAction.SELECT)  # working=0
        await ui._handle_action(action)
        assert ui._adjustment_edit_value == 1, f"{action} did not flip"
        assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT


@pytest.mark.asyncio
async def test_toggle_preview_profile_carries_working_value(tmp_path: Path) -> None:
    """The snapshot's adjustments_profile reflects the working toggle bool + placeholder text."""

    ui = _make_overlay_ui(tmp_path, watermark=False)
    await _navigate_to_watermark(ui)
    await ui._handle_action(UiAction.SELECT)  # working=0
    # Off state: watermark flag in preview profile is False (no overlay drawn).
    profile_off = ui._snapshot.adjustments_profile
    assert profile_off is not None
    assert profile_off.watermark is False

    await ui._handle_action(UiAction.UP)  # working=1
    profile_on = ui._snapshot.adjustments_profile
    assert profile_on is not None
    assert profile_on.watermark is True
    # Placeholder text is injected so the overlay actually paints in the preview
    # even though the persisted config has the default watermark_text.
    assert profile_on.watermark_text != ""


# ---------------------------------------------------------------------------
# Plan 036 Phase 5 — Drop Custom gate; preset stamping; save two-press confirm;
#                    long-press sub-menu; slot cap 6; B&W → Black & white
# ---------------------------------------------------------------------------


def _make_adj_ui_phase5(
    tmp_path: Path,
    *,
    preset: str = "Default",
    saturation: int = 0,
) -> BridgeUi:
    """Build a BridgeUi on the Adjustments page (no Custom requirement)."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[adjustments]\npreset = "{preset}"\nsaturation = {saturation}\n',
        encoding="utf-8",
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    return ui


@pytest.mark.asyncio
async def test_adjustments_slider_editable_without_custom_preset(tmp_path: Path) -> None:
    """KEY1 on a slider row enters edit mode regardless of preset name (no Custom gate)."""
    from instantlink_bridge.ui.settings import SettingKey

    ui = _make_adj_ui_phase5(tmp_path, preset="Vivid", saturation=50)
    # Navigate to Saturation row (index 1).
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT
    assert ui._snapshot.adjustment_edit_key == SettingKey.ADJUST_SATURATION


@pytest.mark.asyncio
async def test_adjustments_slider_editable_with_default_preset(tmp_path: Path) -> None:
    """KEY1 on a slider row enters edit mode when preset='Default'."""
    ui = _make_adj_ui_phase5(tmp_path, preset="Default", saturation=0)
    await ui._handle_action(UiAction.DOWN)  # Saturation
    await ui._handle_action(UiAction.SELECT)

    assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT


@pytest.mark.asyncio
async def test_adjustments_slider_editable_with_instax_film_preset(tmp_path: Path) -> None:
    """KEY1 on a slider row enters edit mode when preset='Instax Film'."""
    ui = _make_adj_ui_phase5(tmp_path, preset="Instax Film", saturation=0)
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    assert ui._snapshot.mode is UiMode.ADJUSTMENT_EDIT


@pytest.mark.asyncio
async def test_save_preset_requires_two_presses(tmp_path: Path) -> None:
    """First KEY1 on Save current → arms confirm toast; no file written yet."""

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\nsaturation = 30\n', encoding="utf-8")
    display = _FakeDisplay()
    presets_path = tmp_path / "presets.toml"
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)

    # Navigate to "Save current" row (last row, index 9).
    for _ in range(9):
        await ui._handle_action(UiAction.DOWN)

    # First KEY1 → destructive toast, no file write.
    import unittest.mock

    with unittest.mock.patch("instantlink_bridge.imaging.presets.USER_PRESETS_PATH", presets_path):
        await ui._handle_action(UiAction.SELECT)

    assert ui._pending_save_preset is True
    msg = ui._snapshot.settings_message or ""
    assert "Press KEY1 again" in msg
    assert "Custom" in msg
    # No file written yet.
    assert not presets_path.exists()


@pytest.mark.asyncio
async def test_save_preset_second_press_writes_file(tmp_path: Path) -> None:
    """Second KEY1 on Save current → writes the preset file and switches preset label."""
    import unittest.mock

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\nsaturation = 30\n', encoding="utf-8")
    display = _FakeDisplay()
    presets_path = tmp_path / "presets.toml"
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)

    # Navigate to "Save current" (index 9; plan 037 phase 4 added Datestamp format).
    for _ in range(9):
        await ui._handle_action(UiAction.DOWN)

    with unittest.mock.patch("instantlink_bridge.imaging.presets.USER_PRESETS_PATH", presets_path):
        await ui._handle_action(UiAction.SELECT)  # first press — arms confirm
        await ui._handle_action(UiAction.SELECT)  # second press — commits

    # File was written and preset label switched.
    assert presets_path.exists()
    assert ui._config.adjustments.preset == "Custom1"


@pytest.mark.asyncio
async def test_save_preset_cancel_between_presses(tmp_path: Path) -> None:
    """KEY2 between first and second KEY1 on Save current → no save."""
    import unittest.mock

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\nsaturation = 10\n', encoding="utf-8")
    display = _FakeDisplay()
    presets_path = tmp_path / "presets.toml"
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)

    # Save current is at index 9 (plan 037 phase 4 inserted Datestamp format).
    for _ in range(9):
        await ui._handle_action(UiAction.DOWN)

    with unittest.mock.patch("instantlink_bridge.imaging.presets.USER_PRESETS_PATH", presets_path):
        await ui._handle_action(UiAction.SELECT)  # first press
        assert ui._pending_save_preset is True
        await ui._handle_action(UiAction.BACK)  # cancel

    assert not presets_path.exists()
    assert ui._pending_save_preset is False


@pytest.mark.asyncio
async def test_long_press_on_builtin_preset_shows_toast(tmp_path: Path) -> None:
    """Long-press (HELP) on a built-in preset in picker → toast, no sub-menu."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    # Open preset picker.
    await ui._handle_action(UiAction.SELECT)
    assert ui._snapshot.settings_title == "Preset"

    # Focus row 0 = Default (built-in); long-press.
    await ui._handle_action(UiAction.HELP)

    # No sub-menu; still in the picker; toast displayed.
    assert ui._preset_submenu_slot is None
    assert ui._snapshot.settings_message is not None
    assert "cannot be edited" in ui._snapshot.settings_message


@pytest.mark.asyncio
async def test_long_press_on_user_preset_opens_submenu(tmp_path: Path) -> None:
    """Long-press on a saved custom preset → opens overwrite/delete sub-menu."""
    from instantlink_bridge.imaging.postprocess import AdjustmentProfile

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    # Inject a saved user preset into memory.
    ui._user_presets = {"Custom1": AdjustmentProfile(saturation=1.5)}
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    # Open preset picker.
    await ui._handle_action(UiAction.SELECT)
    assert ui._snapshot.settings_title == "Preset"

    # Built-ins: Default(0), Vivid(1), Soft(2), Black & white(3), Instax Film(4).
    # Custom1 is at index 5. Navigate there.
    for _ in range(5):
        await ui._handle_action(UiAction.DOWN)

    # Long-press on Custom1.
    await ui._handle_action(UiAction.HELP)

    # Sub-menu opened.
    assert ui._preset_submenu_slot == "Custom1"
    rows = ui._snapshot.settings_rows
    assert len(rows) == 2
    assert "Overwrite" in rows[0].label
    assert "Delete" in rows[1].label


@pytest.mark.asyncio
async def test_overwrite_preset_two_press_confirm(tmp_path: Path) -> None:
    """Overwrite row in sub-menu requires two KEY1 presses."""
    from instantlink_bridge.imaging.postprocess import AdjustmentProfile

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\nsaturation = 20\n', encoding="utf-8")
    display = _FakeDisplay()
    presets_path = tmp_path / "presets.toml"
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._user_presets = {"Custom1": AdjustmentProfile(saturation=1.0)}
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    # Open preset picker.
    await ui._handle_action(UiAction.SELECT)
    # Navigate to Custom1 (index 5).
    for _ in range(5):
        await ui._handle_action(UiAction.DOWN)
    # Long-press to open sub-menu.
    await ui._handle_action(UiAction.HELP)
    assert ui._preset_submenu_slot == "Custom1"

    import unittest.mock

    with unittest.mock.patch("instantlink_bridge.imaging.presets.USER_PRESETS_PATH", presets_path):
        # First SELECT on Overwrite row → arms confirm, no write yet.
        await ui._handle_action(UiAction.SELECT)
        assert ui._preset_submenu_pending_overwrite is True
        assert not presets_path.exists()
        msg = ui._snapshot.settings_message or ""
        assert "Press KEY1 again" in msg

        # Second SELECT → commits overwrite.
        await ui._handle_action(UiAction.SELECT)

    assert presets_path.exists()
    assert ui._config.adjustments.preset == "Custom1"


@pytest.mark.asyncio
async def test_delete_preset_two_press_confirm(tmp_path: Path) -> None:
    """Delete row in sub-menu requires two KEY1 presses."""
    import unittest.mock

    from instantlink_bridge.imaging.postprocess import AdjustmentProfile

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\n', encoding="utf-8")
    display = _FakeDisplay()
    presets_path = tmp_path / "presets.toml"
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._user_presets = {"Custom1": AdjustmentProfile(saturation=1.5)}
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    await ui._handle_action(UiAction.SELECT)  # open preset picker
    # Navigate to Custom1 (index 5).
    for _ in range(5):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.HELP)  # open sub-menu

    # Navigate to Delete row (index 1).
    await ui._handle_action(UiAction.DOWN)

    with unittest.mock.patch("instantlink_bridge.imaging.presets.USER_PRESETS_PATH", presets_path):
        # First SELECT → arms confirm.
        await ui._handle_action(UiAction.SELECT)
        assert ui._preset_submenu_pending_delete is True
        msg = ui._snapshot.settings_message or ""
        assert "Press KEY1 again" in msg

        # Second SELECT → deletes; preset falls back to Default since it was active.
        await ui._handle_action(UiAction.SELECT)

    assert "Custom1" not in ui._user_presets
    assert ui._config.adjustments.preset == "Default"


@pytest.mark.asyncio
async def test_selecting_preset_stamps_values_into_config(tmp_path: Path) -> None:
    """Selecting 'Vivid' from the preset picker stamps Vivid's values into config."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\nsaturation = 0\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    # Open preset picker (row 0).
    await ui._handle_action(UiAction.SELECT)
    assert ui._snapshot.settings_title == "Preset"

    # Navigate to Vivid (index 1).
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    # Vivid's saturation UI value is +50.
    assert ui._config.adjustments.saturation == 50
    assert ui._config.adjustments.preset == "Vivid"


@pytest.mark.asyncio
async def test_selecting_black_and_white_preset_stamps_values(tmp_path: Path) -> None:
    """Selecting 'Black & white' stamps saturation=-100 into config."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\nsaturation = 0\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    await ui._handle_action(UiAction.SELECT)  # open preset picker
    # Navigate to "Black & white" (index 3).
    for _ in range(3):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    assert ui._config.adjustments.saturation == -100
    assert ui._config.adjustments.preset == "Black & white"


# ---------------------------------------------------------------------------
# Plan 036 P1 fixes — new regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preset_row_shows_modified_marker_when_axis_differs(tmp_path: Path) -> None:
    """Preset row shows 'Vivid · edited' when an axis differs from canonical.

    Plan 037 polish #6: the cryptic " *" badge was replaced with the
    self-describing " · edited" suffix.
    """
    config_path = tmp_path / "config.toml"
    # Vivid canonical saturation is 50; set it to 37 to trigger the marker.
    config_path.write_text('[adjustments]\npreset = "Vivid"\nsaturation = 37\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    from instantlink_bridge.ui.settings import SettingKey

    row = ui._settings_row_for_key(SettingKey.ADJUST_PRESET, "")
    assert row.value == "Vivid · edited", f"Expected 'Vivid · edited', got {row.value!r}"


@pytest.mark.asyncio
async def test_preset_row_no_modified_marker_when_axes_match(tmp_path: Path) -> None:
    """Preset row shows 'Vivid' (no marker) when axes match Vivid's canonical values."""
    config_path = tmp_path / "config.toml"
    # Vivid: saturation=50, sharpness=25, others=0.
    config_path.write_text(
        '[adjustments]\npreset = "Vivid"\nsaturation = 50\nsharpness = 25\n',
        encoding="utf-8",
    )
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    from instantlink_bridge.ui.settings import SettingKey

    row = ui._settings_row_for_key(SettingKey.ADJUST_PRESET, "")
    assert row.value == "Vivid", f"Expected 'Vivid', got {row.value!r}"


@pytest.mark.asyncio
async def test_save_overwrites_active_custom_slot(tmp_path: Path) -> None:
    """Save on an active CustomN preset overwrites that slot, not a new one."""
    import unittest.mock

    presets_path = tmp_path / "presets.toml"
    # Pre-populate Custom1.
    from instantlink_bridge.imaging.postprocess import AdjustmentProfile
    from instantlink_bridge.imaging.presets import save_user_presets

    save_user_presets(presets_path, {"Custom1": AdjustmentProfile(saturation=1.5)})

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Custom1"\nsaturation = 30\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    # Load user presets so the controller knows Custom1 exists.
    from instantlink_bridge.imaging.presets import load_user_presets

    ui._user_presets = load_user_presets(presets_path)
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)

    # Navigate to "Save current" row (index 9; plan 037 phase 4 added Datestamp format).
    for _ in range(9):
        await ui._handle_action(UiAction.DOWN)

    with unittest.mock.patch("instantlink_bridge.imaging.presets.USER_PRESETS_PATH", presets_path):
        # First press — arms confirm toast referencing Custom1 (overwrite).
        await ui._handle_action(UiAction.SELECT)
        msg = ui._snapshot.settings_message or ""
        assert "overwrite" in msg.lower(), f"Expected overwrite toast, got: {msg!r}"
        assert "Custom1" in msg, f"Expected Custom1 in toast, got: {msg!r}"

        # Second press — commits.
        await ui._handle_action(UiAction.SELECT)

    # File written and preset label stays Custom1.
    assert presets_path.exists()
    assert ui._config.adjustments.preset == "Custom1"
    # The saved profile should reflect saturation=30 (factor 1.3).
    updated_presets = load_user_presets(presets_path)
    import pytest as _pytest

    assert updated_presets["Custom1"].saturation == _pytest.approx(1.3, abs=0.02)


@pytest.mark.asyncio
async def test_preset_picker_shows_empty_slots_when_no_user_customs(tmp_path: Path) -> None:
    """Preset picker always shows all 11 options (5 built-ins + 6 Custom slots)."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    # No user presets loaded.
    ui._user_presets = {}
    options = ui._preset_picker_options()
    assert len(options) == 11, f"Expected 11 options, got {len(options)}"
    # All 6 custom slots should be present (as empty entries).
    from instantlink_bridge.ui.settings import USER_PRESET_SLOT_NAMES

    for slot in USER_PRESET_SLOT_NAMES:
        values = [opt.value for opt in options]
        assert slot in values, f"{slot} not in picker options"
    # Labels for empty slots include '(empty)'.
    empty_labels = [opt.label for opt in options if "(empty)" in opt.label]
    assert len(empty_labels) == 6, f"Expected 6 empty-slot labels, got {len(empty_labels)}"


@pytest.mark.asyncio
async def test_empty_custom_slot_key1_shows_toast(tmp_path: Path) -> None:
    """KEY1 on an empty Custom slot shows a toast instead of loading."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    ui._user_presets = {}
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)

    # Open preset picker (row 0, SELECT).
    await ui._handle_action(UiAction.SELECT)
    assert ui._snapshot.settings_title == "Preset"

    # Navigate to Custom1 (index 5: 0=Default 1=Vivid 2=Soft 3=B&W 4=Instax Film 5=Custom1).
    for _ in range(5):
        await ui._handle_action(UiAction.DOWN)

    # KEY1 on empty slot → toast, preset not changed.
    await ui._handle_action(UiAction.SELECT)
    msg = ui._snapshot.settings_message or ""
    assert "empty" in msg.lower() or "save" in msg.lower(), (
        f"Expected empty-slot toast, got: {msg!r}"
    )
    # Config preset unchanged.
    assert ui._config.adjustments.preset == "Default"


# ---------------------------------------------------------------------------
# Plan 036 audit follow-up — item 2 (autoname) + item 3 (discoverability)
# ---------------------------------------------------------------------------


def test_user_preset_picker_hint_specifies_key3_hold(tmp_path: Path) -> None:
    """Picker row hint for a saved Custom slot must mention K3 and hold."""
    from instantlink_bridge.imaging.postprocess import AdjustmentProfile

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    # Inject a populated custom slot so it gets the populated-slot hint.
    ui._user_presets = {"Custom1": AdjustmentProfile(saturation=1.5)}
    options = ui._preset_picker_options()
    rows = ui._preset_picker_rows(options, "Default")
    # Custom1 is at index 5 (after 5 built-ins).
    custom1_hint = rows[5].hint
    assert "K3" in custom1_hint, f"Hint must mention K3, got: {custom1_hint!r}"
    assert "hold" in custom1_hint.lower(), f"Hint must mention hold, got: {custom1_hint!r}"


@pytest.mark.asyncio
async def test_slots_full_toast_mentions_overwrite_path(tmp_path: Path) -> None:
    """When all 6 custom slots are full, the toast mentions K3-hold and overwrite."""
    from instantlink_bridge.imaging.postprocess import AdjustmentProfile

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\nsaturation = 30\n', encoding="utf-8")
    presets_path = tmp_path / "presets.toml"
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    # Fill all 6 custom slots so save is blocked — write to the patched path too.
    from instantlink_bridge.imaging.presets import save_user_presets

    full_presets = {f"Custom{i}": AdjustmentProfile(saturation=1.0 + i * 0.1) for i in range(1, 7)}
    save_user_presets(presets_path, full_presets)
    ui._user_presets = full_presets
    ui._show_settings(page=SettingsPage.ADJUSTMENTS)

    import unittest.mock as mock

    with mock.patch("instantlink_bridge.imaging.presets.USER_PRESETS_PATH", presets_path):
        # Navigate to "Save current" row (index 9 on the Adjustments page;
        # plan 037 phase 4 inserted Datestamp format between Datestamp/Watermark).
        for _ in range(9):
            await ui._handle_action(UiAction.DOWN)
        await ui._handle_action(UiAction.SELECT)

    msg = ui._snapshot.settings_message or ""
    assert "K3" in msg, f"Slots-full toast must mention K3, got: {msg!r}"
    assert "overwrite" in msg.lower(), f"Slots-full toast must mention overwrite, got: {msg!r}"


def test_save_preset_help_text_mentions_management(tmp_path: Path) -> None:
    """ADJUST_SAVE_CUSTOM help text must mention K3 and hold (management path)."""
    from instantlink_bridge.ui.settings import SettingKey, setting_help_text

    text = setting_help_text(SettingKey.ADJUST_SAVE_CUSTOM)
    assert "K3" in text, f"Help text must mention K3, got: {text!r}"
    assert "hold" in text.lower(), f"Help text must mention hold, got: {text!r}"


# -----------------------------------------------------------------------------
# Plan 037 phase 1 — settings audit batch (#1 + #2 + #3 + #4)
# -----------------------------------------------------------------------------


def _make_settings_ui(config: BridgeConfig) -> tuple[BridgeUi, _FakeDisplay]:
    """Build a minimal BridgeUi for settings-only tests."""

    display = _FakeDisplay()
    ui = BridgeUi(
        config,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    return ui, display


def test_visible_keys_hides_battery_on_x306() -> None:
    """Plan 037 #1: X306 backend has no host telemetry; hide the battery row."""

    config = BridgeConfig(power=PowerConfig(backend=PowerBackend.X306))
    ui, _ = _make_settings_ui(config)

    visible = ui._visible_keys_for_page(SettingsPage.SYSTEM)

    assert SettingKey.SYSTEM_BATTERY_INFO not in visible


def test_visible_keys_hides_battery_on_none_backend() -> None:
    """Plan 037 #1: NONE backend has no telemetry either."""

    config = BridgeConfig(power=PowerConfig(backend=PowerBackend.NONE))
    ui, _ = _make_settings_ui(config)

    visible = ui._visible_keys_for_page(SettingsPage.SYSTEM)

    assert SettingKey.SYSTEM_BATTERY_INFO not in visible


def test_visible_keys_keeps_battery_on_pisugar() -> None:
    """Plan 037 #1: PiSugar exposes telemetry; battery row stays."""

    config = BridgeConfig(power=PowerConfig(backend=PowerBackend.PISUGAR))
    ui, _ = _make_settings_ui(config)

    visible = ui._visible_keys_for_page(SettingsPage.SYSTEM)

    assert SettingKey.SYSTEM_BATTERY_INFO in visible


def test_idle_poweroff_row_value_off() -> None:
    """Plan 037 #2: disabled idle poweroff renders as 'Off', not 'No'."""

    config = BridgeConfig(power=PowerConfig(idle_poweroff_enabled=False))
    ui, _ = _make_settings_ui(config)

    row = ui._settings_row_for_key(SettingKey.SYSTEM_IDLE_POWEROFF, "")

    assert row.label == "Idle poweroff"
    assert row.value == "Off"


def test_idle_poweroff_row_value_after_10_min() -> None:
    """Plan 037 #2: enabled idle poweroff renders the timeout explicitly."""

    config = BridgeConfig(power=PowerConfig(idle_poweroff_enabled=True))
    ui, _ = _make_settings_ui(config)

    row = ui._settings_row_for_key(SettingKey.SYSTEM_IDLE_POWEROFF, "")

    assert row.label == "Idle poweroff"
    assert row.value == "After 10 min"


def test_system_idle_info_enum_removed() -> None:
    """Plan 037 polish #12: SYSTEM_IDLE_INFO enum value is gone for good.

    The enum was marked deprecated in plan 037 phase 1 with a "keep for
    one release" comment. The release shipped and no in-memory or
    persisted reference can resurrect it, so we remove it cleanly.
    """

    assert not hasattr(SettingKey, "SYSTEM_IDLE_INFO")


def test_section_header_keys_cover_all_three_dividers() -> None:
    """Plan 037 #3 + #4: SECTION_HEADER_KEYS lists the three known dividers."""

    assert SECTION_HEADER_KEYS == frozenset(
        {
            SettingKey.NETWORK_DIAGNOSTICS_HEADER,
            SettingKey.PRINT_ADVANCED_HEADER,
            SettingKey.SYSTEM_PERSONALISATION_HEADER,
        }
    )


def test_section_header_keys_are_handled() -> None:
    """Plan 037 #3 + #4: section headers must satisfy the 'handled' guard."""

    assert SECTION_HEADER_KEYS <= HANDLED_SETTING_KEYS


@pytest.mark.asyncio
async def test_nav_skips_section_header_forward() -> None:
    """Plan 037 #3 + #4: DOWN over a header skips to the next non-header row."""

    ui, _display = _make_settings_ui(BridgeConfig())
    ui._show_settings(page=SettingsPage.NETWORK)
    # NETWORK rows: 5 = FTP_PASSWORD_INFO, 6 = NETWORK_DIAGNOSTICS_HEADER,
    # 7 = NETWORK_BLUETOOTH_INFO. Position the cursor at 5 and press DOWN.
    ui._settings_indices[SettingsPage.NETWORK] = 5
    ui._snapshot = replace(ui._snapshot, selected_index=5)

    await ui._handle_settings_action(UiAction.DOWN)

    keys = ui._visible_keys_for_page(SettingsPage.NETWORK)
    new_index = ui._snapshot.selected_index
    assert keys[new_index] is SettingKey.NETWORK_BLUETOOTH_INFO
    assert keys[new_index] is not SettingKey.NETWORK_DIAGNOSTICS_HEADER


@pytest.mark.asyncio
async def test_nav_skips_section_header_backward() -> None:
    """Plan 037 #3 + #4: UP over a header lands on the row before it."""

    ui, _ = _make_settings_ui(BridgeConfig())
    ui._show_settings(page=SettingsPage.NETWORK)
    # Start on the row after the divider (NETWORK_BLUETOOTH_INFO, index 7).
    ui._settings_indices[SettingsPage.NETWORK] = 7
    ui._snapshot = replace(ui._snapshot, selected_index=7)

    await ui._handle_settings_action(UiAction.UP)

    keys = ui._visible_keys_for_page(SettingsPage.NETWORK)
    new_index = ui._snapshot.selected_index
    assert keys[new_index] is SettingKey.FTP_PASSWORD_INFO
    assert keys[new_index] is not SettingKey.NETWORK_DIAGNOSTICS_HEADER


@pytest.mark.asyncio
async def test_activate_section_header_is_noop() -> None:
    """Plan 037 #3 + #4: KEY1/RIGHT on a header is a no-op (defensive backstop)."""

    ui, _display = _make_settings_ui(BridgeConfig())
    ui._show_settings(page=SettingsPage.NETWORK)
    before = ui._snapshot

    await ui._activate_setting(SettingKey.NETWORK_DIAGNOSTICS_HEADER)

    # No state change: same snapshot identity (no replace) and no new
    # rendered frame after the no-op.
    assert ui._snapshot is before


@pytest.mark.asyncio
async def test_initial_selection_skips_persisted_header() -> None:
    """Plan 037 #3 + #4: persisted header index advances to the next non-header."""

    ui, _display = _make_settings_ui(BridgeConfig())
    network_keys = SETTINGS_BY_PAGE[SettingsPage.NETWORK]
    header_index = network_keys.index(SettingKey.NETWORK_DIAGNOSTICS_HEADER)
    ui._settings_indices[SettingsPage.NETWORK] = header_index

    ui._show_settings(page=SettingsPage.NETWORK)

    landed_key = network_keys[ui._snapshot.selected_index]
    assert landed_key not in SECTION_HEADER_KEYS
    # Forward-only advance: should land on the row immediately after.
    assert landed_key is SettingKey.NETWORK_BLUETOOTH_INFO


# -----------------------------------------------------------------------------
# Plan 037 phase 4 — customizable watermark + datestamp format presets
# -----------------------------------------------------------------------------


def test_adjustments_page_includes_datestamp_format_row() -> None:
    """The Datestamp format picker row is registered on the Adjustments page."""

    assert SettingKey.ADJUST_DATESTAMP_FORMAT in SETTINGS_BY_PAGE[SettingsPage.ADJUSTMENTS]


def test_datestamp_format_picker_options() -> None:
    """The picker exposes all 5 macOS-aligned preset values, in the expected order."""
    from instantlink_bridge.config import DatestampFormat
    from instantlink_bridge.ui.settings import setting_options

    options = setting_options(SettingKey.ADJUST_DATESTAMP_FORMAT)

    assert len(options) == 5
    values = [opt.value for opt in options]
    for expected in (
        DatestampFormat.QUARTZ_DATE,
        DatestampFormat.OLYMPUS,
        DatestampFormat.CONTAX,
        DatestampFormat.MODERN,
        DatestampFormat.LAB_PRINT,
    ):
        assert expected in values, f"{expected} not in picker options"


def test_datestamp_format_selected_option_index_reflects_config() -> None:
    """The picker highlights the option whose value matches the configured format."""
    from dataclasses import replace as _replace

    from instantlink_bridge.config import AdjustmentsConfig, DatestampFormat
    from instantlink_bridge.ui.settings import (
        DATESTAMP_FORMAT_OPTIONS,
        selected_option_index,
    )

    config = BridgeConfig(adjustments=AdjustmentsConfig(datestamp_format=DatestampFormat.OLYMPUS))
    index = selected_option_index(config, SettingKey.ADJUST_DATESTAMP_FORMAT)
    assert DATESTAMP_FORMAT_OPTIONS[index].value is DatestampFormat.OLYMPUS

    # And Contax → Contax round-trip.
    config = _replace(
        config,
        adjustments=_replace(config.adjustments, datestamp_format=DatestampFormat.CONTAX),
    )
    index = selected_option_index(config, SettingKey.ADJUST_DATESTAMP_FORMAT)
    assert DATESTAMP_FORMAT_OPTIONS[index].value is DatestampFormat.CONTAX


def test_watermark_row_shows_current_text_when_set() -> None:
    """Plan 037 phase 4: enabled watermark with text shows 'On · "Text"' in the row.

    Plan 037 polish #4: the "On" prefix moves to ``i18n_value_prefix`` so
    zh-Hans translates it; ``value`` carries only the raw user suffix.
    """
    from instantlink_bridge.config import AdjustmentsConfig

    config = BridgeConfig(adjustments=AdjustmentsConfig(watermark=True, watermark_text="Hello"))
    ui, _ = _make_settings_ui(config)
    row = ui._settings_row_for_key(SettingKey.ADJUST_WATERMARK, printer_name="none")
    assert "Hello" in row.value
    assert row.i18n_value_prefix == "On"


def test_watermark_row_shows_no_text_hint_when_empty() -> None:
    """Enabled watermark with empty text shows the explicit '(no text)' hint."""
    from instantlink_bridge.config import AdjustmentsConfig

    config = BridgeConfig(adjustments=AdjustmentsConfig(watermark=True, watermark_text=""))
    ui, _ = _make_settings_ui(config)
    row = ui._settings_row_for_key(SettingKey.ADJUST_WATERMARK, printer_name="none")
    # "On · (no text)" remains a single phrase in i18n; no prefix split.
    assert row.value == "On · (no text)"


def test_watermark_row_off_when_disabled() -> None:
    """A disabled watermark still reads 'Off' regardless of stored text."""
    from instantlink_bridge.config import AdjustmentsConfig

    config = BridgeConfig(adjustments=AdjustmentsConfig(watermark=False, watermark_text="Hello"))
    ui, _ = _make_settings_ui(config)
    row = ui._settings_row_for_key(SettingKey.ADJUST_WATERMARK, printer_name="none")
    assert row.value == "Off"


def test_watermark_row_truncates_long_text() -> None:
    """Watermark text >14 chars gets ellipsised to fit the 240 px row."""
    from instantlink_bridge.config import AdjustmentsConfig

    long_text = "Hongjun and the Watermark"
    config = BridgeConfig(adjustments=AdjustmentsConfig(watermark=True, watermark_text=long_text))
    ui, _ = _make_settings_ui(config)
    row = ui._settings_row_for_key(SettingKey.ADJUST_WATERMARK, printer_name="none")
    assert "…" in row.value
    # The truncated body must not contain the tail of the source string.
    assert "Watermark" not in row.value


def test_setting_datestamp_format_writes_config() -> None:
    """config_with_setting_value writes the picked DatestampFormat into adjustments."""
    from instantlink_bridge.config import DatestampFormat
    from instantlink_bridge.ui.settings import config_with_setting_value

    config = BridgeConfig()
    updated = config_with_setting_value(
        config, SettingKey.ADJUST_DATESTAMP_FORMAT, DatestampFormat.OLYMPUS
    )
    assert updated.adjustments.datestamp_format is DatestampFormat.OLYMPUS


def test_datestamp_format_row_value_shows_current_preset_name() -> None:
    """The Datestamp format row value matches the picker label for the active enum."""
    from instantlink_bridge.config import AdjustmentsConfig, DatestampFormat

    config = BridgeConfig(adjustments=AdjustmentsConfig(datestamp_format=DatestampFormat.CONTAX))
    ui, _ = _make_settings_ui(config)
    row = ui._settings_row_for_key(SettingKey.ADJUST_DATESTAMP_FORMAT, printer_name="none")
    assert row.value == "Contax"


# ---------------------------------------------------------------------------
# Plan 037 polish: 15-fix audit batch
# ---------------------------------------------------------------------------


def test_section_header_row_carries_is_header_flag() -> None:
    """Plan 037 polish #1: section divider rows are tagged with
    ``is_header=True`` so the renderer can style them as labels rather
    than greyed-out picker rows."""

    ui, _ = _make_settings_ui(BridgeConfig())
    for key in (
        SettingKey.NETWORK_DIAGNOSTICS_HEADER,
        SettingKey.PRINT_ADVANCED_HEADER,
        SettingKey.SYSTEM_PERSONALISATION_HEADER,
    ):
        row = ui._settings_row_for_key(key, printer_name="none")
        assert row.is_header, f"{key} should be flagged is_header=True"
        assert row.value == ""


def test_non_header_rows_have_is_header_false() -> None:
    """Plan 037 polish #1 regression guard: ordinary rows must not be
    flagged as headers even when their value happens to be blank."""

    ui, _ = _make_settings_ui(BridgeConfig())
    row = ui._settings_row_for_key(SettingKey.OPEN_NETWORK, printer_name="none")
    assert row.is_header is False


def test_camera_link_label_replaces_wifi_mode() -> None:
    """Plan 037 polish #8: FTP_RECEIVE_MODE row label renamed from
    "Wi-Fi Mode" to "Camera link" (matches the help-text vocabulary)."""

    ui, _ = _make_settings_ui(BridgeConfig())
    row = ui._settings_row_for_key(SettingKey.FTP_RECEIVE_MODE, printer_name="none")
    assert row.label == "Camera link"


def test_hue_help_text_no_trailing_period() -> None:
    """Plan 037 polish #14: Hue help string no longer ends with a period
    so it matches sibling Saturation/Exposure/Sharpness help strings."""
    from instantlink_bridge.ui.settings import SettingKey, setting_help_text

    help_text = setting_help_text(SettingKey.ADJUST_HUE)
    assert help_text == "Tint. Left toward orange, right toward blue"
    assert not help_text.endswith(".")


def test_preset_modified_marker_is_edited_text_not_asterisk(tmp_path: Path) -> None:
    """Plan 037 polish #6: the trailing modified marker is the
    self-describing " · edited" badge, not the cryptic " *"."""

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Vivid"\nsaturation = 7\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    row = ui._settings_row_for_key(SettingKey.ADJUST_PRESET, printer_name="none")
    assert " · edited" in row.value
    assert " *" not in row.value


def test_watermark_row_on_with_text_translates_prefix_in_zh_hans() -> None:
    """Plan 037 polish #4: watermark row's "On" prefix moves into
    ``i18n_value_prefix`` so zh-Hans translates the prefix while the
    user text passes through unchanged."""
    from instantlink_bridge.config import AdjustmentsConfig
    from instantlink_bridge.ui.i18n import Language, t

    config = BridgeConfig(adjustments=AdjustmentsConfig(watermark=True, watermark_text="Hello"))
    ui, _ = _make_settings_ui(config)
    row = ui._settings_row_for_key(SettingKey.ADJUST_WATERMARK, printer_name="none")
    assert row.i18n_value_prefix == "On"
    # Simulate the render-layer compose: prefix translates, suffix stays raw.
    composed = t(row.i18n_value_prefix, Language.ZH_HANS) + row.value
    assert composed == '开 · "Hello"'


def test_empty_preset_slot_picker_has_no_hint(tmp_path: Path) -> None:
    """Plan 037 polish #3: empty Custom slots in the preset picker carry
    an empty hint (the row label already says "(empty)") instead of the
    nonsense "KEY1 empty"."""

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Default"\n', encoding="utf-8")
    display = _FakeDisplay()
    ui = BridgeUi(
        load_config(config_path),
        config_path=config_path,
        display=display,
        input_device=NullInput(),
        pairer=_FakePairer([]),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )
    options = ui._preset_picker_options()
    rows = ui._preset_picker_rows(options, "Default")
    empty_rows = [r for r in rows if "(empty)" in r.label]
    assert len(empty_rows) == 6, "expected 6 empty Custom slots in a fresh config"
    for r in empty_rows:
        assert r.hint == ""


async def _enter_adjustments_edit_mode(ui: BridgeUi, key: SettingKey) -> None:
    """Pump the UI into the focused-edit mode for ``key`` by selecting it."""

    ui._show_settings(page=SettingsPage.ADJUSTMENTS)
    # Walk the cursor to the target row.
    keys = ui._visible_keys_for_page(SettingsPage.ADJUSTMENTS)
    index = keys.index(key)
    ui._snapshot = replace(ui._snapshot, selected_index=index)
    ui._settings_indices[SettingsPage.ADJUSTMENTS] = index
    await ui._activate_setting(key)


@pytest.mark.asyncio
async def test_toggle_edit_message_clears_on_up_down() -> None:
    """Plan 037 polish #11: KEY3 help text shown in toggle-edit mode
    clears as soon as UP/DOWN flips the toggle, so the user's live edit
    isn't visually masked by a stale help overlay."""

    ui, _ = _make_settings_ui(BridgeConfig())
    await _enter_adjustments_edit_mode(ui, SettingKey.ADJUST_WATERMARK)
    # Show KEY3 help (simulates the user pressing KEY3 in edit mode).
    await ui._handle_adjustment_edit_action(UiAction.HELP)
    assert ui._snapshot.settings_message is not None
    # A UP press flips the toggle; the help message must clear at the
    # same time so the live edit is visible.
    await ui._handle_adjustment_edit_action(UiAction.UP)
    assert ui._snapshot.settings_message is None


@pytest.mark.asyncio
async def test_slider_edit_message_clears_on_up_down() -> None:
    """Plan 037 polish #11 (sibling): KEY3 help text shown in slider-edit
    mode clears when the user nudges the slider with UP/DOWN."""

    ui, _ = _make_settings_ui(BridgeConfig())
    await _enter_adjustments_edit_mode(ui, SettingKey.ADJUST_SATURATION)
    await ui._handle_adjustment_edit_action(UiAction.HELP)
    assert ui._snapshot.settings_message is not None
    await ui._handle_adjustment_edit_action(UiAction.UP)
    assert ui._snapshot.settings_message is None
