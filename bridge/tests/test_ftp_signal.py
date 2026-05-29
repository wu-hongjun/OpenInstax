"""Tests for FTP pre-flight reply codes (Phase 6: bridge UX as FTP signals)."""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from instantlink_bridge.camera.ftp import FtpReceiveService, _printer_reachable
from instantlink_bridge.config import FtpConfig
from instantlink_bridge.ui.models import PairedPrinter, UiMode, UiSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PAIRED = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name="INSTAX-12345678")
_ONLINE_MODES = {
    UiMode.READY,
    UiMode.NO_FILM,
    UiMode.IMAGE_RECEIVED,
    UiMode.AWAITING_CONFIRM,
    UiMode.PRINT_COMPLETE,
}


def _make_snap(**kwargs: object) -> UiSnapshot:
    defaults: dict[str, object] = dict(
        mode=UiMode.READY,
        ftp_host="192.168.8.1",
        paired_printer=_PAIRED,
        printer_status_fresh=True,
        film_remaining=10,
        allow_print_without_film=False,
    )
    defaults.update(kwargs)
    return UiSnapshot(**defaults)  # type: ignore[arg-type]


def _make_service(snap: UiSnapshot | None) -> FtpReceiveService:
    queue: asyncio.Queue[object] = asyncio.Queue()
    loop = asyncio.new_event_loop()
    provider = (lambda: snap) if snap is not None else None
    return FtpReceiveService(
        FtpConfig(incoming_dir=pathlib.Path("/tmp/ftp-test")),
        queue,  # type: ignore[arg-type]
        loop,
        bridge_snapshot_provider=provider,
    )


# ---------------------------------------------------------------------------
# Reply-text length guard -- validated at import time
# ---------------------------------------------------------------------------

_REPLY_TEXTS = [
    "451 Bridge starting, try again in a moment.",
    "501 Bridge not paired. Pair from the Mac app.",
    "451 {name} offline. Power on printer.",  # name up to 15 chars
    "552 No film. Load film and retry.",
    "450 Printer busy, try again.",
]


@pytest.mark.parametrize("text", _REPLY_TEXTS)
def test_reply_texts_are_at_most_50_chars(text: str) -> None:
    # Replace the template placeholder with the maximum allowed name length.
    text = text.replace("{name}", "A" * 15)
    assert len(text) <= 50, f"Reply text too long ({len(text)} chars): {text!r}"


# ---------------------------------------------------------------------------
# Case 1 - bridge booting -> 451
# ---------------------------------------------------------------------------


def test_preflight_booting_returns_451() -> None:
    snap = _make_snap(mode=UiMode.BOOTING, printer_status_fresh=False)
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is not None
    assert reply.startswith("451")
    assert len(reply) <= 50


# ---------------------------------------------------------------------------
# Case 2 - not paired (not booting) -> 501
# ---------------------------------------------------------------------------


def test_preflight_not_paired_returns_501() -> None:
    snap = _make_snap(mode=UiMode.NEEDS_PAIRING, paired_printer=None, printer_status_fresh=False)
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is not None
    assert reply.startswith("501")
    assert len(reply) <= 50


# ---------------------------------------------------------------------------
# Case 3 - paired but printer offline -> 451 with printer name
# ---------------------------------------------------------------------------


def test_preflight_printer_offline_returns_451_with_name() -> None:
    snap = _make_snap(mode=UiMode.PRINTER_OFFLINE, printer_status_fresh=False)
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is not None
    assert reply.startswith("451")
    assert "INSTAX-12345678" in reply
    assert len(reply) <= 50


# ---------------------------------------------------------------------------
# Case 4 - no film + allow_print_without_film=False -> 552
# ---------------------------------------------------------------------------


def test_preflight_no_film_returns_552() -> None:
    snap = _make_snap(film_remaining=0, allow_print_without_film=False)
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is not None
    assert reply.startswith("552")
    assert len(reply) <= 50


# ---------------------------------------------------------------------------
# Case 5 - no film + allow_print_without_film=True -> None (fall through)
# ---------------------------------------------------------------------------


def test_preflight_no_film_but_override_returns_none() -> None:
    snap = _make_snap(film_remaining=0, allow_print_without_film=True)
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is None


# ---------------------------------------------------------------------------
# Case 6 - mode=PRINTING -> 450
# ---------------------------------------------------------------------------


def test_preflight_printing_returns_450() -> None:
    snap = _make_snap(mode=UiMode.PRINTING)
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is not None
    assert reply.startswith("450")
    assert len(reply) <= 50


# ---------------------------------------------------------------------------
# Case 7 - mode=IMAGE_RECEIVED -> None (queue absorbs concurrent uploads)
# ---------------------------------------------------------------------------


def test_preflight_image_received_falls_through() -> None:
    snap = _make_snap(mode=UiMode.IMAGE_RECEIVED)
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is None


# ---------------------------------------------------------------------------
# Case 8 - mode=READY, paired, online, film>0 -> None
# ---------------------------------------------------------------------------


def test_preflight_ready_state_returns_none() -> None:
    snap = _make_snap(mode=UiMode.READY, film_remaining=5)
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is None


# ---------------------------------------------------------------------------
# Case 9 - mode=AWAITING_CONFIRM -> None (user previewing, not busy)
# ---------------------------------------------------------------------------


def test_preflight_awaiting_confirm_returns_none() -> None:
    snap = _make_snap(mode=UiMode.AWAITING_CONFIRM)
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is None


# ---------------------------------------------------------------------------
# Case 10 - long printer name (>15 chars) -> reply <= 50 chars
# ---------------------------------------------------------------------------


def test_preflight_long_printer_name_truncated_in_reply() -> None:
    long_name = "INSTAX-VERYLONGPRINTERNAME"  # 26 chars
    printer = PairedPrinter(address="AA:BB:CC:DD:EE:FF", name=long_name)
    snap = _make_snap(
        mode=UiMode.PRINTER_OFFLINE,
        paired_printer=printer,
        printer_status_fresh=False,
    )
    service = _make_service(snap)

    reply = service._ftp_preflight_reply()

    assert reply is not None
    assert reply.startswith("451")
    assert len(reply) <= 50
    # The truncated name (first 15 chars) should appear; the full name must not
    assert long_name[:15] in reply
    assert long_name not in reply


# ---------------------------------------------------------------------------
# Case 11 - bridge_snapshot_provider=None -> None (graceful degradation)
# ---------------------------------------------------------------------------


def test_preflight_no_provider_returns_none() -> None:
    service = _make_service(None)

    reply = service._ftp_preflight_reply()

    assert reply is None


# ---------------------------------------------------------------------------
# _printer_reachable helper unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(_ONLINE_MODES))
def test_printer_reachable_true_for_operational_modes(mode: UiMode) -> None:
    snap = _make_snap(mode=mode, printer_status_fresh=True)
    assert _printer_reachable(snap) is True


@pytest.mark.parametrize(
    "mode",
    [
        UiMode.PRINTER_SEARCHING,
        UiMode.PRINTER_OFFLINE,
        UiMode.BOOTING,
        UiMode.NEEDS_PAIRING,
        UiMode.ERROR,
    ],
)
def test_printer_reachable_false_for_non_operational_modes(mode: UiMode) -> None:
    snap = _make_snap(mode=mode, printer_status_fresh=True)
    assert _printer_reachable(snap) is False


def test_printer_reachable_false_when_status_not_fresh() -> None:
    snap = _make_snap(mode=UiMode.READY, printer_status_fresh=False)
    assert _printer_reachable(snap) is False
