"""InstantLink-backed printer transport.

The Python Bleak transport remains in the tree as a fallback, but hardware testing showed that
BlueZ/Bleak can see some Instax Link printers and still disconnect during service discovery.
InstantLink's Rust backend uses btleplug and is the authoritative implementation for printer
connection, model detection, status, and print transfer.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import platform
import re
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TypeVar

from instantlink_bridge.ble.instax import (
    CoverOpenError,
    LowPrinterBatteryError,
    NoFilmError,
    PrinterBusyError,
    PrintRejectedError,
)
from instantlink_bridge.ble.models import PrinterModel, spec_for
from instantlink_bridge.imaging.pipeline import (
    FitMode,
    ImagePipelineError,
    PrintEdit,
    prepare_for_instantlink_backend,
)
from instantlink_bridge.printing import PrintProgress, PrintProgressCallback, PrintStage

LOGGER = logging.getLogger(__name__)

ERROR_PRINTER_NOT_FOUND = -1
ERROR_MULTIPLE_PRINTERS = -2
ERROR_BLE = -3
ERROR_TIMEOUT = -4
ERROR_INVALID_ARGUMENT = -5
ERROR_IMAGE = -6
ERROR_PRINT_REJECTED = -7
ERROR_NO_FILM = -8
ERROR_LOW_BATTERY = -9
ERROR_COVER_OPEN = -10
ERROR_PRINTER_BUSY = -11

INSTANTLINK_FIT_CROP = 0
INSTANTLINK_FIT_CONTAIN = 1
INSTANTLINK_FIT_STRETCH = 2
DEFAULT_SCAN_DURATION_S = 5
STRING_BUFFER_SIZE = 4096
FFI_CANCEL_GRACE_S = 5.0
STATUS_FAILURE_LOG_INTERVAL_S = 30.0

_PRINT_PROGRESS_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_uint32)
_CONNECT_PROGRESS_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_int32, ctypes.c_char_p)
CONNECT_STAGE_NAMES = {
    0: "scan_started",
    1: "scan_finished",
    2: "device_matched",
    3: "ble_connecting",
    4: "service_discovery",
    5: "characteristic_lookup",
    6: "notification_subscribe",
    7: "model_detecting",
    8: "status_fetching",
    9: "connected",
    10: "failed",
}
# Lowest connect stage that proves GATT came up far enough that an encrypted write should
# succeed. A connect that reaches this stage (or later) and then fails with a BLE/write error is
# the stale-bond signature: the link is up but the printer cleared its pairing after a power cycle.
CONNECT_STAGE_NOTIFICATION_SUBSCRIBE = 6
# Consecutive status failures that mean the cached BLE link is dead (not a one-off hiccup),
# after which the connection is torn down so the next poll does a fresh connect (which can
# re-establish the link and trigger auto-rebond). Tolerates a single transient failure to
# avoid needless reconnect churn; the persistent btleplug Manager means reconnects don't leak.
STALE_LINK_STATUS_FAILURE_THRESHOLD = 2
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class InstantLinkStatus:
    """Status returned by the InstantLink backend."""

    name: str
    model: PrinterModel
    battery: int
    film_remaining: int
    is_charging: bool
    print_count: int | None


class InstantLinkError(RuntimeError):
    """Base error for the InstantLink backend."""

    def __init__(self, message: str, *, code: int) -> None:
        super().__init__(message)
        self.code = code
        # Highest connect-progress stage observed during the in-flight connect attempt that
        # produced this error, when known. ``None`` means the error did not originate from a
        # connect attempt (e.g. a status read on an already-established link).
        self.connect_failure_stage: int | None = None


class InstantLinkLibraryUnavailableError(InstantLinkError):
    """Raised when the Rust FFI library is not installed."""


class InstantLinkPrinterNotFoundError(InstantLinkError):
    """Raised when InstantLink cannot find the selected printer."""


class InstantLinkMultiplePrintersError(InstantLinkError):
    """Raised when InstantLink finds multiple possible printers."""


class InstantLinkBleError(InstantLinkError):
    """Raised for BLE transport errors reported by InstantLink."""


class InstantLinkInvalidArgumentError(InstantLinkError):
    """Raised when InstantLink Bridge passes invalid data to the FFI layer."""


_DEFAULT_BACKEND: InstantLinkBackend | None = None


def instantlink_backend_enabled() -> bool:
    """Return whether the InstantLink backend should be used by default."""

    backend = os.environ.get("INSTANTLINK_BRIDGE_PRINTER_BACKEND", "instantlink")
    return backend.strip().casefold() in {
        "",
        "instantlink",
        "rust",
    }


def default_instantlink_backend() -> InstantLinkBackend:
    """Return the process-wide InstantLink backend wrapper."""

    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = InstantLinkBackend()
    return _DEFAULT_BACKEND


async def close_default_instantlink_backend() -> None:
    """Disconnect the process-wide InstantLink backend, if loaded."""

    if _DEFAULT_BACKEND is not None:
        await _DEFAULT_BACKEND.disconnect()


def stable_instantlink_address(name: str) -> str:
    """Return a stable pseudo-address for a printer selected by InstantLink."""

    normalized = normalize_printer_name(name)
    serial = _extract_serial(normalized)
    return f"instantlink:{serial or normalized}".upper()


def normalize_printer_name(name: str) -> str:
    """Strip platform suffixes from an Instax advertising name."""

    return re.sub(r"\s*\((IOS|ANDROID)\)$", "", name.strip(), flags=re.IGNORECASE).strip()


async def scan_instax_printers(timeout_s: float = DEFAULT_SCAN_DURATION_S) -> list[str]:
    """Scan for nearby Instax printer names through InstantLink."""

    return await default_instantlink_backend().scan(timeout_s)


async def print_file_to_printer(
    address: str,
    image_path: Path,
    *,
    name: str | None = None,
    fit: FitMode = FitMode.AUTO,
    quality: int = 100,
    edit: PrintEdit | None = None,
    print_option: int = 0,
    model: PrinterModel | None = None,
    progress: PrintProgressCallback | None = None,
) -> None:
    """Print a file through InstantLink's Rust transport."""

    _ = address
    await default_instantlink_backend().print_file(
        name or _name_from_address(address),
        image_path,
        fit=fit,
        quality=quality,
        edit=edit,
        print_option=print_option,
        model_override=model,
        progress=progress,
    )


class InstantLinkBackend:
    """Async wrapper around InstantLink's synchronous C FFI."""

    def __init__(self, library_path: Path | None = None) -> None:
        self._library_path = library_path
        self._lib: ctypes.CDLL | None = None
        self._lock = asyncio.Lock()
        self._last_status_failure_log_at = -float("inf")
        self._last_status_failure_signature: tuple[str, str, str] | None = None
        self._consecutive_status_failures = 0

    async def scan(self, timeout_s: float = DEFAULT_SCAN_DURATION_S) -> list[str]:
        """Return normalized visible Instax printer names."""

        return await self._run_blocking_serialized(
            "scan",
            partial(self._scan_blocking, timeout_s),
        )

    async def status(
        self,
        name: str,
        *,
        scan_duration_s: int = DEFAULT_SCAN_DURATION_S,
    ) -> InstantLinkStatus:
        """Connect if needed and return current printer status."""

        return await self._run_blocking_serialized(
            "status",
            partial(self._status_blocking, name, scan_duration_s),
        )

    async def print_file(
        self,
        name: str,
        image_path: Path,
        *,
        fit: FitMode = FitMode.AUTO,
        quality: int = 100,
        edit: PrintEdit | None = None,
        print_option: int = 0,
        model_override: PrinterModel | None = None,
        progress: PrintProgressCallback | None = None,
    ) -> None:
        """Prepare an edited image and send it through InstantLink."""

        await self._run_blocking_serialized(
            "print",
            partial(
                self._print_file_blocking,
                name,
                image_path,
                fit,
                quality,
                edit,
                print_option,
                model_override,
                progress,
            ),
        )

    async def disconnect(self) -> None:
        """Disconnect the current InstantLink device."""

        await self._run_blocking_serialized("disconnect", self._disconnect_blocking)

    async def configure_keepalive(self, interval_s: float | None) -> None:
        """Configure InstantLink core's background keepalive loop."""

        await self._run_blocking_serialized(
            "configure_keepalive",
            partial(self._configure_keepalive_blocking, interval_s),
        )

    async def keepalive_once(self) -> None:
        """Send one explicit InstantLink keepalive/status request."""

        await self._run_blocking_serialized("keepalive", self._keepalive_blocking)

    async def _run_blocking_serialized(self, operation: str, worker: Callable[[], _T]) -> _T:
        """Run one synchronous FFI call while preserving serialization through cancellation."""

        async with self._lock:
            task = asyncio.ensure_future(asyncio.to_thread(worker))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                LOGGER.warning("instantlink.%s_cancel_waiting_for_worker", operation)
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=FFI_CANCEL_GRACE_S)
                except TimeoutError:
                    LOGGER.critical(
                        "instantlink.%s_cancel_worker_stuck restarting_process grace_s=%.1f",
                        operation,
                        FFI_CANCEL_GRACE_S,
                    )
                    os._exit(1)
                raise

    def _scan_blocking(self, timeout_s: float) -> list[str]:
        duration = max(1, round(timeout_s))
        buf = ctypes.create_string_buffer(STRING_BUFFER_SIZE)
        rc = int(self._library().instantlink_scan(duration, buf, len(buf)))
        if rc < 0:
            _raise_for_code(rc, "scan failed")
        raw_json = bytes(buf.value).decode("utf-8", errors="replace")
        try:
            values = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise InstantLinkBleError("scan returned invalid JSON", code=ERROR_BLE) from exc
        if not isinstance(values, list):
            raise InstantLinkBleError("scan returned non-list JSON", code=ERROR_BLE)
        names = [
            normalize_printer_name(value)
            for value in values
            if isinstance(value, str) and value.strip() and not _is_android_advertisement(value)
        ]
        return _dedupe_names(names)

    def _status_blocking(self, name: str, scan_duration_s: int) -> InstantLinkStatus:
        try:
            status = self._status_blocking_connected(name, scan_duration_s)
            self._clear_status_failure_log_state()
            self._consecutive_status_failures = 0
            return status
        except (InstantLinkBleError, InstantLinkPrinterNotFoundError, TimeoutError) as exc:
            # Persistent-connection policy: keep the cached link across a single transient
            # read/timeout so we don't pay a full scan + reconnect every tick. But a link
            # that keeps failing is dead (e.g. the printer slept and dropped BLE), so after
            # a few consecutive failures we MUST tear it down — otherwise we'd retry status
            # on a dead handle forever and never reconnect (or trigger auto-rebond).
            self._consecutive_status_failures += 1
            disconnect = self._should_disconnect_on_status_error(
                exc, self._consecutive_status_failures
            )
            self._log_status_failure(name, exc, disconnected=disconnect)
            if disconnect:
                self._disconnect_blocking()
                self._consecutive_status_failures = 0
            raise

    def _should_disconnect_on_status_error(
        self, exc: BaseException, consecutive_failures: int
    ) -> bool:
        """Decide whether to drop the cached connection after a status error.

        Tear down when the printer is genuinely gone (``InstantLinkPrinterNotFoundError``),
        OR when the link has failed on ``STALE_LINK_STATUS_FAILURE_THRESHOLD`` consecutive
        attempts (a dead link, not a one-off hiccup). A single transient
        ``InstantLinkBleError`` / ``TimeoutError`` keeps the connection so the next poll
        reuses the live link; persistent failures force a fresh reconnect.
        """

        if isinstance(exc, InstantLinkPrinterNotFoundError):
            return True
        return consecutive_failures >= STALE_LINK_STATUS_FAILURE_THRESHOLD

    def _log_status_failure(self, name: str, exc: BaseException, *, disconnected: bool) -> None:
        normalized_name = normalize_printer_name(name)
        event = "status_failed_disconnect" if disconnected else "status_failed_keep_connection"
        signature = (normalized_name, type(exc).__name__, str(exc))
        now = time.monotonic()
        if (
            signature != self._last_status_failure_signature
            or now - self._last_status_failure_log_at >= STATUS_FAILURE_LOG_INTERVAL_S
        ):
            LOGGER.warning(
                "instantlink.%s name=%s error_type=%s error=%s",
                event,
                normalized_name,
                type(exc).__name__,
                exc,
            )
            LOGGER.debug("instantlink.%s_trace", event, exc_info=True)
            self._last_status_failure_signature = signature
            self._last_status_failure_log_at = now
        else:
            LOGGER.debug(
                "instantlink.%s_suppressed name=%s error_type=%s error=%s",
                event,
                normalized_name,
                type(exc).__name__,
                exc,
            )

    def _clear_status_failure_log_state(self) -> None:
        self._last_status_failure_signature = None
        self._last_status_failure_log_at = -float("inf")

    def _status_blocking_connected(self, name: str, scan_duration_s: int) -> InstantLinkStatus:
        self._ensure_connected_blocking(name, scan_duration_s=scan_duration_s)

        battery = ctypes.c_int()
        film = ctypes.c_int()
        charging = ctypes.c_int()
        print_count = ctypes.c_int()
        if hasattr(self._library(), "instantlink_status"):
            rc = int(
                self._library().instantlink_status(
                    ctypes.byref(battery),
                    ctypes.byref(film),
                    ctypes.byref(charging),
                    ctypes.byref(print_count),
                )
            )
            if rc == ERROR_NO_FILM:
                battery.value = self._battery_value_for_no_film_status()
                film.value = 0
                charging.value = 0
                print_count.value = -1
            elif rc < 0:
                _raise_for_code(rc, "status failed")
        else:
            battery.value = self._battery_value_for_no_film_status()
            print_count.value = -1
            rc = int(
                self._library().instantlink_film_and_charging(
                    ctypes.byref(film),
                    ctypes.byref(charging),
                )
            )
            if rc == ERROR_NO_FILM:
                film.value = 0
                charging.value = 0
            elif rc < 0:
                _raise_for_code(rc, "status film failed")

        model = self._device_model_blocking()
        connected_name = self._device_name_blocking() or normalize_printer_name(name)
        return InstantLinkStatus(
            name=connected_name,
            model=model,
            battery=battery.value,
            film_remaining=film.value,
            is_charging=bool(charging.value),
            print_count=print_count.value if print_count.value >= 0 else None,
        )

    def _battery_value_for_no_film_status(self) -> int:
        battery_value = int(self._library().instantlink_battery())
        if battery_value < 0:
            _raise_for_code(battery_value, "status battery failed")
        return battery_value

    def _print_file_blocking(
        self,
        name: str,
        image_path: Path,
        fit: FitMode,
        quality: int,
        edit: PrintEdit | None,
        print_option: int,
        model_override: PrinterModel | None,
        progress: PrintProgressCallback | None,
    ) -> None:
        _emit_progress(
            progress,
            PrintProgress(PrintStage.CONNECTING, "Connecting", normalize_printer_name(name), None),
        )
        self._ensure_connected_blocking(name, scan_duration_s=DEFAULT_SCAN_DURATION_S)
        detected_model = self._device_model_blocking()
        model = _compatible_model_override(detected_model, model_override)

        spec = spec_for(model)
        _emit_progress(
            progress,
            PrintProgress(PrintStage.PREPARING, "Preparing image", spec.name, None),
        )
        try:
            prepared = prepare_for_instantlink_backend(
                image_path,
                model,
                fit=fit,
                quality=quality,
                edit=edit,
            )
        except ImagePipelineError:
            raise
        except Exception as exc:
            raise ImagePipelineError("Image unsupported") from exc

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="instantlink-bridge-instantlink-",
                suffix=".jpg",
                delete=False,
            ) as temp_file:
                temp_file.write(prepared.data)
                temp_path = Path(temp_file.name)

            _emit_progress(
                progress,
                PrintProgress(
                    PrintStage.SENDING,
                    "Sending to printer",
                    f"{len(prepared.data) // 1024} KB",
                    0,
                ),
            )
            callback = _print_progress_callback(progress, len(prepared.data))
            rc = int(
                self._library().instantlink_print_with_progress(
                    str(temp_path).encode("utf-8"),
                    ctypes.c_uint8(max(1, min(100, quality))),
                    ctypes.c_uint8(INSTANTLINK_FIT_STRETCH),
                    ctypes.c_uint8(max(0, min(255, print_option))),
                    callback,
                )
            )
            if rc < 0:
                _raise_for_code(rc, "print failed")
            _emit_progress(
                progress,
                PrintProgress(PrintStage.FINISHING, "Finishing", "Waiting for printer", 100),
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _ensure_connected_blocking(
        self,
        name: str,
        *,
        scan_duration_s: int,
    ) -> None:
        target_name = _connect_target_name(name)
        connected_name = self._device_name_blocking()
        if connected_name is not None and _printer_names_match(connected_name, target_name):
            return
        stage_tracker = _ConnectStageTracker()
        rc = self._connect_named_blocking(target_name, scan_duration_s, stage_tracker)
        if rc < 0:
            try:
                _raise_for_code(rc, "connect failed")
            except InstantLinkError as exc:
                # Carry the highest connect stage observed so the status/UI layer can classify a
                # late-stage write/comms failure as the stale-bond signature.
                exc.connect_failure_stage = stage_tracker.max_stage
                raise

    def _connect_named_blocking(
        self,
        target_name: str,
        scan_duration_s: int,
        stage_tracker: _ConnectStageTracker | None = None,
    ) -> int:
        library = self._library()
        duration = max(1, scan_duration_s)
        if hasattr(library, "instantlink_connect_named_with_progress"):
            callback = _connect_progress_logger(target_name, stage_tracker)
            return int(
                library.instantlink_connect_named_with_progress(
                    target_name.encode("utf-8"),
                    duration,
                    callback,
                )
            )
        return int(library.instantlink_connect_named(target_name.encode("utf-8"), duration))

    def _device_name_blocking(self) -> str | None:
        return self._string_result("instantlink_device_name", missing_ok=True)

    def _device_model_blocking(self) -> PrinterModel:
        model = self._string_result("instantlink_device_model", missing_ok=False)
        if model is None:
            raise InstantLinkPrinterNotFoundError("printer is not connected", code=-1)
        return _parse_instantlink_model(model)

    def _disconnect_blocking(self) -> None:
        if self._lib is None:
            return
        rc = int(self._library().instantlink_disconnect())
        if rc in {0, ERROR_PRINTER_NOT_FOUND}:
            return
        LOGGER.warning("instantlink.disconnect_failed code=%s", rc)

    def _configure_keepalive_blocking(self, interval_s: float | None) -> None:
        library = self._library()
        configure = getattr(library, "instantlink_set_keepalive_interval", None)
        if configure is None:
            LOGGER.debug("instantlink.keepalive_config_unsupported")
            return
        seconds = 0 if interval_s is None or interval_s <= 0 else max(1, round(interval_s))
        rc = int(configure(seconds))
        if rc < 0:
            _raise_for_code(rc, "set keepalive failed")
        LOGGER.info("instantlink.keepalive_configured interval_s=%s", seconds)

    def _keepalive_blocking(self) -> None:
        library = self._library()
        keepalive = getattr(library, "instantlink_keepalive", None)
        if keepalive is None:
            LOGGER.debug("instantlink.keepalive_unsupported")
            return
        rc = int(keepalive())
        if rc < 0:
            _raise_for_code(rc, "keepalive failed")

    def _string_result(self, function_name: str, *, missing_ok: bool) -> str | None:
        buf = ctypes.create_string_buffer(STRING_BUFFER_SIZE)
        fn = getattr(self._library(), function_name)
        rc = int(fn(buf, len(buf)))
        if rc == ERROR_PRINTER_NOT_FOUND and missing_ok:
            return None
        if rc < 0:
            _raise_for_code(rc, f"{function_name} failed")
        return bytes(buf.value).decode("utf-8", errors="replace").strip() or None

    def _library(self) -> ctypes.CDLL:
        if self._lib is None:
            self._lib = _load_library(self._library_path)
        return self._lib


def _print_progress_callback(
    progress: PrintProgressCallback | None,
    image_size: int,
) -> Callable[[int, int], None]:
    last_percent = -5

    def emit(done: int, total: int) -> None:
        nonlocal last_percent
        if total <= 0:
            return
        percent = min(100, max(0, int(done * 100 / total)))
        if percent < 100 and percent < last_percent + 5:
            return
        last_percent = percent
        _emit_progress(
            progress,
            PrintProgress(
                PrintStage.SENDING,
                f"Sending {percent}%",
                f"{done}/{total} chunks  {image_size // 1024} KB",
                percent,
            ),
        )

    return _PRINT_PROGRESS_CALLBACK(emit)


def _emit_progress(progress: PrintProgressCallback | None, update: PrintProgress) -> None:
    if progress is not None:
        progress(update)


def _load_library(library_path: Path | None) -> ctypes.CDLL:
    candidates = list(_candidate_library_paths(library_path))
    for candidate in candidates:
        if not candidate.exists():
            continue
        lib = ctypes.CDLL(str(candidate))
        _configure_library(lib)
        lib.instantlink_init()
        LOGGER.info("instantlink.library_loaded path=%s", candidate)
        return lib
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise InstantLinkLibraryUnavailableError(
        f"InstantLink backend library is not installed; searched {searched}",
        code=ERROR_BLE,
    )


def _candidate_library_paths(library_path: Path | None) -> Iterable[Path]:
    if library_path is not None:
        yield library_path
        return
    env_path = os.environ.get("INSTANTLINK_BRIDGE_INSTANTLINK_LIB")
    if env_path:
        yield Path(env_path)
    system = platform.system()
    file_name = "libinstantlink_ffi.dylib" if system == "Darwin" else "libinstantlink_ffi.so"
    bridge_root = Path(__file__).resolve().parents[3]
    workspace_root = bridge_root.parent
    yield Path("/opt/InstantLinkBridge/lib") / file_name
    yield bridge_root / "lib" / file_name
    yield workspace_root / "target" / "release" / file_name
    yield workspace_root / "target" / "aarch64-unknown-linux-gnu" / "release" / file_name


def _configure_library(lib: ctypes.CDLL) -> None:
    lib.instantlink_init.argtypes = []
    lib.instantlink_init.restype = None
    lib.instantlink_scan.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.instantlink_scan.restype = ctypes.c_int
    lib.instantlink_connect_named.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.instantlink_connect_named.restype = ctypes.c_int
    if hasattr(lib, "instantlink_connect_named_with_progress"):
        lib.instantlink_connect_named_with_progress.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
            _CONNECT_PROGRESS_CALLBACK,
        ]
        lib.instantlink_connect_named_with_progress.restype = ctypes.c_int
    lib.instantlink_disconnect.argtypes = []
    lib.instantlink_disconnect.restype = ctypes.c_int
    lib.instantlink_status.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.instantlink_status.restype = ctypes.c_int
    lib.instantlink_battery.argtypes = []
    lib.instantlink_battery.restype = ctypes.c_int
    lib.instantlink_film_and_charging.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.instantlink_film_and_charging.restype = ctypes.c_int
    if hasattr(lib, "instantlink_keepalive"):
        lib.instantlink_keepalive.argtypes = []
        lib.instantlink_keepalive.restype = ctypes.c_int
    if hasattr(lib, "instantlink_set_keepalive_interval"):
        lib.instantlink_set_keepalive_interval.argtypes = [ctypes.c_int]
        lib.instantlink_set_keepalive_interval.restype = ctypes.c_int
    lib.instantlink_device_name.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.instantlink_device_name.restype = ctypes.c_int
    lib.instantlink_device_model.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.instantlink_device_model.restype = ctypes.c_int
    lib.instantlink_print_with_progress.argtypes = [
        ctypes.c_char_p,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint8,
        _PRINT_PROGRESS_CALLBACK,
    ]
    lib.instantlink_print_with_progress.restype = ctypes.c_int


def _raise_for_code(code: int, context: str) -> None:
    if code == ERROR_PRINTER_NOT_FOUND:
        raise InstantLinkPrinterNotFoundError(f"{context}: printer not found", code=code)
    if code == ERROR_MULTIPLE_PRINTERS:
        raise InstantLinkMultiplePrintersError(f"{context}: multiple printers found", code=code)
    if code == ERROR_BLE:
        raise InstantLinkBleError(f"{context}: BLE communication failed", code=code)
    if code == ERROR_TIMEOUT:
        raise TimeoutError(context)
    if code == ERROR_INVALID_ARGUMENT:
        raise InstantLinkInvalidArgumentError(f"{context}: invalid argument", code=code)
    if code == ERROR_IMAGE:
        raise ImagePipelineError("Image unsupported")
    if code == ERROR_NO_FILM:
        raise NoFilmError("no film remaining")
    if code == ERROR_LOW_BATTERY:
        raise LowPrinterBatteryError("printer battery too low")
    if code == ERROR_COVER_OPEN:
        raise CoverOpenError("printer cover is open")
    if code == ERROR_PRINTER_BUSY:
        raise PrinterBusyError("printer is busy")
    if code == ERROR_PRINT_REJECTED:
        if context.startswith("print"):
            raise PrintRejectedError("print rejected")
        raise InstantLinkBleError(f"{context}: unexpected printer response", code=code)
    raise InstantLinkError(f"{context}: InstantLink error {code}", code=code)


def _parse_instantlink_model(value: str) -> PrinterModel:
    normalized = value.casefold()
    if "mini link 3" in normalized or "minilink3" in normalized:
        return PrinterModel.MINI_LINK3
    if "square" in normalized:
        return PrinterModel.SQUARE
    if "wide" in normalized:
        return PrinterModel.WIDE
    if "mini" in normalized:
        return PrinterModel.MINI
    raise InstantLinkBleError(f"unknown InstantLink model string: {value}", code=ERROR_BLE)


def _compatible_model_override(
    detected_model: PrinterModel,
    model_override: PrinterModel | None,
) -> PrinterModel:
    if model_override is None or model_override is detected_model:
        return detected_model
    detected_spec = spec_for(detected_model)
    override_spec = spec_for(model_override)
    if (detected_spec.width, detected_spec.height) == (override_spec.width, override_spec.height):
        LOGGER.info(
            "instantlink.model_override detected=%s override=%s",
            detected_model.value,
            model_override.value,
        )
        return model_override
    LOGGER.warning(
        "instantlink.model_override_ignored detected=%s override=%s",
        detected_model.value,
        model_override.value,
    )
    return detected_model


def _printer_names_match(left: str, right: str) -> bool:
    left_normalized = normalize_printer_name(left).casefold()
    right_normalized = normalize_printer_name(right).casefold()
    if left_normalized == right_normalized:
        return True
    left_serial = _extract_serial(left_normalized)
    right_serial = _extract_serial(right_normalized)
    return bool(left_serial and right_serial and left_serial == right_serial)


def _extract_serial(name: str) -> str | None:
    normalized = normalize_printer_name(name).upper()
    if not normalized.startswith("INSTAX-"):
        return None
    serial = "".join(ch for ch in normalized.removeprefix("INSTAX-") if ch.isalnum())
    return serial or None


def _is_android_advertisement(name: str) -> bool:
    return name.strip().upper().endswith("(ANDROID)")


def _connect_target_name(name: str) -> str:
    if _has_platform_suffix(name):
        return name.strip()
    return normalize_printer_name(name)


def _has_platform_suffix(name: str) -> bool:
    upper = name.strip().upper()
    return upper.endswith("(IOS)") or upper.endswith("(ANDROID)")


class _ConnectStageTracker:
    """Capture the highest connect-progress stage seen during one connect attempt."""

    def __init__(self) -> None:
        self.max_stage: int | None = None

    def observe(self, stage: int) -> None:
        if stage == _CONNECT_STAGE_FAILED:
            # The terminal "failed" sentinel is not a real progress milestone; ignore it so the
            # recorded max reflects how far the connection actually advanced.
            return
        if self.max_stage is None or stage > self.max_stage:
            self.max_stage = stage


_CONNECT_STAGE_FAILED = 10


def _connect_progress_logger(
    target_name: str,
    stage_tracker: _ConnectStageTracker | None = None,
) -> Callable[[int, bytes | None], None]:
    def emit(stage: int, detail: bytes | None) -> None:
        if stage_tracker is not None:
            stage_tracker.observe(stage)
        stage_name = CONNECT_STAGE_NAMES.get(stage, "unknown")
        detail_text = detail.decode("utf-8", errors="replace") if detail else ""
        LOGGER.info(
            "instantlink.connect_progress target=%s stage=%s detail=%s",
            target_name,
            stage_name,
            detail_text,
        )

    return _CONNECT_PROGRESS_CALLBACK(emit)


def _dedupe_names(names: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = normalize_printer_name(name).casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalize_printer_name(name))
    return deduped


def _name_from_address(address: str) -> str:
    if address.upper().startswith("INSTANTLINK:"):
        return address.split(":", maxsplit=1)[1]
    return address
