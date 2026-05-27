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
    OFFLINE_BACKOFF_BASE_S,
    OFFLINE_BACKOFF_CAP_S,
    OFFLINE_MESSAGE_AFTER_MISSES,
    OFFLINE_STATUS_RETRY_S,
    RENDER_TICK_S,
    RESTART_PRINTER_RETRY_S,
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
from instantlink_bridge.ui.settings import (
    HANDLED_SETTING_KEYS,
    SETTING_HELP_TEXT,
    SETTINGS_BY_PAGE,
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

    assert camera_status_message_for_health(health, FtpReceiveMode.WIRED) == "USB debug off"
    assert camera_transport_message_for_health(health, FtpReceiveMode.WIRED) == "USB debug off"


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
    assert camera_status_message_for_health(health, FtpReceiveMode.WIRED) == "USB debug no IP"
    assert camera_transport_message_for_health(health, FtpReceiveMode.WIRED) == "USB debug no IP"


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

    assert camera_status_message_for_health(health, FtpReceiveMode.WIRED) == "USB debug only"
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
    # First offline tick at the miss threshold uses the backoff base, not a flat 5s.
    assert ui._printer_status_retry_delay(False) == OFFLINE_BACKOFF_BASE_S

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
        ui._show_printer_searching_if_retrying(printer, "Searching for printer")
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
        ui._snapshot = ui._build_snapshot(mode=UiMode.PRINTER_SEARCHING, paired_printer=printer)
        for _ in range(50):
            if display.snapshots and display.snapshots[-1].mode is UiMode.PRINTER_SEARCHING:
                break
            await asyncio.sleep(0.01)
        assert display.snapshots, "render tick never rendered"
        assert display.snapshots[-1].mode is UiMode.PRINTER_SEARCHING
        render_count = len(display.snapshots)
        # An unchanged snapshot must not be re-rendered (short-circuit keeps the tick cheap).
        await asyncio.sleep(RENDER_TICK_S * 2)
        assert len(display.snapshots) == render_count
    finally:
        tick_task.cancel()
        with suppress(asyncio.CancelledError):
            await tick_task


def test_offline_status_retry_delay_uses_exponential_backoff_with_cap() -> None:
    ui = BridgeUi(
        BridgeConfig(),
        display=_FakeDisplay(),
        input_device=NullInput(),
        pairer=_FakePairer([]),
        status_provider=_FakeStatusProvider(),
        wifi_mode_setter=_unused_wifi_mode_setter,
    )

    # Below the offline threshold we use the short, near-immediate retry.
    ui._printer_status_misses = OFFLINE_MESSAGE_AFTER_MISSES - 1
    assert ui._printer_status_retry_delay(False) == OFFLINE_STATUS_RETRY_S

    # At and beyond the threshold the delay grows geometrically: base, 2x, 4x, ...
    expected = OFFLINE_BACKOFF_BASE_S
    for extra in range(8):
        ui._printer_status_misses = OFFLINE_MESSAGE_AFTER_MISSES + extra
        delay = ui._printer_status_retry_delay(False)
        assert delay == min(expected, OFFLINE_BACKOFF_CAP_S)
        if delay >= OFFLINE_BACKOFF_CAP_S:
            assert delay == OFFLINE_BACKOFF_CAP_S
        expected *= 2

    # Far beyond the threshold the delay stays pinned at the cap.
    ui._printer_status_misses = OFFLINE_MESSAGE_AFTER_MISSES + 50
    assert ui._printer_status_retry_delay(False) == OFFLINE_BACKOFF_CAP_S


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
    for _ in range(3):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.DOWN)
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
    await ui._handle_action(UiAction.SELECT)
    for _ in range(4):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.SELECT)

    assert ui.config.printer.keepalive_interval_s == 10.0
    assert load_config(config_path).printer.keepalive_interval_s == 10.0
    assert display.snapshots[-1].settings_message == "Config not writable"
    assert display.snapshots[-1].settings_rows[4].label == "Keepalive"
    assert display.snapshots[-1].settings_rows[4].value == "10s"


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
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.HELP)

    assert display.snapshots[-1].settings_title == "Upload FTP"
    assert display.snapshots[-1].settings_message == "FTP sender joins this Wi-Fi"


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

    assert [row.label for row in display.snapshots[-1].settings_rows] == [
        "Printer",
        "Upload FTP",
        "Network",
        "Print",
        "System",
    ]

    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    for _ in range(5):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.RIGHT)

    assert calls == []
    assert _ftp_mode(ui) is FtpReceiveMode.WIRED
    assert display.snapshots[-1].settings_title == "FTP mode"
    assert [row.label for row in display.snapshots[-1].settings_rows] == [
        "Bridge Wi-Fi",
        "Same Wi-Fi adv",
    ]
    await ui._handle_action(UiAction.SELECT)

    assert calls == [WifiMode.HOTSPOT]
    assert _ftp_mode(ui) is FtpReceiveMode.HOTSPOT
    assert display.snapshots[-1].settings_title == "Upload FTP"
    assert display.snapshots[-1].settings_rows[0].label == "Bridge Wi-Fi"
    assert display.snapshots[-1].settings_rows[2].label == "FTP host"
    assert display.snapshots[-1].settings_rows[2].value == "192.168.8.1"
    assert display.snapshots[-1].settings_rows[3].label == "FTP user"
    assert display.snapshots[-1].settings_rows[4].label == "FTP pass"
    assert display.snapshots[-1].settings_rows[5].label == "FTP mode"
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

    await ui._handle_action(UiAction.SELECT)
    for _ in range(4):
        await ui._handle_action(UiAction.DOWN)

    assert display.snapshots[-1].settings_rows[4].label == "Keepalive"
    assert display.snapshots[-1].settings_rows[4].hint == "Right/KEY1 choose"


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
    assert display.snapshots[-1].settings_message == "Choose category"
    assert all(row.value == "" for row in display.snapshots[-1].settings_rows)

    await ui._handle_action(UiAction.DOWN)

    assert display.snapshots[-1].settings_rows[display.snapshots[-1].selected_index].label == (
        "Upload FTP"
    )
    assert display.snapshots[-1].settings_message == "Choose category"

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
    for _ in range(3):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    for _ in range(2):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.HELP)

    assert display.snapshots[-1].settings_title == "Print"
    assert display.snapshots[-1].settings_rows[2].label == "JPEG quality"
    assert display.snapshots[-1].settings_message == "JPEG quality sent to printer"


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
    for _ in range(2):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.LEFT)

    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].settings_title == "Settings"
    assert display.snapshots[-1].settings_rows[0].label == "Printer"
    assert display.snapshots[-1].settings_message == "Choose category"


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

    for _ in range(1):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    rows = display.snapshots[-1].settings_rows
    assert rows[3].label == "FTP user"
    assert rows[3].value == "ib"
    assert rows[4].label == "FTP pass"
    assert rows[4].value == "12345678"


@pytest.mark.asyncio
async def test_settings_menu_shows_hotspot_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ssid_path = tmp_path / "hotspot.ssid"
    psk_path = tmp_path / "hotspot.psk"
    ssid_path.write_text("LinkBrdg-TEST1234\n", encoding="utf-8")
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
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    rows = display.snapshots[-1].settings_rows
    assert rows[0].label == "Bridge Wi-Fi"
    assert rows[0].value == "LinkBrdg-TEST1234"
    assert rows[1].label == "Wi-Fi PIN"
    assert rows[1].value == "12345678"
    assert rows[2].label == "FTP host"
    assert rows[2].value == "192.168.8.1"


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
    for _ in range(3):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    for _ in range(3):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.RIGHT)
    assert not ui.config.workflow.allow_print_without_film
    assert display.snapshots[-1].settings_title == "No-film test"
    await ui._handle_action(UiAction.RIGHT)
    await ui._handle_action(UiAction.SELECT)

    assert ui.config.workflow.allow_print_without_film
    assert display.snapshots[-1].settings_rows[3].label == "No-film test"
    assert display.snapshots[-1].settings_rows[3].value == "On"


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
    ui._camera_transport_message = "USB debug 192.168.7.1"
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
    for _ in range(2):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    rows = display.snapshots[-1].settings_rows
    assert [row.label for row in rows] == [
        "Bridge FTP",
        "Bridge Wi-Fi",
        "Wi-Fi PIN",
        "Bluetooth",
        "Same Wi-Fi adv",
        "USB debug",
    ]
    assert rows[0].value == "192.168.8.1"
    assert rows[3].value == "connected"
    assert rows[4].value == "192.168.5.149"
    assert rows[5].value == "SSH 192.168.7.1"


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
    ui._camera_transport_message = "USB debug no IP"

    await ui._handle_action(UiAction.SELECT)
    for _ in range(2):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

    rows = display.snapshots[-1].settings_rows
    assert rows[-1].label == "USB debug"
    assert rows[-1].value == "no IP"


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

    await ui._handle_action(UiAction.SELECT)
    for _ in range(4):
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

    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)
    await asyncio.sleep(0)

    # BLE reset releases the cached session and schedules a fresh status poll. The provider's
    # transient ``close`` now happens on the poll task's own teardown, not inline on the action
    # loop, so it is no longer asserted here (the reset must never block input/render).
    assert status_provider.close_cached_calls == 1
    assert ui._status_task is not None
    assert display.snapshots[-1].mode is UiMode.SETTINGS
    assert display.snapshots[-1].settings_title == "Printer"
    assert display.snapshots[-1].settings_message == "BLE link reset"
    assert display.snapshots[-1].paired_printer == replace(printer, model=PrinterModel.SQUARE)
    await ui._cancel_status_refresh()


@pytest.mark.asyncio
async def test_settings_system_page_shows_device_and_versions() -> None:
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

    await ui._handle_action(UiAction.SELECT)
    for _ in range(4):
        await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.SELECT)

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

    assert display.snapshots[-1].settings_message == "Unique bridge identifier"


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

    await ui._handle_action(UiAction.SELECT)
    for _ in range(4):
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
    assert display.snapshots[-1].settings_rows[display.snapshots[-1].selected_index].value == "On"


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

    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.SELECT)
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.DOWN)
    await ui._handle_action(UiAction.RIGHT)

    assert not pairer.forgot
    assert display.snapshots[-1].settings_message == "Press again to forget"

    await ui._handle_action(UiAction.RIGHT)

    assert pairer.forgot
    assert display.snapshots[-1].settings_message == "Printer forgotten"
    assert display.snapshots[-1].paired_printer is None


@pytest.mark.asyncio
async def test_printing_mode_ignores_inputs_until_job_finishes() -> None:
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

    assert display.snapshots[-1].settings_rows[3].label == "Bluetooth"
    assert display.snapshots[-1].settings_rows[3].value == "searching"


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
    assert any(
        row.label == "Power" and row.value == "Battery case"
        for row in display.snapshots[-1].settings_rows
    )
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
    assert display.snapshots[-1].settings_title == "Upload FTP"
    assert display.snapshots[-1].settings_message == "Wi-Fi + FTP credentials"
    assert [row.label for row in display.snapshots[-1].settings_rows[:5]] == [
        "Bridge Wi-Fi",
        "Wi-Fi PIN",
        "FTP host",
        "FTP user",
        "FTP pass",
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
    assert display.snapshots[-1].settings_title == "Upload FTP"
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
