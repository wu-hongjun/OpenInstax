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

_PRINT_PROGRESS_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_uint32)
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
            if isinstance(value, str) and value.strip()
        ]
        return _dedupe_names(names)

    def _status_blocking(self, name: str, scan_duration_s: int) -> InstantLinkStatus:
        try:
            return self._status_blocking_connected(name, scan_duration_s)
        except (InstantLinkBleError, InstantLinkPrinterNotFoundError, TimeoutError):
            LOGGER.warning(
                "instantlink.status_failed_disconnect name=%s",
                normalize_printer_name(name),
                exc_info=True,
            )
            self._disconnect_blocking()
            raise

    def _status_blocking_connected(self, name: str, scan_duration_s: int) -> InstantLinkStatus:
        self._ensure_connected_blocking(name, scan_duration_s=scan_duration_s)

        film = ctypes.c_int()
        charging = ctypes.c_int()
        battery_value = int(self._library().instantlink_battery())
        if battery_value < 0:
            _raise_for_code(battery_value, "status battery failed")

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
            battery=battery_value,
            film_remaining=film.value,
            is_charging=bool(charging.value),
            print_count=None,
        )

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
        target_name = normalize_printer_name(name)
        connected_name = self._device_name_blocking()
        if connected_name is not None and _printer_names_match(connected_name, target_name):
            return
        rc = int(
            self._library().instantlink_connect_named(
                target_name.encode("utf-8"),
                max(1, scan_duration_s),
            )
        )
        if rc < 0:
            _raise_for_code(rc, "connect failed")

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
        raise PrintRejectedError("print rejected")
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
