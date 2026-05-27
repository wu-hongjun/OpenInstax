"""Printer status lookup for the LCD UI."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from weakref import WeakKeyDictionary

from instantlink_bridge.ble.client import (
    DiscoveredPrinter,
    connect_instax_printer,
    default_ble_session_manager,
    scan_instax_printers,
)
from instantlink_bridge.ble.instantlink import (
    CONNECT_STAGE_STALE_BOND_MIN,
    InstantLinkBackend,
    InstantLinkBleError,
    InstantLinkLibraryUnavailableError,
    InstantLinkMultiplePrintersError,
    InstantLinkPrinterNotFoundError,
    default_instantlink_backend,
)
from instantlink_bridge.ble.instax import PrinterStatus
from instantlink_bridge.ble.models import PrinterModel, spec_for
from instantlink_bridge.ble.session import (
    InstaxBleSessionLease,
    InstaxBleSessionManager,
    PrinterEndpoint,
)
from instantlink_bridge.ui.models import PairedPrinter
from instantlink_bridge.ui.pairing import normalize_instax_name, parse_instax_devices

LOGGER = logging.getLogger(__name__)
DEFAULT_UNAVAILABLE_SCAN_INTERVAL_S = 2.0
DEFAULT_STALE_SELECTED_SCAN_INTERVAL_S = 5.0
DEFAULT_BLUEZ_FALLBACK_INTERVAL_S = 10.0
DEFAULT_STALE_SELECTED_AFTER_MISSES = 3
_BLUETOOTHCTL_SCAN_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    WeakKeyDictionary()
)


class InstaxStatusProtocol(Protocol):
    """Protocol operations needed for UI status polling."""

    async def status(self) -> PrinterStatus:
        """Fetch current printer status."""


class ConnectedInstaxPrinter(Protocol):
    """Connected printer session needed by status polling."""

    @property
    def protocol(self) -> InstaxStatusProtocol:
        """Return the protocol client."""

    async def disconnect(self) -> None:
        """Disconnect from the printer."""


Scanner = Callable[[float], Awaitable[Sequence[DiscoveredPrinter]]]
BluezScanner = Callable[[float], Awaitable[Sequence[PairedPrinter]]]
Connector = Callable[[str, str | None], Awaitable[ConnectedInstaxPrinter]]
PrinterCandidate = DiscoveredPrinter | PairedPrinter
StatusSleep = Callable[[float], Awaitable[None]]


class PrinterStatusUnavailableReason(StrEnum):
    """Classified reason that status lookup could not reach the selected printer."""

    NOT_ADVERTISING = "not_advertising"
    STALE_SELECTED = "stale_selected"


@dataclass(frozen=True, slots=True)
class PrinterStatusSnapshot:
    """Compact printer status for LCD rendering."""

    film_remaining: int | None
    battery: int | None
    is_charging: bool | None
    model: PrinterModel | None = None
    name: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ScannerCandidateDiagnostics:
    """Visible Instax candidate captured during a scanner pass."""

    address: str
    name: str


@dataclass(frozen=True, slots=True)
class ScannerDiagnostics:
    """Scanner diagnostics for stale-printer recovery and support logs."""

    selected_address: str
    selected_name: str
    selected_visible: bool
    candidates: tuple[ScannerCandidateDiagnostics, ...]

    @property
    def candidate_count(self) -> int:
        """Return the number of visible Instax candidates."""

        return len(self.candidates)

    @property
    def candidate_names(self) -> tuple[str, ...]:
        """Return visible Instax candidate names."""

        return tuple(candidate.name for candidate in self.candidates)

    @property
    def candidate_addresses(self) -> tuple[str, ...]:
        """Return visible Instax candidate addresses."""

        return tuple(candidate.address for candidate in self.candidates)


class PrinterStatusProvider(Protocol):
    """Fetch current status from the selected printer."""

    async def fetch(self, printer: PairedPrinter) -> PrinterStatusSnapshot:
        """Return the current printer status."""

    async def close(self) -> None:
        """Release provider-owned status resources."""


class PrinterStatusUnavailableError(RuntimeError):
    """Raised when the selected printer is not currently advertising."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: ScannerDiagnostics | None = None,
        reason: PrinterStatusUnavailableReason = PrinterStatusUnavailableReason.NOT_ADVERTISING,
        consecutive_misses: int = 1,
        retry_after_s: float | None = None,
        status_message: str | None = None,
        stale_bond_suspected: bool = False,
        printer_not_found: bool = False,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics
        self.reason = reason
        self.consecutive_misses = consecutive_misses
        self.retry_after_s = retry_after_s
        self.status_message = status_message
        # True when the failure matches the stale-bond signature: the connect reached a late
        # GATT stage but the first encrypted write failed. The UI controller uses this to drive
        # the auto-rebond recovery (remove BlueZ bond, keep selection, reconnect).
        self.stale_bond_suspected = stale_bond_suspected
        # True when the FFI advertisement scan could not find the printer at all
        # (InstantLinkPrinterNotFoundError). The controller uses this to recover the "BlueZ is
        # holding a silent auto-reconnected link, so the printer is not advertising and the scan
        # can't see it" deadlock by dropping that BlueZ link so the printer re-advertises.
        self.printer_not_found = printer_not_found

    @property
    def stale_selected(self) -> bool:
        """Return true when repeated scans have not seen the selected printer."""

        return self.reason is PrinterStatusUnavailableReason.STALE_SELECTED


class BlePrinterStatusProvider:
    """Fetch status through a shared BLE session manager."""

    def __init__(
        self,
        *,
        scan_timeout_s: float = 0.5,
        connect_timeout_s: float = 8.0,
        keep_connection_open: bool = True,
        unavailable_scan_interval_s: float = DEFAULT_UNAVAILABLE_SCAN_INTERVAL_S,
        stale_selected_scan_interval_s: float = DEFAULT_STALE_SELECTED_SCAN_INTERVAL_S,
        bluez_fallback_interval_s: float = DEFAULT_BLUEZ_FALLBACK_INTERVAL_S,
        stale_selected_after_misses: int = DEFAULT_STALE_SELECTED_AFTER_MISSES,
        scanner: Scanner | None = None,
        connector: Connector | None = None,
        bluez_scanner: BluezScanner | None = None,
        session_manager: InstaxBleSessionManager[ConnectedInstaxPrinter] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: StatusSleep = asyncio.sleep,
    ) -> None:
        self._scan_timeout_s = scan_timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._keep_connection_open = keep_connection_open
        self._unavailable_scan_interval_s = _positive_interval(
            unavailable_scan_interval_s,
            name="unavailable_scan_interval_s",
        )
        self._stale_selected_scan_interval_s = _positive_interval(
            stale_selected_scan_interval_s,
            name="stale_selected_scan_interval_s",
        )
        self._bluez_fallback_interval_s = _positive_interval(
            bluez_fallback_interval_s,
            name="bluez_fallback_interval_s",
        )
        self._stale_selected_after_misses = max(1, stale_selected_after_misses)
        self._clock = clock
        self._sleep = sleep
        self._scanner: Scanner = (
            scanner if scanner is not None else cast(Scanner, scan_instax_printers)
        )
        self._connector: Connector = (
            connector if connector is not None else cast(Connector, connect_instax_printer)
        )
        self._bluez_scanner: BluezScanner = (
            bluez_scanner
            if bluez_scanner is not None
            else cast(BluezScanner, scan_bluez_instax_printers)
        )
        self._session_manager: InstaxBleSessionManager[ConnectedInstaxPrinter]
        if session_manager is not None:
            self._session_manager = session_manager
        elif connector is None:
            self._session_manager = cast(
                InstaxBleSessionManager[ConnectedInstaxPrinter],
                default_ble_session_manager(),
            )
        else:
            self._session_manager = InstaxBleSessionManager[ConnectedInstaxPrinter](
                self._connect_endpoint
            )
        self._last_scan_diagnostics: ScannerDiagnostics | None = None
        self._last_bluez_scan_at: float | None = None
        self._scan_lock = asyncio.Lock()
        self._last_unavailable_scan_at: float | None = None
        self._last_unavailable_endpoint: PrinterEndpoint | None = None
        self._not_advertising_misses = 0

    @property
    def last_scan_diagnostics(self) -> ScannerDiagnostics | None:
        """Return diagnostics from the latest scanner pass."""

        return self._last_scan_diagnostics

    async def fetch(self, printer: PairedPrinter) -> PrinterStatusSnapshot:
        """Fetch film and battery status from the printer."""

        cached = await self._fetch_cached(printer)
        if cached is not None:
            return cached

        target = await self._resolve_target(printer)
        LOGGER.info(
            "ui.printer_status_connect address=%s name=%s",
            target.address,
            target.name,
        )
        snapshot = await self._fetch_endpoint(target)
        self._clear_unavailable_state()
        return snapshot

    async def close(self) -> None:
        """Release provider ownership while leaving the shared session available for handoff."""

        return None

    async def close_cached_session(self) -> None:
        """Close the shared cached BLE session."""

        await self._session_manager.close()

    async def _fetch_cached(self, printer: PairedPrinter) -> PrinterStatusSnapshot | None:
        endpoint = self._session_manager.cached_endpoint_for(_endpoint_from_paired(printer))
        if endpoint is None:
            return None
        LOGGER.info(
            "ui.printer_status_keepalive address=%s name=%s",
            endpoint.address,
            endpoint.name,
        )
        try:
            snapshot = await self._fetch_endpoint(endpoint)
            self._clear_unavailable_state()
            return snapshot
        except Exception as exc:
            LOGGER.warning(
                "ui.printer_status_keepalive_failed address=%s name=%s error_type=%s error=%s",
                endpoint.address,
                endpoint.name,
                type(exc).__name__,
                exc,
            )
            return None

    async def _fetch_endpoint(
        self,
        target: PairedPrinter | PrinterEndpoint,
    ) -> PrinterStatusSnapshot:
        endpoint = _endpoint_from_target(target)
        lease = await self._session_manager.acquire_status(
            endpoint,
            connect_timeout_s=self._connect_timeout_s,
            model_override=endpoint.model,
        )
        failed = True
        try:
            status = await lease.connected.protocol.status()
            failed = False
            return PrinterStatusSnapshot(
                film_remaining=status.film_remaining,
                battery=status.battery,
                is_charging=status.is_charging,
                model=_snapshot_model(status.model, endpoint.model),
                name=status.name,
            )
        finally:
            await _release_status_lease(
                lease,
                failed=failed,
                keep_connected=self._keep_connection_open if not failed else None,
            )

    async def _connect_endpoint(
        self,
        endpoint: PrinterEndpoint,
        model_override: PrinterModel | None,
    ) -> ConnectedInstaxPrinter:
        _ = model_override
        return await self._connector(endpoint.address, endpoint.name)

    async def _resolve_target(self, printer: PairedPrinter) -> PairedPrinter:
        async with self._scan_lock:
            await self._pace_unavailable_scan(printer)
            self._last_scan_diagnostics = None
            try:
                candidates = await self._scanner(self._scan_timeout_s)
            except Exception:
                LOGGER.exception("ui.printer_status_scan_failed")
                self._record_scan_diagnostics(printer, ())
                return printer
            diagnostics = self._record_scan_diagnostics(printer, candidates)
            target = status_target_for_visible_match(printer, candidates)
            display_target = (
                target if target is not None else select_status_target(printer, candidates)
            )
            LOGGER.info(
                "ui.printer_status_scan count=%s candidates=%s selected_visible=%s "
                "target=%s target_name=%s",
                diagnostics.candidate_count,
                _format_diagnostics_candidates(diagnostics),
                diagnostics.selected_visible,
                display_target.address,
                display_target.name,
            )
            if target is not None:
                self._clear_unavailable_state()
                return target
            if self._bluez_fallback_due():
                target = await self._resolve_bluez_target(printer)
                if target is not None:
                    self._clear_unavailable_state()
                    return target
            else:
                LOGGER.debug(
                    "ui.printer_status_bluez_scan_skipped interval_s=%s",
                    self._bluez_fallback_interval_s,
                )
            raise self._unavailable_error(printer)

    async def _resolve_bluez_target(self, printer: PairedPrinter) -> PairedPrinter | None:
        try:
            candidates = await self._bluez_scanner(max(1.0, self._scan_timeout_s))
        except Exception:
            LOGGER.exception("ui.printer_status_bluez_scan_failed")
            self._record_scan_diagnostics(printer, ())
            return None
        diagnostics = self._record_scan_diagnostics(printer, candidates)
        target = status_target_for_visible_match(printer, candidates)
        display_target = target if target is not None else select_status_target(printer, candidates)
        LOGGER.info(
            "ui.printer_status_bluez_scan count=%s candidates=%s selected_visible=%s "
            "target=%s target_name=%s",
            diagnostics.candidate_count,
            _format_diagnostics_candidates(diagnostics),
            diagnostics.selected_visible,
            display_target.address,
            display_target.name,
        )
        return target

    def _bluez_fallback_due(self) -> bool:
        now = self._clock()
        last_scan_at = self._last_bluez_scan_at
        if last_scan_at is not None and now - last_scan_at < self._bluez_fallback_interval_s:
            return False
        self._last_bluez_scan_at = now
        return True

    def _record_scan_diagnostics(
        self,
        printer: PairedPrinter,
        candidates: Sequence[PrinterCandidate],
    ) -> ScannerDiagnostics:
        diagnostics = scanner_diagnostics(printer, candidates)
        self._last_scan_diagnostics = _merge_scanner_diagnostics(
            self._last_scan_diagnostics,
            diagnostics,
        )
        return diagnostics

    async def _pace_unavailable_scan(self, printer: PairedPrinter) -> None:
        last_scan_at = self._last_unavailable_scan_at
        if last_scan_at is None:
            return
        endpoint = _endpoint_from_paired(printer).normalized()
        last_endpoint = self._last_unavailable_endpoint
        if last_endpoint is None or not last_endpoint.matches(endpoint):
            return
        elapsed_s = self._clock() - last_scan_at
        interval_s = self._unavailable_scan_interval()
        wait_s = interval_s - elapsed_s
        if wait_s <= 0:
            return
        LOGGER.debug(
            "ui.printer_status_scan_paced address=%s name=%s wait_s=%.3f",
            printer.address,
            printer.name,
            wait_s,
        )
        await self._sleep(wait_s)

    def _unavailable_error(self, printer: PairedPrinter) -> PrinterStatusUnavailableError:
        endpoint = _endpoint_from_paired(printer).normalized()
        if self._last_unavailable_endpoint is None or not self._last_unavailable_endpoint.matches(
            endpoint
        ):
            self._not_advertising_misses = 0
        self._last_unavailable_endpoint = endpoint
        self._not_advertising_misses += 1
        self._last_unavailable_scan_at = self._clock()

        stale = self._not_advertising_misses >= self._stale_selected_after_misses
        reason = (
            PrinterStatusUnavailableReason.STALE_SELECTED
            if stale
            else PrinterStatusUnavailableReason.NOT_ADVERTISING
        )
        message = (
            "selected printer is stale or powered off"
            if stale
            else "printer status endpoint is not advertising"
        )
        status_message = "Hold K3 to re-pair" if stale else None
        retry_after_s = self._unavailable_scan_interval_for_misses(self._not_advertising_misses)
        return PrinterStatusUnavailableError(
            message,
            diagnostics=self._last_scan_diagnostics,
            reason=reason,
            consecutive_misses=self._not_advertising_misses,
            retry_after_s=retry_after_s,
            status_message=status_message,
        )

    def _clear_unavailable_state(self) -> None:
        self._last_unavailable_scan_at = None
        self._last_unavailable_endpoint = None
        self._not_advertising_misses = 0

    def _unavailable_scan_interval(self) -> float:
        return self._unavailable_scan_interval_for_misses(self._not_advertising_misses)

    def _unavailable_scan_interval_for_misses(self, misses: int) -> float:
        if misses >= self._stale_selected_after_misses:
            return self._stale_selected_scan_interval_s
        return self._unavailable_scan_interval_s


class InstantLinkPrinterStatusProvider:
    """Fetch printer status through InstantLink's Rust backend."""

    def __init__(
        self,
        *,
        backend: InstantLinkBackend | None = None,
        scan_duration_s: int = 5,
    ) -> None:
        self._backend = backend if backend is not None else default_instantlink_backend()
        self._scan_duration_s = scan_duration_s

    async def fetch(self, printer: PairedPrinter) -> PrinterStatusSnapshot:
        """Return the current printer status."""

        try:
            status = await self._backend.status(
                printer.name,
                scan_duration_s=self._scan_duration_s,
            )
        except InstantLinkPrinterNotFoundError as exc:
            raise PrinterStatusUnavailableError(
                "printer is not advertising",
                diagnostics=scanner_diagnostics(printer, ()),
                reason=PrinterStatusUnavailableReason.NOT_ADVERTISING,
                status_message="Turn printer on",
                printer_not_found=True,
            ) from exc
        except InstantLinkMultiplePrintersError as exc:
            raise PrinterStatusUnavailableError(
                "multiple matching printers found",
                diagnostics=scanner_diagnostics(printer, ()),
                reason=PrinterStatusUnavailableReason.STALE_SELECTED,
                status_message="Select printer again",
            ) from exc
        except InstantLinkLibraryUnavailableError:
            LOGGER.exception("instantlink.status_library_unavailable")
            raise
        except (InstantLinkBleError, TimeoutError) as exc:
            raise PrinterStatusUnavailableError(
                "printer status unavailable",
                diagnostics=scanner_diagnostics(printer, ()),
                reason=PrinterStatusUnavailableReason.NOT_ADVERTISING,
                status_message="Retrying printer",
                stale_bond_suspected=_is_stale_bond_signature(exc),
            ) from exc

        return PrinterStatusSnapshot(
            film_remaining=status.film_remaining,
            battery=status.battery,
            is_charging=status.is_charging,
            model=status.model,
            name=status.name,
        )

    async def close(self) -> None:
        """Leave the InstantLink backend available for print handoff."""

        return None

    async def configure_keepalive(self, interval_s: float | None) -> None:
        """Configure InstantLink core's background keepalive loop."""

        await self._backend.configure_keepalive(interval_s)

    async def close_cached_session(self) -> None:
        """Close the cached InstantLink session."""

        await self._backend.disconnect()


async def _release_status_lease(
    lease: InstaxBleSessionLease[ConnectedInstaxPrinter],
    *,
    failed: bool,
    keep_connected: bool | None,
) -> None:
    release_task = asyncio.create_task(lease.release(failed=failed, keep_connected=keep_connected))
    try:
        await asyncio.shield(release_task)
    except asyncio.CancelledError:
        await asyncio.shield(release_task)
        raise


async def scan_bluez_instax_printers(timeout_s: float = 8.0) -> list[PairedPrinter]:
    """Scan with bluetoothctl and return visible or cached Instax devices."""

    timeout_seconds = max(1, round(timeout_s))
    async with _bluetoothctl_scan_lock():
        try:
            scan_output = await _run_bluetoothctl(
                "--timeout",
                str(timeout_seconds),
                "scan",
                "on",
                timeout_seconds=timeout_seconds + 3,
            )
        finally:
            await _run_bluetoothctl("scan", "off", timeout_seconds=5)
        return _dedupe_candidates(parse_instax_devices(scan_output))


def _is_stale_bond_signature(exc: InstantLinkBleError | TimeoutError) -> bool:
    """Return true when a status BLE error matches the stale-bond write-failure signature.

    The signature is: a connect attempt that advanced to at least the characteristic-lookup
    stage (service discovery succeeded, so GATT was up) but then failed with a BLE error — either
    the encrypted characteristics never resolved ("write characteristic not found") or the first
    encrypted write/subscribe failed. A timeout, or a BLE error from an earlier connect stage
    (scan/connect/service-discovery), is treated as an ordinary transient miss.
    """

    if not isinstance(exc, InstantLinkBleError):
        return False
    stage = exc.connect_failure_stage
    return stage is not None and stage >= CONNECT_STAGE_STALE_BOND_MIN


def _endpoint_from_paired(printer: PairedPrinter) -> PrinterEndpoint:
    return PrinterEndpoint(address=printer.address, name=printer.name, model=printer.model)


def _endpoint_from_target(target: PairedPrinter | PrinterEndpoint) -> PrinterEndpoint:
    if isinstance(target, PrinterEndpoint):
        return target
    return _endpoint_from_paired(target)


def _positive_interval(value: float, *, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than 0")
    return value


def _snapshot_model(
    detected_model: PrinterModel,
    known_model: PrinterModel | None,
) -> PrinterModel | None:
    if detected_model is not PrinterModel.MINI:
        return detected_model
    if known_model is None:
        return detected_model
    detected_spec = spec_for(detected_model)
    known_spec = spec_for(known_model)
    if (known_spec.width, known_spec.height) == (detected_spec.width, detected_spec.height):
        return known_model
    return detected_model


def select_status_target(
    selected: PairedPrinter,
    candidates: Sequence[PrinterCandidate],
) -> PairedPrinter:
    """Choose the best BLE advertisement to use for a selected printer."""

    matches = [
        PairedPrinter(
            address=candidate.address.upper(),
            name=candidate.name,
            model=selected.model,
        )
        for candidate in candidates
        if _matches_selected_printer(selected, candidate)
    ]
    if not matches:
        return selected
    best = min(matches, key=_candidate_priority)
    return PairedPrinter(
        address=best.address,
        name=normalize_instax_name(best.name),
        model=selected.model,
    )


def status_target_for_visible_match(
    selected: PairedPrinter,
    candidates: Sequence[PrinterCandidate],
) -> PairedPrinter | None:
    """Return the connectable status endpoint for a visible selected printer."""

    matches = [
        PairedPrinter(
            address=candidate.address.upper(),
            name=candidate.name,
            model=selected.model,
        )
        for candidate in candidates
        if _matches_selected_printer(selected, candidate)
    ]
    if not matches:
        return None
    best = min(matches, key=_candidate_priority)
    if _is_status_connectable(best):
        return PairedPrinter(
            address=best.address,
            name=normalize_instax_name(best.name),
            model=selected.model,
        )
    return _derive_ios_status_endpoint(best)


def has_matching_status_target(
    selected: PairedPrinter,
    candidates: Sequence[PrinterCandidate],
) -> bool:
    """Return true if scan results include the selected printer."""

    return any(_matches_selected_printer(selected, candidate) for candidate in candidates)


def scanner_diagnostics(
    selected: PairedPrinter,
    candidates: Sequence[PrinterCandidate],
) -> ScannerDiagnostics:
    """Return structured scanner diagnostics for visible Instax candidates."""

    return ScannerDiagnostics(
        selected_address=selected.address.upper(),
        selected_name=selected.name,
        selected_visible=has_matching_status_target(selected, candidates),
        candidates=tuple(
            ScannerCandidateDiagnostics(
                address=candidate.address.upper(),
                name=candidate.name,
            )
            for candidate in candidates
        ),
    )


def _matches_selected_printer(selected: PairedPrinter, candidate: PrinterCandidate) -> bool:
    if candidate.address.upper() == selected.address.upper():
        return True
    selected_name = normalize_instax_name(selected.name).casefold()
    candidate_name = normalize_instax_name(candidate.name).casefold()
    return bool(selected_name and candidate_name and selected_name == candidate_name)


async def _run_bluetoothctl(
    *args: str,
    timeout_seconds: int,
) -> str:
    process = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        await _kill_process(process)
        raise
    except BaseException:
        await _kill_process(process)
        raise
    return stdout.decode(errors="replace")


def _bluetoothctl_scan_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _BLUETOOTHCTL_SCAN_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _BLUETOOTHCTL_SCAN_LOCKS[loop] = lock
    return lock


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        process.kill()
    with suppress(Exception):
        await process.wait()


def _dedupe_candidates(candidates: Sequence[PairedPrinter]) -> list[PairedPrinter]:
    deduped: list[PairedPrinter] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.address in seen:
            continue
        seen.add(candidate.address)
        deduped.append(candidate)
    return deduped


def _candidate_priority(printer: PairedPrinter) -> tuple[int, str]:
    address = printer.address.upper()
    name = printer.name.upper()
    if name.endswith("(IOS)") or address.startswith("FA:AB:BC"):
        return (0, name)
    if name.endswith("(ANDROID)"):
        return (1, name)
    return (2, name)


def _is_status_connectable(printer: PairedPrinter) -> bool:
    address = printer.address.upper()
    name = printer.name.upper()
    return name.endswith("(IOS)") or address.startswith("FA:AB:BC")


def _derive_ios_status_endpoint(printer: PairedPrinter) -> PairedPrinter | None:
    octets = printer.address.upper().split(":")
    if len(octets) != 6:
        return None
    return PairedPrinter(
        address=f"FA:AB:BC:{octets[3]}:{octets[4]}:{octets[5]}",
        name=normalize_instax_name(printer.name),
        model=printer.model,
    )


def _format_diagnostics_candidates(diagnostics: ScannerDiagnostics) -> str:
    if not diagnostics.candidates:
        return "none"
    return ",".join(f"{candidate.name}@{candidate.address}" for candidate in diagnostics.candidates)


def _merge_scanner_diagnostics(
    existing: ScannerDiagnostics | None,
    current: ScannerDiagnostics,
) -> ScannerDiagnostics:
    if existing is None:
        return current

    candidates = list(existing.candidates)
    seen = {candidate.address for candidate in candidates}
    for candidate in current.candidates:
        if candidate.address in seen:
            continue
        seen.add(candidate.address)
        candidates.append(candidate)

    return ScannerDiagnostics(
        selected_address=current.selected_address,
        selected_name=current.selected_name,
        selected_visible=existing.selected_visible or current.selected_visible,
        candidates=tuple(candidates),
    )
