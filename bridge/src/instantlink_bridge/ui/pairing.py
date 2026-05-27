"""Bluetooth printer selection helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from instantlink_bridge.ble.instantlink import (
    default_instantlink_backend,
    normalize_printer_name,
    stable_instantlink_address,
)
from instantlink_bridge.ble.models import PrinterModel, parse_printer_model
from instantlink_bridge.ui.models import PairedPrinter

LOGGER = logging.getLogger(__name__)

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DEVICE_LINE_RE = re.compile(r"^Device\s+(?P<address>[0-9A-Fa-f:]{17})\s+(?P<name>.+)$")
DEVICE_ADDRESS_RE = re.compile(r"^[0-9A-Fa-f:]{17}$")


class PrinterPairingError(RuntimeError):
    """Raised when printer selection cannot complete."""


class PrinterPairer(Protocol):
    """Printer selection interface used by the UI controller."""

    async def list_paired(self) -> list[PairedPrinter]:
        """Return the selected or BlueZ-paired Instax printer."""

    async def pair_first_available(self) -> PairedPrinter:
        """Scan for and select the first visible Instax printer."""

    def save_selected(self, printer: PairedPrinter) -> None:
        """Persist the selected printer identity."""

    async def forget_selected(self) -> None:
        """Forget the selected printer and matching BlueZ cache entries."""

    async def remove_bluez_bond(self, printer: PairedPrinter) -> None:
        """Remove only the BlueZ bond/cache for a printer, keeping it selected."""

    async def disconnect_bluez_link(self, printer: PairedPrinter) -> bool:
        """Drop any connected-but-silent BlueZ link for a printer so it re-advertises."""


class InstantLinkSelectionBackend(Protocol):
    """Subset of the InstantLink backend needed by the printer-selection UI."""

    async def scan(self, timeout_s: float = 1.0) -> list[str]:
        """Return visible printer names."""

    async def disconnect(self) -> None:
        """Disconnect any cached printer session."""


@dataclass(frozen=True, slots=True)
class _CommandResult:
    returncode: int
    output: str


class SelectedPrinterStore:
    """Persist the user's selected Instax printer.

    Instax Link printers do not reliably support OS-level BlueZ bonding. The appliance still
    needs a stable "paired" concept for boot UX, so we store the selected printer identity here
    and resolve/connect by BLE advertisement when printing.
    """

    def __init__(self, path: Path = Path("/var/lib/InstantLinkBridge/printer.json")) -> None:
        self._path = path

    def load(self) -> PairedPrinter | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("bluetooth.selected_printer_load_failed path=%s", self._path)
            return None
        if not isinstance(data, dict):
            return None
        address = data.get("address")
        name = data.get("name")
        if not isinstance(address, str) or not isinstance(name, str):
            return None
        return PairedPrinter(address=address, name=name, model=_load_model(data.get("model")))

    def save(self, printer: PairedPrinter) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "address": printer.address,
            "name": normalize_instax_name(printer.name),
        }
        if printer.model is not None:
            data["model"] = printer.model.value
        text = json.dumps(data, indent=2, sort_keys=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            dir=self._path.parent,
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(text)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            if self._path.exists():
                tmp_path.chmod(self._path.stat().st_mode & 0o777)
            else:
                tmp_path.chmod(0o660)
            os.replace(tmp_path, self._path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def forget(self) -> bool:
        """Remove the persisted selected printer, returning true when it existed."""

        try:
            self._path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise PrinterPairingError(f"Unable to forget selected printer: {self._path}") from exc
        return True


class BluetoothctlPrinterPairer:
    """Select Instax printers through BlueZ's bluetoothctl CLI."""

    def __init__(
        self,
        scan_seconds: int = 30,
        store: SelectedPrinterStore | None = None,
    ) -> None:
        self._scan_seconds = scan_seconds
        self._store = store if store is not None else SelectedPrinterStore()

    async def list_paired(self) -> list[PairedPrinter]:
        selected = self._store.load()
        if selected is not None:
            await self._trust_selected_bluez_devices(selected)
            return [selected]
        result = await _run_bluetoothctl("devices", "Paired", check=False)
        paired = parse_instax_devices(result.output)
        for printer in paired:
            await self._trust_selected_bluez_devices(printer)
        return paired

    async def pair_first_available(self) -> PairedPrinter:
        await _run_bluetoothctl("power", "on", check=False)
        await _run_bluetoothctl("scan", "off", check=False)
        await self._remove_stale_unpaired_instax_devices()
        try:
            candidates = await self._scan_for_instax_devices()
        finally:
            await _run_bluetoothctl("scan", "off", check=False)
        if not candidates:
            raise PrinterPairingError("No INSTAX-* printer found")

        printer = candidates[0]
        selected = PairedPrinter(
            address=printer.address,
            name=normalize_instax_name(printer.name),
        )
        await self._trust_selected_bluez_devices(selected)
        self._store.save(selected)
        LOGGER.info(
            "bluetooth.instax_selected address=%s name=%s",
            selected.address,
            selected.name,
        )
        return selected

    def save_selected(self, printer: PairedPrinter) -> None:
        """Persist the selected printer identity."""

        self._store.save(printer)

    async def forget_selected(self) -> None:
        """Forget the selected printer and remove matching BlueZ cache entries."""

        selected = self._store.load()
        removed = self._store.forget()
        if selected is None:
            LOGGER.info("bluetooth.forget_selected selected=none removed_store=%s", removed)
            return

        LOGGER.info(
            "bluetooth.forget_selected address=%s name=%s removed_store=%s",
            selected.address,
            selected.name,
            removed,
        )
        await self._remove_selected_bluez_devices(selected)

    async def remove_bluez_bond(self, printer: PairedPrinter) -> None:
        """Remove only the BlueZ bond/cache for a printer, keeping it selected.

        This mirrors the cache-removal half of ``forget_selected`` but never touches the
        persisted selection. It is used by the auto-rebond recovery path so the existing
        ``NoInputNoOutput`` agent re-bonds with a fresh key on the next connect.
        """

        LOGGER.info(
            "bluetooth.remove_bond address=%s name=%s",
            printer.address,
            printer.name,
        )
        await self._remove_selected_bluez_devices(printer)

    async def disconnect_bluez_link(self, printer: PairedPrinter) -> bool:
        """Drop any connected-but-silent BlueZ link for ``printer`` so it re-advertises."""

        return await _disconnect_bluez_link_for_identity(printer)

    async def _scan_for_instax_devices(self) -> list[PairedPrinter]:
        """Scan in short passes and return as soon as an Instax printer is visible."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._scan_seconds
        candidates: list[PairedPrinter] = []
        while loop.time() < deadline:
            scan_result = await _run_bluetoothctl(
                "--timeout",
                "1",
                "scan",
                "on",
                check=False,
                timeout_seconds=4,
            )
            devices_result = await _run_bluetoothctl("devices", check=False)
            candidates = sorted(
                _dedupe_devices(
                    [
                        *parse_instax_devices(scan_result.output),
                        *parse_instax_devices(devices_result.output),
                    ]
                ),
                key=_candidate_priority,
            )
            LOGGER.info(
                "bluetooth.instax_candidates count=%s candidates=%s",
                len(candidates),
                _format_candidate_list(candidates),
            )
            if candidates:
                return candidates
        return candidates

    async def _remove_stale_unpaired_instax_devices(self) -> None:
        devices_result = await _run_bluetoothctl("devices", check=False)
        for printer in parse_instax_devices(devices_result.output):
            info_result = await _run_bluetoothctl("info", printer.address, check=False)
            info = parse_device_info(info_result.output)
            if _info_bool(info, "Paired"):
                continue
            LOGGER.info(
                "bluetooth.remove_stale_instax address=%s name=%s",
                printer.address,
                printer.name,
            )
            await _run_bluetoothctl("remove", printer.address, check=False)

    async def _remove_selected_bluez_devices(self, selected: PairedPrinter) -> None:
        addresses = await _bluez_addresses_for_identity(selected, include_selected_address=True)
        for address in addresses:
            LOGGER.info(
                "bluetooth.remove_selected_cache address=%s selected_address=%s selected_name=%s",
                address,
                selected.address,
                selected.name,
            )
            await _run_bluetoothctl("remove", address, check=False)

    async def _trust_selected_bluez_devices(self, selected: PairedPrinter) -> None:
        devices_result = await _run_bluetoothctl("devices", check=False)
        addresses = {selected.address.upper()}
        for printer in parse_instax_devices(devices_result.output):
            if _matches_selected_identity(selected, printer):
                addresses.add(printer.address.upper())

        for address in sorted(addresses):
            if not _looks_like_bluez_address(address):
                continue
            LOGGER.info(
                "bluetooth.trust_selected_cache address=%s selected_address=%s selected_name=%s",
                address,
                selected.address,
                selected.name,
            )
            await _run_bluetoothctl("trust", address, check=False)


class InstantLinkPrinterSelector:
    """Select Instax printers through InstantLink scan results.

    This is intentionally a selection flow, not an OS-level Bluetooth pairing flow. Instax Link
    printers are Just-Works peripherals that do not reliably create durable BlueZ bonds on Linux,
    and InstantLink resolves the selected device by normalized printer name when connecting.
    """

    def __init__(
        self,
        scan_seconds: int = 30,
        store: SelectedPrinterStore | None = None,
        backend: InstantLinkSelectionBackend | None = None,
    ) -> None:
        self._scan_seconds = scan_seconds
        self._store = store if store is not None else SelectedPrinterStore()
        self._backend = backend if backend is not None else default_instantlink_backend()

    async def list_paired(self) -> list[PairedPrinter]:
        """Return the selected InstantLink printer."""

        selected = self._store.load()
        return [selected] if selected is not None else []

    async def pair_first_available(self) -> PairedPrinter:
        """Scan for and select the first visible Instax printer."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._scan_seconds
        candidates: list[str] = []
        while loop.time() < deadline:
            candidates = await self._backend.scan(1.0)
            LOGGER.info(
                "instantlink.printer_candidates count=%s candidates=%s",
                len(candidates),
                ",".join(candidates) if candidates else "none",
            )
            if candidates:
                break
        if not candidates:
            raise PrinterPairingError("No INSTAX-* printer found")

        name = normalize_printer_name(candidates[0])
        selected = PairedPrinter(address=stable_instantlink_address(name), name=name)
        self._store.save(selected)
        LOGGER.info(
            "instantlink.printer_selected address=%s name=%s",
            selected.address,
            selected.name,
        )
        return selected

    def save_selected(self, printer: PairedPrinter) -> None:
        """Persist the selected printer identity."""

        self._store.save(
            PairedPrinter(
                address=stable_instantlink_address(printer.name),
                name=normalize_printer_name(printer.name),
                model=printer.model,
            )
        )

    async def forget_selected(self) -> None:
        """Forget the selected printer and disconnect InstantLink's cached session."""

        selected = self._store.load()
        removed = self._store.forget()
        await self._backend.disconnect()
        LOGGER.info(
            "instantlink.forget_selected selected=%s removed_store=%s",
            selected.name if selected is not None else "none",
            removed,
        )

    async def remove_bluez_bond(self, printer: PairedPrinter) -> None:
        """Remove only the BlueZ bond/cache for a printer, keeping it selected.

        InstantLink selects printers by normalized name and stores a pseudo-address, so the
        matching BlueZ device(s) are located by Instax-name match against ``bluetoothctl
        devices``. The persisted selection is intentionally left untouched; the cached
        InstantLink session is dropped so the next connect re-bonds with a fresh key.
        """

        LOGGER.info(
            "instantlink.remove_bond address=%s name=%s",
            printer.address,
            printer.name,
        )
        await _remove_bluez_devices_for_identity(printer)
        await self._backend.disconnect()

    async def disconnect_bluez_link(self, printer: PairedPrinter) -> bool:
        """Drop any connected-but-silent BlueZ link for ``printer`` so it re-advertises.

        The cached InstantLink session is dropped first so the bridge is not holding the link it
        is about to disconnect; the BlueZ link itself is the one BlueZ auto-reconnected.
        """

        disconnected = await _disconnect_bluez_link_for_identity(printer)
        if disconnected:
            await self._backend.disconnect()
        return disconnected


def parse_instax_devices(output: str) -> list[PairedPrinter]:
    """Parse bluetoothctl device output and keep Instax Link devices."""

    devices: list[PairedPrinter] = []
    seen: set[str] = set()
    for raw_line in clean_bluetoothctl_output(output).splitlines():
        line = _strip_bluetoothctl_prefix(raw_line.strip())
        match = DEVICE_LINE_RE.match(line)
        if match is None:
            continue
        address = match.group("address").upper()
        name = match.group("name").strip()
        if address in seen or not is_instax_device_name(name):
            continue
        seen.add(address)
        devices.append(PairedPrinter(address=address, name=name))
    return devices


def is_instax_device_name(name: str) -> bool:
    """Return true when a Bluetooth name looks like an Instax printer."""

    normalized = name.strip().upper()
    return normalized.startswith("INSTAX-") or normalized.startswith("INSTAX_")


def normalize_instax_name(name: str) -> str:
    """Strip platform suffixes from Instax advertising names."""

    return re.sub(r"\s*\((IOS|ANDROID)\)$", "", name.strip(), flags=re.IGNORECASE).strip()


async def _bluez_addresses_for_identity(
    selected: PairedPrinter,
    *,
    include_selected_address: bool,
) -> list[str]:
    """Return BlueZ device addresses matching a printer identity, sorted ascending."""

    addresses: set[str] = set()
    if include_selected_address and _looks_like_bluez_address(selected.address.upper()):
        addresses.add(selected.address.upper())
    devices_result = await _run_bluetoothctl("devices", check=False)
    for printer in parse_instax_devices(devices_result.output):
        if _matches_selected_identity(selected, printer):
            addresses.add(printer.address.upper())
    return sorted(addresses)


async def _remove_bluez_devices_for_identity(selected: PairedPrinter) -> None:
    """Remove every BlueZ device whose name/address matches the selected printer identity."""

    addresses = await _bluez_addresses_for_identity(selected, include_selected_address=False)
    for address in addresses:
        LOGGER.info(
            "bluetooth.remove_bond_cache address=%s selected_name=%s",
            address,
            selected.name,
        )
        await _run_bluetoothctl("remove", address, check=False)


async def _disconnect_bluez_link_for_identity(selected: PairedPrinter) -> bool:
    """Drop any *connected* BlueZ link for the selected printer; keep the bond.

    A power-cycled bonded printer is frequently auto-reconnected by BlueZ, which holds a silent
    link: a connected peripheral stops advertising, so InstantLink's advertisement-based scan can
    never find it and status connects loop on ``PrinterNotFound``. Disconnecting that link makes
    the printer resume advertising so the next scan can adopt it. Bond/selection are untouched, so
    the reconnect does not need to re-pair. Returns true only when a connected link was dropped;
    when the printer is genuinely off no device shows ``Connected`` and this is a safe no-op.
    """

    addresses = await _bluez_addresses_for_identity(selected, include_selected_address=True)
    disconnected = False
    for address in addresses:
        info_result = await _run_bluetoothctl("info", address, check=False)
        if not _info_bool(parse_device_info(info_result.output), "Connected"):
            continue
        LOGGER.info(
            "bluetooth.disconnect_silent_link address=%s selected_name=%s",
            address,
            selected.name,
        )
        await _run_bluetoothctl("disconnect", address, check=False)
        disconnected = True
    return disconnected


def _matches_selected_identity(selected: PairedPrinter, candidate: PairedPrinter) -> bool:
    if candidate.address.upper() == selected.address.upper():
        return True
    selected_name = normalize_instax_name(selected.name).casefold()
    candidate_name = normalize_instax_name(candidate.name).casefold()
    return bool(selected_name and candidate_name and selected_name == candidate_name)


def _looks_like_bluez_address(value: str) -> bool:
    return DEVICE_ADDRESS_RE.fullmatch(value) is not None


def parse_device_info(output: str) -> dict[str, str]:
    """Parse `bluetoothctl info` key-value output."""

    info: dict[str, str] = {}
    for raw_line in clean_bluetoothctl_output(output).splitlines():
        line = raw_line.strip()
        if ": " not in line:
            continue
        key, value = line.split(": ", maxsplit=1)
        info[key.strip()] = value.strip()
    return info


def clean_bluetoothctl_output(output: str) -> str:
    """Remove ANSI escape sequences from bluetoothctl output."""

    return ANSI_ESCAPE_RE.sub("", output)


def _load_model(value: object) -> PrinterModel | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_printer_model(value)
    except ValueError:
        LOGGER.warning("bluetooth.selected_printer_model_invalid value=%s", value)
        return None


async def _run_bluetoothctl(
    *args: str,
    check: bool = True,
    timeout_seconds: int = 10,
) -> _CommandResult:
    process = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise PrinterPairingError(f"bluetoothctl {' '.join(args)} timed out") from exc

    output = clean_bluetoothctl_output(stdout.decode(errors="replace"))
    result = _CommandResult(returncode=process.returncode or 0, output=output)
    if check and result.returncode != 0:
        raise PrinterPairingError(_last_relevant_line(output, "bluetoothctl failed"))
    return result


def _strip_bluetoothctl_prefix(line: str) -> str:
    if line.startswith("[") and "] " in line:
        return line.split("] ", maxsplit=1)[1]
    return line


def _dedupe_devices(devices: list[PairedPrinter]) -> list[PairedPrinter]:
    deduped: list[PairedPrinter] = []
    seen: set[str] = set()
    for device in devices:
        if device.address in seen:
            continue
        deduped.append(device)
        seen.add(device.address)
    return deduped


def _info_bool(info: dict[str, str], key: str) -> bool:
    return info.get(key, "").casefold() in {"yes", "true"}


def _last_relevant_line(output: str, fallback: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else fallback


def _format_candidate_list(candidates: list[PairedPrinter]) -> str:
    if not candidates:
        return "none"
    return ",".join(f"{candidate.name}@{candidate.address}" for candidate in candidates)


def _candidate_priority(printer: PairedPrinter) -> tuple[int, str]:
    name = printer.name.upper()
    if name.endswith("(IOS)") or printer.address.startswith("FA:AB:BC"):
        return (0, printer.name)
    if name.endswith("(ANDROID)"):
        return (1, printer.name)
    return (2, printer.name)
