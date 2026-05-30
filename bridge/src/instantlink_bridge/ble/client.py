"""Bleak-backed transport skeleton for Instax printers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from instantlink_bridge.ble import protocol
from instantlink_bridge.ble.instax import InstaxProtocolClient
from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.ble.session import InstaxBleSessionManager, PrinterEndpoint
from instantlink_bridge.imaging.pipeline import FitMode, PrintEdit
from instantlink_bridge.imaging.postprocess import AdjustmentProfile
from instantlink_bridge.imaging.worker import prepare_for_instax_async
from instantlink_bridge.printing import PrintProgress, PrintProgressCallback, PrintStage

INSTAX_SERVICE_UUID = "70954782-2d83-473d-9e5f-81e1d02d5273"
INSTAX_WRITE_CHAR_UUID = "70954783-2d83-473d-9e5f-81e1d02d5273"
INSTAX_NOTIFY_CHAR_UUID = "70954784-2d83-473d-9e5f-81e1d02d5273"
DIS_MODEL_NUMBER_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
LOGGER = logging.getLogger(__name__)
BLE_CONNECT_TIMEOUT_S = 10.0
BLE_WRITE_TIMEOUT_S = 5.0
BLE_GATT_READ_TIMEOUT_S = 5.0
BLE_CLEANUP_TIMEOUT_S = 5.0
IMAGE_PREP_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class DiscoveredPrinter:
    """BLE advertisement summary."""

    name: str
    address: str
    device: object | None = None


_DISCOVERED_DEVICE_CACHE: dict[str, object] = {}


class _BleakClientProtocol(Protocol):
    async def connect(self, **kwargs: object) -> object:
        """Connect to the BLE device."""

    async def disconnect(self) -> object:
        """Disconnect from the BLE device."""

    async def read_gatt_char(self, char_specifier: str) -> bytearray:
        """Read a GATT characteristic."""

    async def start_notify(
        self,
        char_specifier: str,
        callback: Callable[[object, bytearray], None],
    ) -> None:
        """Start notification subscription."""

    async def stop_notify(self, char_specifier: str) -> None:
        """Stop notification subscription."""

    async def write_gatt_char(
        self,
        char_specifier: str,
        data: bytes,
        *,
        response: bool,
    ) -> None:
        """Write a GATT characteristic."""


class BleakInstaxTransport:
    """Bleak transport implementing the Instax protocol transport contract."""

    def __init__(self, client: _BleakClientProtocol, model_number: str | None = None) -> None:
        self._client = client
        self._model_number = model_number
        self._notifications: asyncio.Queue[bytes] = asyncio.Queue()
        self._assembler = protocol.PacketAssembler()
        self._command_lock = asyncio.Lock()

    async def start_notify(self) -> None:
        """Subscribe to Instax notifications."""

        await asyncio.wait_for(
            self._client.start_notify(INSTAX_NOTIFY_CHAR_UUID, self._on_notification),
            timeout=BLE_CLEANUP_TIMEOUT_S,
        )

    async def stop_notify(self) -> None:
        """Unsubscribe from Instax notifications."""

        await asyncio.wait_for(
            self._client.stop_notify(INSTAX_NOTIFY_CHAR_UUID),
            timeout=BLE_CLEANUP_TIMEOUT_S,
        )

    async def send(self, data: bytes) -> None:
        """Send one protocol packet, fragmented for BLE writes."""

        for fragment in protocol.fragment(data):
            await asyncio.wait_for(
                self._client.write_gatt_char(
                    INSTAX_WRITE_CHAR_UUID,
                    fragment,
                    response=False,
                ),
                timeout=BLE_WRITE_TIMEOUT_S,
            )

    async def receive(self, timeout_s: float = 10.0) -> protocol.Packet:
        """Receive the next assembled protocol packet."""

        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            try:
                packet = self._assembler.feed(b"")
            except protocol.ProtocolError:
                packet = None
            if packet is not None:
                return packet

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("printer response timed out")
            data = await asyncio.wait_for(self._notifications.get(), timeout=remaining)
            try:
                packet = self._assembler.feed(data)
            except protocol.ProtocolError:
                continue
            if packet is not None:
                return packet

    async def send_and_receive(
        self,
        data: bytes,
        timeout_s: float = 10.0,
    ) -> protocol.Packet:
        """Serialize command/response operations."""

        async with self._command_lock:
            await self.send(data)
            return await self.receive(timeout_s)

    def model_number_hint(self) -> str | None:
        """Return optional DIS Model Number string."""

        return self._model_number

    def _on_notification(self, _sender: object, data: bytearray) -> None:
        self._notifications.put_nowait(bytes(data))


@dataclass(slots=True)
class ConnectedInstaxPrinter:
    """Connected Bleak client plus model-aware protocol client."""

    client: _BleakClientProtocol
    transport: BleakInstaxTransport
    protocol: InstaxProtocolClient

    async def disconnect(self) -> None:
        """Stop notifications and disconnect."""

        try:
            await self.transport.stop_notify()
        except Exception:
            LOGGER.warning("ble.stop_notify_failed", exc_info=True)
        finally:
            try:
                await asyncio.wait_for(self.client.disconnect(), timeout=BLE_CLEANUP_TIMEOUT_S)
            except Exception:
                LOGGER.warning("ble.disconnect_failed", exc_info=True)


_DEFAULT_SESSION_MANAGER: InstaxBleSessionManager[ConnectedInstaxPrinter] | None = None


async def scan_instax_printers(timeout_s: float = 5.0) -> list[DiscoveredPrinter]:
    """Scan for nearby Instax printers by advertised name or service UUID."""

    from bleak import BleakScanner

    discovered = await BleakScanner.discover(timeout=timeout_s, return_adv=True)
    printers: list[DiscoveredPrinter] = []
    for device, advertisement in discovered.values():
        name = _advertised_name(device, advertisement)
        services = _advertised_services(device, advertisement)
        if name.startswith("INSTAX") or INSTAX_SERVICE_UUID in services:
            address = str(getattr(device, "address", ""))
            _cache_discovered_device(address, device)
            printers.append(DiscoveredPrinter(name=name or address, address=address, device=device))
    _append_known_session_endpoint(printers)
    return printers


def _advertised_name(device: object, advertisement: object) -> str:
    local_name = getattr(advertisement, "local_name", None)
    device_name = getattr(device, "name", None)
    return str(local_name or device_name or "")


def _advertised_services(device: object, advertisement: object) -> set[str]:
    service_uuids = getattr(advertisement, "service_uuids", None)
    if service_uuids is None:
        metadata = getattr(device, "metadata", {})
        if isinstance(metadata, dict):
            service_uuids = metadata.get("uuids", [])
    if not isinstance(service_uuids, list | tuple | set):
        return set()
    return {str(service).lower() for service in service_uuids}


async def connect_instax_printer(
    address: str,
    name: str | None = None,
    *,
    timeout_s: float = BLE_CONNECT_TIMEOUT_S,
    model_override: PrinterModel | None = None,
) -> ConnectedInstaxPrinter:
    """Connect to a printer address and create a model-aware protocol client."""

    from bleak import BleakClient

    started_at = time.perf_counter()
    cached_device = _cached_discovered_device(address)
    client_target = cached_device if cached_device is not None else address
    LOGGER.info(
        "ble.connect_start address=%s name=%s fresh_scan_device=%s",
        address,
        name or "",
        cached_device is not None,
    )
    client = BleakClient(cast(Any, client_target), services=[INSTAX_SERVICE_UUID])
    transport: BleakInstaxTransport | None = None
    try:
        await asyncio.wait_for(client.connect(), timeout=timeout_s)
        model_number = await _read_model_number(client)
        transport = BleakInstaxTransport(client, model_number=model_number)
        await transport.start_notify()
        protocol_client = await InstaxProtocolClient.create(
            transport,
            name or address,
            model_override=model_override,
        )
        LOGGER.info(
            "ble.connect_ready address=%s name=%s model=%s elapsed_s=%.3f",
            address,
            name or "",
            protocol_client.model.value,
            time.perf_counter() - started_at,
        )
        return ConnectedInstaxPrinter(
            client=client,
            transport=transport,
            protocol=protocol_client,
        )
    except (asyncio.CancelledError, Exception):
        await _cleanup_partial_connection(client, transport)
        raise


def default_ble_session_manager() -> InstaxBleSessionManager[ConnectedInstaxPrinter]:
    """Return the process-wide BLE session manager used by status and print flows."""

    global _DEFAULT_SESSION_MANAGER
    if _DEFAULT_SESSION_MANAGER is None:
        _DEFAULT_SESSION_MANAGER = InstaxBleSessionManager(
            _connect_session_endpoint,
            connect_timeout_s=BLE_CONNECT_TIMEOUT_S,
            connected_model=lambda connected: connected.protocol.model,
        )
    return _DEFAULT_SESSION_MANAGER


async def close_default_ble_session_manager(*, forget_endpoint: bool = True) -> None:
    """Close the process-wide BLE session manager if it has been created."""

    if _DEFAULT_SESSION_MANAGER is not None:
        await _DEFAULT_SESSION_MANAGER.close(forget_endpoint=forget_endpoint)


async def _connect_session_endpoint(
    endpoint: PrinterEndpoint,
    model_override: PrinterModel | None,
) -> ConnectedInstaxPrinter:
    return await connect_instax_printer(
        endpoint.address,
        name=endpoint.name,
        model_override=model_override,
    )


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
    adjustments: AdjustmentProfile | None = None,
    session_manager: InstaxBleSessionManager[ConnectedInstaxPrinter] | None = None,
) -> None:
    """Connect, prepare for the detected model, print, and disconnect."""

    started_at = time.perf_counter()
    _emit_progress(
        progress,
        PrintProgress(PrintStage.CONNECTING, "Connecting", name or "Instax printer", None),
    )
    manager = session_manager if session_manager is not None else default_ble_session_manager()
    endpoint = PrinterEndpoint(address=address, name=name or address, model=model)
    lease = await manager.acquire_print(endpoint, model_override=model)
    connected = lease.connected
    failed = False
    try:
        _emit_progress(
            progress,
            PrintProgress(
                PrintStage.PREPARING,
                "Preparing image",
                f"{connected.protocol.model.value} output",
                None,
            ),
        )
        prepare_started_at = time.perf_counter()
        prepared = await prepare_for_instax_async(
            image_path,
            connected.protocol.model,
            fit=fit,
            quality=quality,
            edit=edit,
            adjustments=adjustments,
            timeout_s=IMAGE_PREP_TIMEOUT_S,
        )
        LOGGER.info(
            "image.prepare_complete path=%s model=%s fit=%s quality=%s bytes=%s elapsed_s=%.3f",
            image_path,
            prepared.model.value,
            prepared.fit.value,
            prepared.quality,
            len(prepared.data),
            time.perf_counter() - prepare_started_at,
        )
        _emit_progress(
            progress,
            PrintProgress(
                PrintStage.SENDING,
                "Sending to printer",
                f"{len(prepared.data) // 1024} KB",
                0,
            ),
        )
        chunk_progress = _chunk_progress_emitter(progress, len(prepared.data))
        send_started_at = time.perf_counter()
        await connected.protocol.print_prepared(
            prepared,
            print_option=print_option,
            progress=chunk_progress,
        )
        LOGGER.info(
            "ble.print_send_complete path=%s model=%s bytes=%s elapsed_s=%.3f",
            image_path,
            prepared.model.value,
            len(prepared.data),
            time.perf_counter() - send_started_at,
        )
        _emit_progress(
            progress,
            PrintProgress(PrintStage.FINISHING, "Finishing", "Waiting for printer", 100),
        )
    except BaseException:
        failed = True
        raise
    finally:
        await lease.release(failed=failed, keep_connected=False)
    LOGGER.info(
        "ble.print_file_complete path=%s address=%s elapsed_s=%.3f",
        image_path,
        address,
        time.perf_counter() - started_at,
    )


async def _read_model_number(client: _BleakClientProtocol) -> str | None:
    try:
        data = await asyncio.wait_for(
            client.read_gatt_char(DIS_MODEL_NUMBER_UUID),
            timeout=BLE_GATT_READ_TIMEOUT_S,
        )
    except Exception:
        return None
    model = bytes(data).decode("utf-8", errors="ignore").strip()
    return model or None


async def _cleanup_partial_connection(
    client: _BleakClientProtocol,
    transport: BleakInstaxTransport | None,
) -> None:
    if transport is not None:
        await _run_cleanup_step(
            transport.stop_notify(),
            log_event="ble.connect_stop_notify_cleanup_failed",
        )
    await _run_cleanup_step(
        client.disconnect(),
        log_event="ble.connect_cleanup_failed",
    )


async def _run_cleanup_step(
    cleanup: Awaitable[object],
    *,
    log_event: str,
) -> None:
    task = asyncio.ensure_future(cleanup)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=BLE_CLEANUP_TIMEOUT_S)
    except TimeoutError:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        LOGGER.warning("%s timeout_s=%s", log_event, BLE_CLEANUP_TIMEOUT_S, exc_info=True)
    except Exception:
        LOGGER.warning(log_event, exc_info=True)


def _chunk_progress_emitter(
    progress: PrintProgressCallback | None,
    image_size: int,
) -> Callable[[int, int], None] | None:
    if progress is None:
        return None
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
                # detail = None: chunk count + KB size dropped per user
                # request; the progress bar carries the full signal.
                None,
                percent,
            ),
        )

    return emit


def _emit_progress(
    progress: PrintProgressCallback | None,
    update: PrintProgress,
) -> None:
    if progress is not None:
        progress(update)


def _append_known_session_endpoint(printers: list[DiscoveredPrinter]) -> None:
    endpoint = default_ble_session_manager().known_endpoint()
    if endpoint is None:
        return
    if any(_same_printer(candidate, endpoint) for candidate in printers):
        return
    printers.append(DiscoveredPrinter(address=endpoint.address, name=endpoint.name))


def _same_printer(candidate: DiscoveredPrinter, endpoint: PrinterEndpoint) -> bool:
    return candidate.address.upper() == endpoint.address.upper() or candidate.name == endpoint.name


def _cache_discovered_device(address: str, device: object) -> None:
    normalized = address.strip().upper()
    if normalized:
        _DISCOVERED_DEVICE_CACHE[normalized] = device


def _cached_discovered_device(address: str) -> object | None:
    return _DISCOVERED_DEVICE_CACHE.get(address.strip().upper())
