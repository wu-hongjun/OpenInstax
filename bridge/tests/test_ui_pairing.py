from __future__ import annotations

from pathlib import Path

import pytest

from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.ui import pairing
from instantlink_bridge.ui.models import PairedPrinter
from instantlink_bridge.ui.pairing import (
    BluetoothctlPrinterPairer,
    InstantLinkPrinterSelector,
    SelectedPrinterStore,
    clean_bluetoothctl_output,
    is_instax_device_name,
    normalize_instax_name,
    parse_device_info,
    parse_instax_devices,
)


class FakeInstantLinkBackend:
    def __init__(self, scans: list[list[str]]) -> None:
        self.scans = scans
        self.disconnect_calls = 0

    async def scan(self, _timeout_s: float = 1.0) -> list[str]:
        return self.scans.pop(0) if self.scans else []

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


def test_parse_instax_devices_keeps_instax_names() -> None:
    output = "\n".join(
        [
            "Device AA:BB:CC:DD:EE:01 Keyboard",
            "Device AA:BB:CC:DD:EE:02 INSTAX-12345678",
            "[NEW] Device AA:BB:CC:DD:EE:03 INSTAX_ABCD1234",
        ]
    )

    devices = parse_instax_devices(output)

    assert [device.address for device in devices] == [
        "AA:BB:CC:DD:EE:02",
        "AA:BB:CC:DD:EE:03",
    ]


def test_parse_instax_devices_handles_ansi_bluetoothctl_prefixes() -> None:
    output = "[\x1b[0;92mNEW\x1b[0m] Device 88:B4:36:51:CC:E2 INSTAX-1N034655(ANDROID)"

    devices = parse_instax_devices(output)

    assert devices[0].address == "88:B4:36:51:CC:E2"
    assert devices[0].name == "INSTAX-1N034655(ANDROID)"


def test_parse_instax_devices_deduplicates_addresses() -> None:
    output = "\n".join(
        [
            "Device AA:BB:CC:DD:EE:02 INSTAX-12345678",
            "[CHG] Device AA:BB:CC:DD:EE:02 INSTAX-12345678",
        ]
    )

    assert len(parse_instax_devices(output)) == 1


def test_is_instax_device_name_requires_prefix() -> None:
    assert is_instax_device_name("INSTAX-12345678")
    assert is_instax_device_name("INSTAX-1N034655(ANDROID)")
    assert not is_instax_device_name("My Instax Printer")


def test_normalize_instax_name_strips_platform_suffix() -> None:
    assert normalize_instax_name("INSTAX-1N034655(IOS)") == "INSTAX-1N034655"
    assert normalize_instax_name("INSTAX-1N034655 (IOS)") == "INSTAX-1N034655"
    assert normalize_instax_name("INSTAX-1N034655(ANDROID)") == "INSTAX-1N034655"
    assert normalize_instax_name("INSTAX-1N034655") == "INSTAX-1N034655"


def test_parse_device_info() -> None:
    output = "\n".join(
        [
            "Device 88:B4:36:51:CC:E2 (public)",
            "\tName: INSTAX-1N034655(ANDROID)",
            "\tPaired: yes",
            "\tTrusted: true",
        ]
    )

    assert parse_device_info(output) == {
        "Name": "INSTAX-1N034655(ANDROID)",
        "Paired": "yes",
        "Trusted": "true",
    }


def test_clean_bluetoothctl_output_strips_ansi() -> None:
    assert clean_bluetoothctl_output("\x1b[0;92mNEW\x1b[0m") == "NEW"


def test_selected_printer_store_round_trips_normalized_name(tmp_path: Path) -> None:
    store = SelectedPrinterStore(tmp_path / "printer.json")

    store.save(PairedPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)"))

    assert store.load() == PairedPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655")


def test_selected_printer_store_round_trips_detected_model(tmp_path: Path) -> None:
    store = SelectedPrinterStore(tmp_path / "printer.json")

    store.save(
        PairedPrinter(
            address="FA:AB:BC:51:CC:E2",
            name="INSTAX-1N034655",
            model=PrinterModel.SQUARE,
        )
    )

    assert store.load() == PairedPrinter(
        address="FA:AB:BC:51:CC:E2",
        name="INSTAX-1N034655",
        model=PrinterModel.SQUARE,
    )


def test_selected_printer_store_forgets_saved_printer(tmp_path: Path) -> None:
    store = SelectedPrinterStore(tmp_path / "printer.json")
    store.save(PairedPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655"))

    assert store.forget()

    assert store.load() is None
    assert not store.forget()


@pytest.mark.asyncio
async def test_instantlink_selector_persists_normalized_name(tmp_path: Path) -> None:
    store = SelectedPrinterStore(tmp_path / "printer.json")
    backend = FakeInstantLinkBackend([["INSTAX-52006924 (IOS)"]])

    selected = await InstantLinkPrinterSelector(store=store, backend=backend).pair_first_available()

    assert selected == PairedPrinter(
        address="INSTANTLINK:52006924",
        name="INSTAX-52006924",
    )
    assert store.load() == selected


@pytest.mark.asyncio
async def test_instantlink_selector_forget_disconnects_backend(tmp_path: Path) -> None:
    store = SelectedPrinterStore(tmp_path / "printer.json")
    store.save(PairedPrinter(address="INSTANTLINK:52006924", name="INSTAX-52006924"))
    backend = FakeInstantLinkBackend([])

    await InstantLinkPrinterSelector(store=store, backend=backend).forget_selected()

    assert store.load() is None
    assert backend.disconnect_calls == 1


@pytest.mark.asyncio
async def test_pairer_forget_selected_removes_matching_bluez_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SelectedPrinterStore(tmp_path / "printer.json")
    store.save(PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655"))
    calls: list[tuple[str, ...]] = []

    async def fake_run_bluetoothctl(
        *args: str,
        check: bool = True,
        timeout_seconds: int = 10,
    ) -> pairing._CommandResult:
        _ = check, timeout_seconds
        calls.append(args)
        if args == ("devices",):
            return pairing._CommandResult(
                returncode=0,
                output="\n".join(
                    [
                        "Device 88:B4:36:51:CC:E2 INSTAX-1N034655(ANDROID)",
                        "Device FA:AB:BC:51:CC:E2 INSTAX-1N034655(IOS)",
                        "Device AA:BB:CC:DD:EE:FF INSTAX-OTHER",
                    ]
                ),
            )
        return pairing._CommandResult(returncode=0, output="")

    monkeypatch.setattr(pairing, "_run_bluetoothctl", fake_run_bluetoothctl)

    await BluetoothctlPrinterPairer(store=store).forget_selected()

    assert store.load() is None
    assert ("remove", "88:B4:36:51:CC:E2") in calls
    assert ("remove", "FA:AB:BC:51:CC:E2") in calls
    assert ("remove", "AA:BB:CC:DD:EE:FF") not in calls


@pytest.mark.asyncio
async def test_pairer_list_paired_trusts_saved_bluez_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SelectedPrinterStore(tmp_path / "printer.json")
    store.save(PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655"))
    calls: list[tuple[str, ...]] = []

    async def fake_run_bluetoothctl(
        *args: str,
        check: bool = True,
        timeout_seconds: int = 10,
    ) -> pairing._CommandResult:
        _ = check, timeout_seconds
        calls.append(args)
        if args == ("devices",):
            return pairing._CommandResult(
                returncode=0,
                output="\n".join(
                    [
                        "Device 88:B4:36:51:CC:E2 INSTAX-1N034655(ANDROID)",
                        "Device FA:AB:BC:51:CC:E2 INSTAX-1N034655(IOS)",
                    ]
                ),
            )
        return pairing._CommandResult(returncode=0, output="")

    monkeypatch.setattr(pairing, "_run_bluetoothctl", fake_run_bluetoothctl)

    selected = await BluetoothctlPrinterPairer(store=store).list_paired()

    assert selected == [PairedPrinter(address="88:B4:36:51:CC:E2", name="INSTAX-1N034655")]
    assert ("trust", "88:B4:36:51:CC:E2") in calls
    assert ("trust", "FA:AB:BC:51:CC:E2") in calls


@pytest.mark.asyncio
async def test_pairer_pair_first_available_trusts_selected_printer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SelectedPrinterStore(tmp_path / "printer.json")
    calls: list[tuple[str, ...]] = []

    async def fake_run_bluetoothctl(
        *args: str,
        check: bool = True,
        timeout_seconds: int = 10,
    ) -> pairing._CommandResult:
        _ = check, timeout_seconds
        calls.append(args)
        if args == ("devices",):
            return pairing._CommandResult(returncode=0, output="")
        if args == ("--timeout", "1", "scan", "on"):
            return pairing._CommandResult(
                returncode=0,
                output="[NEW] Device FA:AB:BC:51:CC:E2 INSTAX-1N034655(IOS)",
            )
        return pairing._CommandResult(returncode=0, output="")

    monkeypatch.setattr(pairing, "_run_bluetoothctl", fake_run_bluetoothctl)

    selected = await BluetoothctlPrinterPairer(store=store).pair_first_available()

    assert selected == PairedPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655")
    assert ("trust", "FA:AB:BC:51:CC:E2") in calls
    assert store.load() == selected


@pytest.mark.asyncio
async def test_pairer_scan_returns_after_first_visible_instax(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_bluetoothctl(
        *args: str,
        check: bool = True,
        timeout_seconds: int = 10,
    ) -> pairing._CommandResult:
        _ = check, timeout_seconds
        calls.append(args)
        if args == ("--timeout", "1", "scan", "on"):
            return pairing._CommandResult(
                returncode=0,
                output="[NEW] Device FA:AB:BC:51:CC:E2 INSTAX-1N034655(IOS)",
            )
        if args == ("devices",):
            return pairing._CommandResult(returncode=0, output="")
        raise AssertionError(f"unexpected bluetoothctl call: {args}")

    monkeypatch.setattr(pairing, "_run_bluetoothctl", fake_run_bluetoothctl)

    candidates = await BluetoothctlPrinterPairer(scan_seconds=30)._scan_for_instax_devices()

    assert candidates == [PairedPrinter(address="FA:AB:BC:51:CC:E2", name="INSTAX-1N034655(IOS)")]
    assert calls == [
        ("--timeout", "1", "scan", "on"),
        ("devices",),
    ]
