from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, cast

import pytest

from instantlink_bridge.ble.client import DiscoveredPrinter
from instantlink_bridge.ble.instantlink import (
    CONNECT_STAGE_NOTIFICATION_SUBSCRIBE,
    ERROR_BLE,
    ERROR_NO_FILM,
    ERROR_PRINT_REJECTED,
    ERROR_PRINTER_NOT_FOUND,
    InstantLinkBackend,
    InstantLinkBleError,
    InstantLinkPrinterNotFoundError,
)
from instantlink_bridge.ble.instax import PrinterStatus
from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.ble.session import (
    InstaxBleSessionManager,
    PrinterEndpoint,
    SessionRetryPolicy,
)
from instantlink_bridge.ui import status as status_module
from instantlink_bridge.ui.models import PairedPrinter
from instantlink_bridge.ui.status import (
    BlePrinterStatusProvider,
    ConnectedInstaxPrinter,
    InstantLinkPrinterStatusProvider,
    PrinterStatusUnavailableError,
    PrinterStatusUnavailableReason,
    has_matching_status_target,
    scan_bluez_instax_printers,
    scanner_diagnostics,
    select_status_target,
    status_target_for_visible_match,
)


class _FakeInstantLinkStatusLibrary:
    def __init__(
        self,
        *,
        status_rc: int = 0,
        split_film_rc: int = 0,
        battery_rc: int = 35,
    ) -> None:
        self.status_rc = status_rc
        self.split_film_rc = split_film_rc
        self.battery_rc = battery_rc
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.status_calls = 0
        self.keepalive_calls = 0
        self.keepalive_interval_calls: list[int] = []

    def instantlink_connect_named(self, _name: bytes, _duration: int) -> int:
        self.connect_calls += 1
        return 0

    def instantlink_battery(self) -> int:
        return self.battery_rc

    def instantlink_film_and_charging(self, out_film: object, out_charging: object) -> int:
        if self.split_film_rc != 0:
            return self.split_film_rc
        cast(Any, out_film)._obj.value = 7
        cast(Any, out_charging)._obj.value = 1
        return 0

    def instantlink_status(
        self,
        out_battery: object,
        out_film: object,
        out_charging: object,
        out_print_count: object,
    ) -> int:
        self.status_calls += 1
        if self.status_rc != 0:
            return self.status_rc
        cast(Any, out_battery)._obj.value = self.battery_rc
        cast(Any, out_film)._obj.value = 7
        cast(Any, out_charging)._obj.value = 1
        cast(Any, out_print_count)._obj.value = 123
        return 0

    def instantlink_device_name(self, out: object, _out_len: int) -> int:
        buffer = cast(Any, out)
        buffer.value = b"INSTAX-1N034655"
        return len(buffer.value)

    def instantlink_device_model(self, out: object, _out_len: int) -> int:
        buffer = cast(Any, out)
        buffer.value = b"Square Link"
        return len(buffer.value)

    def instantlink_disconnect(self) -> int:
        self.disconnect_calls += 1
        return 0

    def instantlink_keepalive(self) -> int:
        self.keepalive_calls += 1
        return 0

    def instantlink_set_keepalive_interval(self, seconds: int) -> int:
        self.keepalive_interval_calls.append(seconds)
        return 0


class _FakeInstantLinkScanLibrary:
    def instantlink_scan(self, _duration: int, out_json: object, _out_len: int) -> int:
        payload = b'["INSTAX-52006924 (ANDROID)","INSTAX-52006924 (IOS)"]'
        cast(Any, out_json).value = payload
        return len(payload)


def test_select_status_target_prefers_ios_advertisement_for_same_printer() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    candidates = [
        DiscoveredPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655(ANDROID)"),
        DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)"),
    ]

    target = select_status_target(selected, candidates)

    assert target == PairedPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655")


@pytest.mark.asyncio
async def test_instantlink_scan_filters_android_spp_advertisements() -> None:
    backend = InstantLinkBackend()
    backend._lib = cast(Any, _FakeInstantLinkScanLibrary())

    assert await backend.scan(1) == ["INSTAX-52006924"]


@pytest.mark.asyncio
async def test_instantlink_status_uses_combined_status_call() -> None:
    library = _FakeInstantLinkStatusLibrary()
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)

    status = await backend.status("INSTAX-1N034655")

    assert status.name == "INSTAX-1N034655"
    assert status.model is PrinterModel.SQUARE
    assert status.battery == 35
    assert status.film_remaining == 7
    assert status.is_charging is True
    assert status.print_count == 123
    assert library.status_calls == 1


@pytest.mark.asyncio
async def test_instantlink_backend_configures_core_keepalive() -> None:
    library = _FakeInstantLinkStatusLibrary()
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)

    await backend.configure_keepalive(10.4)
    await backend.configure_keepalive(None)
    await backend.keepalive_once()

    assert library.keepalive_interval_calls == [10, 0]
    assert library.keepalive_calls == 1


@pytest.mark.asyncio
async def test_instantlink_status_provider_configures_core_keepalive() -> None:
    library = _FakeInstantLinkStatusLibrary()
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)
    provider = InstantLinkPrinterStatusProvider(backend=backend)

    await provider.configure_keepalive(15)

    assert library.keepalive_interval_calls == [15]


@pytest.mark.asyncio
async def test_instantlink_status_maps_no_film_to_zero_remaining() -> None:
    library = _FakeInstantLinkStatusLibrary(status_rc=ERROR_NO_FILM)
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)

    status = await backend.status("INSTAX-1N034655")

    assert status.film_remaining == 0
    assert status.print_count is None


@pytest.mark.asyncio
async def test_instantlink_status_keeps_connection_on_transient_ble_failure() -> None:
    # A transient BLE error on an established connection must NOT tear the link down;
    # the persistent connection is reused on the next poll.
    library = _FakeInstantLinkStatusLibrary(status_rc=ERROR_PRINT_REJECTED)
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)

    with pytest.raises(InstantLinkBleError, match="status failed"):
        await backend.status("INSTAX-1N034655")

    assert library.disconnect_calls == 0


@pytest.mark.asyncio
async def test_instantlink_status_disconnects_when_printer_is_gone() -> None:
    # A definitive printer-gone error tears down the cached connection so the next poll
    # performs a fresh reconnect.
    library = _FakeInstantLinkStatusLibrary(status_rc=ERROR_PRINTER_NOT_FOUND)
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)

    with pytest.raises(InstantLinkPrinterNotFoundError):
        await backend.status("INSTAX-1N034655")

    assert library.disconnect_calls == 1


class _FakeInstantLinkConnectLibrary:
    """Fake library whose connect runs the progress callback then fails with a chosen rc."""

    def __init__(self, *, stages: list[int], connect_rc: int) -> None:
        self._stages = stages
        self._connect_rc = connect_rc
        self.disconnect_calls = 0

    def instantlink_connect_named_with_progress(
        self,
        _name: bytes,
        _duration: int,
        callback: object,
    ) -> int:
        emit = cast(Any, callback)
        for stage in self._stages:
            emit(stage, None)
        return self._connect_rc

    def instantlink_device_name(self, _out: object, _out_len: int) -> int:
        # Report "not connected" so _ensure_connected_blocking attempts a fresh connect.
        return ERROR_PRINTER_NOT_FOUND

    def instantlink_disconnect(self) -> int:
        self.disconnect_calls += 1
        return 0


@pytest.mark.asyncio
async def test_connect_failure_carries_highest_observed_stage() -> None:
    library = _FakeInstantLinkConnectLibrary(
        stages=[0, 3, 4, 5, CONNECT_STAGE_NOTIFICATION_SUBSCRIBE, 7, 10],
        connect_rc=ERROR_BLE,
    )
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)

    with pytest.raises(InstantLinkBleError) as excinfo:
        await backend.status("INSTAX-1N034655")

    # The terminal "failed" (10) sentinel is ignored; the recorded max is the real milestone.
    assert excinfo.value.connect_failure_stage == 7


@pytest.mark.asyncio
async def test_status_provider_flags_late_stage_write_failure_as_stale_bond() -> None:
    library = _FakeInstantLinkConnectLibrary(
        stages=[0, 3, 4, 5, CONNECT_STAGE_NOTIFICATION_SUBSCRIBE],
        connect_rc=ERROR_BLE,
    )
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)
    provider = InstantLinkPrinterStatusProvider(backend=backend)

    with pytest.raises(PrinterStatusUnavailableError) as excinfo:
        await provider.fetch(PairedPrinter(address="INSTANTLINK:X", name="INSTAX-1N034655"))

    assert excinfo.value.stale_bond_suspected is True


@pytest.mark.asyncio
async def test_status_provider_does_not_flag_early_stage_failure_as_stale_bond() -> None:
    library = _FakeInstantLinkConnectLibrary(
        stages=[0, 1, 2, 3, 4],
        connect_rc=ERROR_BLE,
    )
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)
    provider = InstantLinkPrinterStatusProvider(backend=backend)

    with pytest.raises(PrinterStatusUnavailableError) as excinfo:
        await provider.fetch(PairedPrinter(address="INSTANTLINK:X", name="INSTAX-1N034655"))

    assert excinfo.value.stale_bond_suspected is False


@pytest.mark.asyncio
async def test_instantlink_status_failure_logs_are_rate_limited(
    caplog: pytest.LogCaptureFixture,
) -> None:
    library = _FakeInstantLinkStatusLibrary(status_rc=ERROR_PRINT_REJECTED)
    backend = InstantLinkBackend()
    backend._lib = cast(Any, library)
    caplog.set_level(logging.WARNING, logger="instantlink_bridge.ble.instantlink")

    for _ in range(3):
        with pytest.raises(InstantLinkBleError, match="status failed"):
            await backend.status("INSTAX-1N034655")

    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("instantlink.status_failed_keep_connection")
    ]
    assert len(warning_messages) == 1
    # Transient failures never disconnect, even when repeated.
    assert library.disconnect_calls == 0


def test_select_status_target_falls_back_to_selected_when_not_visible() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    candidates = [
        DiscoveredPrinter(address="FA:AB:BC:00:00:01", name="INSTAX-OTHER(IOS)"),
    ]

    assert select_status_target(selected, candidates) == selected


def test_has_matching_status_target_checks_normalized_name() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    candidates = [
        DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)"),
    ]

    assert has_matching_status_target(selected, candidates)


def test_scanner_diagnostics_reports_visible_candidates_and_selected_visibility() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    candidates = [
        DiscoveredPrinter(address="fa:ab:bc:51:cc:e2", name="INSTAX-1N034655(IOS)"),
        DiscoveredPrinter(address="AA:BB:CC:00:00:01", name="INSTAX-OTHER"),
    ]

    diagnostics = scanner_diagnostics(selected, candidates)

    assert diagnostics.candidate_count == 2
    assert diagnostics.candidate_names == ("INSTAX-1N034655(IOS)", "INSTAX-OTHER")
    assert diagnostics.candidate_addresses == (
        "FA:AB:BC:51:CC:E2",
        "AA:BB:CC:00:00:01",
    )
    assert diagnostics.selected_visible


def test_status_target_for_visible_android_advertisement_derives_ios_endpoint() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    candidates = [
        DiscoveredPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655(ANDROID)"),
    ]

    assert status_target_for_visible_match(selected, candidates) == PairedPrinter(
        address="FA:AB:BC:51:CC:E2",
        name="INSTAX-1N034655",
    )


@pytest.mark.asyncio
async def test_ble_status_provider_reuses_connected_printer_for_keepalive() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    fake_connection = _FakeConnection()
    connect_calls: list[tuple[str, str | None]] = []

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        return [DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")]

    async def connector(address: str, name: str | None) -> ConnectedInstaxPrinter:
        connect_calls.append((address, name))
        return fake_connection

    provider = BlePrinterStatusProvider(
        scanner=scanner,
        connector=connector,
        bluez_scanner=_empty_bluez_scan,
    )

    first = await provider.fetch(selected)
    second = await provider.fetch(selected)
    await provider.close()

    assert first.film_remaining == 8
    assert second.film_remaining == 8
    assert connect_calls == [("FA:AB:BC:51:CC:E2", "INSTAX-1N034655")]
    assert fake_connection.protocol.status_calls == 2
    assert fake_connection.disconnect_calls == 0
    await provider.close_cached_session()
    assert fake_connection.disconnect_calls == 1


@pytest.mark.asyncio
async def test_ble_status_provider_can_use_short_lived_connections() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    fake_connection = _FakeConnection()
    connect_calls: list[str] = []
    scan_calls = 0

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        nonlocal scan_calls
        scan_calls += 1
        return [DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")]

    async def connector(address: str, _name: str | None) -> ConnectedInstaxPrinter:
        connect_calls.append(address)
        return fake_connection

    provider = BlePrinterStatusProvider(
        keep_connection_open=False,
        scanner=scanner,
        connector=connector,
        bluez_scanner=_empty_bluez_scan,
    )

    await provider.fetch(selected)
    await provider.fetch(selected)
    await provider.close()

    assert connect_calls == ["FA:AB:BC:51:CC:E2", "FA:AB:BC:51:CC:E2"]
    assert scan_calls == 1
    assert fake_connection.protocol.status_calls == 2
    assert fake_connection.disconnect_calls == 2


@pytest.mark.asyncio
async def test_ble_status_provider_hands_cached_connection_to_print_acquire() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    fake_connection = _FakeConnection()
    connect_calls: list[PrinterEndpoint] = []

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        return [DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")]

    async def connector(
        endpoint: PrinterEndpoint,
        _model_override: PrinterModel | None,
    ) -> _FakeConnection:
        connect_calls.append(endpoint)
        return fake_connection

    session_manager = InstaxBleSessionManager[ConnectedInstaxPrinter](connector)
    provider = BlePrinterStatusProvider(
        scanner=scanner,
        connector=_unused_connector,
        bluez_scanner=_empty_bluez_scan,
        session_manager=session_manager,
    )

    await provider.fetch(selected)
    await provider.close()
    lease = await session_manager.acquire_print(
        PrinterEndpoint(address=selected.address, name=selected.name)
    )

    assert lease.connected is fake_connection
    assert connect_calls == [PrinterEndpoint(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655")]
    assert fake_connection.disconnect_calls == 0

    await lease.release(keep_connected=False)
    assert fake_connection.disconnect_calls == 1


@pytest.mark.asyncio
async def test_ble_status_provider_reuses_known_endpoint_without_new_advertisement() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    connections = [_FakeConnection(), _FakeConnection()]
    scan_calls = 0
    connect_calls: list[tuple[str, str | None]] = []

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        nonlocal scan_calls
        scan_calls += 1
        if scan_calls > 1:
            raise AssertionError("cached endpoint should avoid a fresh advertisement scan")
        return [DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")]

    async def connector(address: str, name: str | None) -> ConnectedInstaxPrinter:
        connect_calls.append((address, name))
        return connections[len(connect_calls) - 1]

    provider = BlePrinterStatusProvider(
        keep_connection_open=False,
        scanner=scanner,
        connector=connector,
        bluez_scanner=_empty_bluez_scan,
    )

    first = await provider.fetch(selected)
    second = await provider.fetch(selected)

    assert first.film_remaining == 8
    assert second.film_remaining == 8
    assert scan_calls == 1
    assert connect_calls == [
        ("FA:AB:BC:51:CC:E2", "INSTAX-1N034655"),
        ("FA:AB:BC:51:CC:E2", "INSTAX-1N034655"),
    ]
    assert [connection.disconnect_calls for connection in connections] == [1, 1]


@pytest.mark.asyncio
async def test_ble_status_provider_releases_session_when_cancelled_after_status() -> None:
    selected = PrinterEndpoint(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655")
    fake_connection = _FakeConnection()
    fake_connection.protocol = cast(Any, _CancelAfterStatusProtocol())
    connect_calls = 0

    async def connector(
        _endpoint: PrinterEndpoint,
        _model_override: PrinterModel | None,
    ) -> _FakeConnection:
        nonlocal connect_calls
        connect_calls += 1
        return fake_connection

    session_manager = InstaxBleSessionManager[ConnectedInstaxPrinter](connector)
    provider = BlePrinterStatusProvider(
        keep_connection_open=False,
        scanner=_unused_scanner,
        connector=_unused_connector,
        bluez_scanner=_empty_bluez_scan,
        session_manager=session_manager,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider._fetch_endpoint(selected)

    assert fake_connection.disconnect_calls == 1
    assert connect_calls == 1

    lease = await asyncio.wait_for(session_manager.acquire_status(selected), timeout=1)
    await lease.release(keep_connected=False)
    assert connect_calls == 2


@pytest.mark.asyncio
async def test_ble_status_provider_reports_mini_film_format_without_known_model() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    fake_connection = _FakeConnection(model=PrinterModel.MINI)

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        return [DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")]

    async def connector(_address: str, _name: str | None) -> ConnectedInstaxPrinter:
        return fake_connection

    provider = BlePrinterStatusProvider(
        scanner=scanner,
        connector=connector,
        bluez_scanner=_empty_bluez_scan,
    )

    snapshot = await provider.fetch(selected)

    assert snapshot.film_remaining == 8
    assert snapshot.battery == 35
    assert snapshot.model is PrinterModel.MINI
    await provider.close_cached_session()


@pytest.mark.asyncio
async def test_ble_status_provider_preserves_known_mini_link3_for_ambiguous_mini_status() -> None:
    selected = PairedPrinter(
        address="88:B4:36:51:CC:E2",
        name="INSTAX-1N034655",
        model=PrinterModel.MINI_LINK3,
    )
    fake_connection = _FakeConnection(model=PrinterModel.MINI)

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        return [DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")]

    async def connector(_address: str, _name: str | None) -> ConnectedInstaxPrinter:
        return fake_connection

    provider = BlePrinterStatusProvider(
        scanner=scanner,
        connector=connector,
        bluez_scanner=_empty_bluez_scan,
    )

    snapshot = await provider.fetch(selected)

    assert snapshot.model is PrinterModel.MINI_LINK3
    await provider.close_cached_session()


@pytest.mark.asyncio
async def test_ble_status_provider_clears_stale_cached_session_after_status_failure() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    stale_connection = _FakeConnection(fail_status_on_call=2)
    fresh_connection = _FakeConnection()
    connections = [stale_connection, fresh_connection]
    connect_calls = 0

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        return [DiscoveredPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")]

    async def connector(_address: str, _name: str | None) -> ConnectedInstaxPrinter:
        nonlocal connect_calls
        connection = connections[connect_calls]
        connect_calls += 1
        return connection

    provider = BlePrinterStatusProvider(
        scanner=scanner,
        connector=connector,
        bluez_scanner=_empty_bluez_scan,
    )

    await provider.fetch(selected)
    snapshot = await provider.fetch(selected)

    assert snapshot.film_remaining == 8
    assert connect_calls == 2
    assert stale_connection.disconnect_calls == 1
    assert fresh_connection.protocol.status_calls == 1
    await provider.close_cached_session()


@pytest.mark.asyncio
async def test_ble_session_manager_retries_with_bounded_backoff() -> None:
    endpoint = PrinterEndpoint(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655")
    connection = _FakeConnection()
    attempts = 0
    sleeps: list[float] = []

    async def connector(
        _endpoint: PrinterEndpoint,
        _model_override: PrinterModel | None,
    ) -> _FakeConnection:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError(f"connect failed {attempts}")
        return connection

    async def sleep(delay_s: float) -> None:
        sleeps.append(delay_s)

    manager = InstaxBleSessionManager(
        connector,
        retry_policy=SessionRetryPolicy(max_attempts=3, backoff_s=(0.1, 0.2, 0.4)),
        sleep=sleep,
    )

    lease = await manager.acquire_status(endpoint)

    assert lease.connected is connection
    assert attempts == 3
    assert sleeps == [0.1, 0.2]

    await lease.release(keep_connected=False)


@pytest.mark.asyncio
async def test_ble_status_provider_exposes_scan_diagnostics_when_selected_is_not_visible() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        return [DiscoveredPrinter(address="AA:BB:CC:00:00:01", name="INSTAX-OTHER")]

    provider = BlePrinterStatusProvider(
        scanner=scanner,
        connector=_unused_connector,
        bluez_scanner=_empty_bluez_scan,
    )

    with pytest.raises(PrinterStatusUnavailableError) as exc_info:
        await provider.fetch(selected)

    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.candidate_count == 1
    assert diagnostics.candidate_names == ("INSTAX-OTHER",)
    assert diagnostics.candidate_addresses == ("AA:BB:CC:00:00:01",)
    assert not diagnostics.selected_visible
    assert provider.last_scan_diagnostics == diagnostics


@pytest.mark.asyncio
async def test_ble_status_provider_marks_selected_stale_after_repeated_absent_scans() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    now = [0.0]
    sleeps: list[float] = []

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        return []

    async def sleep(delay_s: float) -> None:
        sleeps.append(delay_s)
        now[0] += delay_s

    provider = BlePrinterStatusProvider(
        scanner=scanner,
        connector=_unused_connector,
        bluez_scanner=_empty_bluez_scan,
        unavailable_scan_interval_s=2.0,
        stale_selected_scan_interval_s=5.0,
        bluez_fallback_interval_s=60.0,
        stale_selected_after_misses=2,
        clock=lambda: now[0],
        sleep=sleep,
    )

    with pytest.raises(PrinterStatusUnavailableError) as first_exc:
        await provider.fetch(selected)
    with pytest.raises(PrinterStatusUnavailableError) as second_exc:
        await provider.fetch(selected)
    with pytest.raises(PrinterStatusUnavailableError) as third_exc:
        await provider.fetch(selected)

    assert first_exc.value.reason is PrinterStatusUnavailableReason.NOT_ADVERTISING
    assert first_exc.value.consecutive_misses == 1
    assert not first_exc.value.stale_selected
    assert second_exc.value.reason is PrinterStatusUnavailableReason.STALE_SELECTED
    assert second_exc.value.consecutive_misses == 2
    assert second_exc.value.stale_selected
    assert second_exc.value.retry_after_s == 5.0
    assert second_exc.value.status_message == "Hold K3 to re-pair"
    assert third_exc.value.reason is PrinterStatusUnavailableReason.STALE_SELECTED
    assert third_exc.value.consecutive_misses == 3
    assert third_exc.value.retry_after_s == 5.0
    assert sleeps == [2.0, 5.0]


@pytest.mark.asyncio
async def test_ble_status_provider_throttles_bluez_fallback_between_bleak_scans() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    now = [0.0]
    bleak_calls = 0
    bluez_calls = 0
    sleeps: list[float] = []

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        nonlocal bleak_calls
        bleak_calls += 1
        return []

    async def bluez_scanner(_timeout_s: float) -> list[PairedPrinter]:
        nonlocal bluez_calls
        bluez_calls += 1
        return []

    async def sleep(delay_s: float) -> None:
        sleeps.append(delay_s)

    provider = BlePrinterStatusProvider(
        scanner=scanner,
        connector=_unused_connector,
        bluez_scanner=bluez_scanner,
        bluez_fallback_interval_s=10.0,
        clock=lambda: now[0],
        sleep=sleep,
    )

    with pytest.raises(PrinterStatusUnavailableError):
        await provider.fetch(selected)
    now[0] = 1.0
    with pytest.raises(PrinterStatusUnavailableError):
        await provider.fetch(selected)
    now[0] = 11.0
    with pytest.raises(PrinterStatusUnavailableError):
        await provider.fetch(selected)

    assert bleak_calls == 3
    assert bluez_calls == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_ble_status_provider_bluez_fallback_defaults_to_ten_seconds() -> None:
    selected = PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")
    now = [0.0]
    bluez_calls = 0
    sleeps: list[float] = []

    async def scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
        return []

    async def bluez_scanner(_timeout_s: float) -> list[PairedPrinter]:
        nonlocal bluez_calls
        bluez_calls += 1
        return []

    async def sleep(delay_s: float) -> None:
        sleeps.append(delay_s)

    provider = BlePrinterStatusProvider(
        scanner=scanner,
        connector=_unused_connector,
        bluez_scanner=bluez_scanner,
        clock=lambda: now[0],
        sleep=sleep,
    )

    with pytest.raises(PrinterStatusUnavailableError):
        await provider.fetch(selected)
    now[0] = 9.0
    with pytest.raises(PrinterStatusUnavailableError):
        await provider.fetch(selected)
    now[0] = 10.0
    with pytest.raises(PrinterStatusUnavailableError):
        await provider.fetch(selected)

    assert bluez_calls == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_scan_bluez_instax_printers_serializes_bluetoothctl_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    scan_started = asyncio.Event()
    release_scan = asyncio.Event()
    running_scans = 0
    max_running_scans = 0

    async def fake_run_bluetoothctl(
        *args: str,
        timeout_seconds: int,
    ) -> str:
        nonlocal running_scans, max_running_scans
        _ = timeout_seconds
        calls.append(args)
        if args == ("--timeout", "1", "scan", "on"):
            running_scans += 1
            max_running_scans = max(max_running_scans, running_scans)
            scan_started.set()
            await release_scan.wait()
            running_scans -= 1
            return "Device FA:AB:BC:51:CC:E2 INSTAX-1N034655(IOS)"
        return ""

    monkeypatch.setattr(status_module, "_run_bluetoothctl", fake_run_bluetoothctl)

    first = asyncio.create_task(scan_bluez_instax_printers(1.0))
    second = asyncio.create_task(scan_bluez_instax_printers(1.0))
    await asyncio.wait_for(scan_started.wait(), timeout=1)

    assert max_running_scans == 1

    release_scan.set()
    results = await asyncio.gather(first, second)

    assert list(results) == [
        [PairedPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")],
        [PairedPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")],
    ]
    assert calls == [
        ("--timeout", "1", "scan", "on"),
        ("scan", "off"),
        ("--timeout", "1", "scan", "on"),
        ("scan", "off"),
    ]


async def _empty_bluez_scan(_timeout_s: float) -> list[PairedPrinter]:
    return []


async def _unused_connector(_address: str, _name: str | None) -> ConnectedInstaxPrinter:
    raise AssertionError("connector should not be called")


async def _unused_scanner(_timeout_s: float) -> Sequence[DiscoveredPrinter]:
    raise AssertionError("scanner should not be called")


class _FakeProtocol:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        model: PrinterModel = PrinterModel.MINI,
    ) -> None:
        self.status_calls = 0
        self._fail_on_call = fail_on_call
        self._model = model

    async def status(self) -> PrinterStatus:
        self.status_calls += 1
        if self._fail_on_call == self.status_calls:
            raise RuntimeError("stale session")
        return PrinterStatus(
            battery=35,
            is_charging=False,
            film_remaining=8,
            print_count=12,
            model=self._model,
            name="INSTAX-1N034655",
        )


class _FakeConnection:
    def __init__(
        self,
        *,
        fail_status_on_call: int | None = None,
        model: PrinterModel = PrinterModel.MINI,
    ) -> None:
        self.protocol = _FakeProtocol(fail_on_call=fail_status_on_call, model=model)
        self.disconnect_calls = 0

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


class _CancelAfterStatusProtocol:
    async def status(self) -> PrinterStatus:
        task = asyncio.current_task()
        if task is not None:
            task.cancel()
        return PrinterStatus(
            battery=35,
            is_charging=False,
            film_remaining=8,
            print_count=12,
            model=PrinterModel.MINI,
            name="INSTAX-1N034655",
        )
