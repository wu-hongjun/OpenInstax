"""Tests for the unified status indicator vocabulary."""

from __future__ import annotations

import logging

import pytest

from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.ui.models import PairedPrinter, UiMode, UiSnapshot
from instantlink_bridge.ui.status_indicator import (
    BREATH_AMPLITUDE,
    BREATH_BASELINE,
    BREATH_PERIOD_S,
    GpioStatusSink,
    NullStatusSink,
    StatusPattern,
    StatusSignal,
    StatusState,
    breath_intensity,
    derive_status,
)


def _ready_snapshot(**overrides: object) -> UiSnapshot:
    """Build a fully-ready snapshot — bar should show GREEN solid."""

    base: dict[str, object] = dict(
        mode=UiMode.READY,
        ftp_host="192.168.7.1",
        camera_receive_ready=True,
        paired_printer=PairedPrinter(
            address="AA:BB:CC:DD:EE:FF",
            name="INSTAX-12345678",
            model=PrinterModel.SQUARE,
        ),
        printer_status_fresh=True,
        printer_model=PrinterModel.SQUARE,
        film_remaining=7,
    )
    base.update(overrides)
    return UiSnapshot(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Mode → signal mapping
# ---------------------------------------------------------------------------


def test_ready_with_fresh_status_and_film_resolves_to_ready_solid() -> None:
    state = derive_status(_ready_snapshot())

    assert state.signal is StatusSignal.READY
    assert state.pattern is StatusPattern.SOLID


def test_ready_without_film_downgrades_to_not_ready_solid() -> None:
    # Film exhausted with no test-mode override — the body downgrades to
    # "Waiting", so the bar must do the same instead of lying as green.
    state = derive_status(_ready_snapshot(film_remaining=0))

    assert state.signal is StatusSignal.NOT_READY
    assert state.pattern is StatusPattern.SOLID


def test_ready_without_ftp_path_downgrades_to_not_ready_solid() -> None:
    state = derive_status(_ready_snapshot(camera_receive_ready=False))

    assert state.signal is StatusSignal.NOT_READY


def test_print_flow_modes_resolve_to_printing_breathing() -> None:
    for mode in (UiMode.IMAGE_RECEIVED, UiMode.AWAITING_CONFIRM, UiMode.PRINTING):
        state = derive_status(_ready_snapshot(mode=mode))

        assert state.signal is StatusSignal.PRINTING, mode
        assert state.pattern is StatusPattern.BREATHING, mode


def test_print_complete_resolves_to_ready_solid() -> None:
    state = derive_status(_ready_snapshot(mode=UiMode.PRINT_COMPLETE))

    assert state.signal is StatusSignal.READY
    assert state.pattern is StatusPattern.SOLID


def test_no_film_resolves_to_warning_breathing() -> None:
    state = derive_status(
        _ready_snapshot(mode=UiMode.NO_FILM, film_remaining=0)
    )

    assert state.signal is StatusSignal.WARNING
    assert state.pattern is StatusPattern.BREATHING


def test_error_resolves_to_error_solid() -> None:
    state = derive_status(_ready_snapshot(mode=UiMode.ERROR))

    assert state.signal is StatusSignal.ERROR
    assert state.pattern is StatusPattern.SOLID


def test_not_ready_modes_resolve_to_not_ready_solid() -> None:
    for mode in (UiMode.PRINTER_OFFLINE, UiMode.NEEDS_PAIRING, UiMode.PAIR_FAILED):
        state = derive_status(_ready_snapshot(mode=mode))

        assert state.signal is StatusSignal.NOT_READY, mode
        assert state.pattern is StatusPattern.SOLID, mode


def test_searching_modes_resolve_to_searching_breathing() -> None:
    for mode in (
        UiMode.BOOTING,
        UiMode.PRINTER_SEARCHING,
        UiMode.PAIRING,
        UiMode.VALIDATION,
    ):
        state = derive_status(_ready_snapshot(mode=mode))

        assert state.signal is StatusSignal.SEARCHING, mode
        assert state.pattern is StatusPattern.BREATHING, mode


def test_printer_searching_with_no_signal_collapses_to_not_ready_solid() -> None:
    """When the scan keeps returning zero BLE hits we're not really
    searching — we're waiting on the user to power the printer on. The
    indicator switches to solid yellow so the bar doesn't breathe over a
    passive screen ("Turn printer on").
    """

    for message in ("No printer signal", "Scanning: 0 printers"):
        state = derive_status(
            _ready_snapshot(
                mode=UiMode.PRINTER_SEARCHING,
                printer_status_message=message,
            )
        )

        assert state.signal is StatusSignal.NOT_READY, message
        assert state.pattern is StatusPattern.SOLID, message


def test_printer_searching_while_probing_stays_breathing() -> None:
    """An active probe ("Looking for printer", "Printer seen; connecting")
    keeps the breathing-yellow indicator because work is in progress."""

    for message in ("Looking for printer", "Printer seen; connecting"):
        state = derive_status(
            _ready_snapshot(
                mode=UiMode.PRINTER_SEARCHING,
                printer_status_message=message,
            )
        )

        assert state.signal is StatusSignal.SEARCHING, message
        assert state.pattern is StatusPattern.BREATHING, message


# ---------------------------------------------------------------------------
# SETTINGS overlay — dot inherits underlying device health
# ---------------------------------------------------------------------------
#
# Going into Settings collapses the status pill into a circle, but the
# circle keeps reporting the device's actual health (green if ready,
# yellow if searching, red if error). The earlier NEUTRAL/blue routing
# was abandoned per user feedback — a blue dot in Settings doesn't tell
# the user whether their printer is OK, and that information matters
# while they're configuring.


def test_settings_inherits_ready_when_device_healthy() -> None:
    """A healthy bridge stays green in the Settings dot."""

    state = derive_status(_ready_snapshot(mode=UiMode.SETTINGS))
    assert state.signal is StatusSignal.READY
    assert state.pattern is StatusPattern.SOLID


def test_settings_inherits_not_ready_when_unpaired() -> None:
    """No printer paired → not-ready (yellow solid) even inside Settings."""

    state = derive_status(_ready_snapshot(mode=UiMode.SETTINGS, paired_printer=None))
    assert state.signal is StatusSignal.NOT_READY


def test_settings_inherits_warning_when_no_film() -> None:
    """Film exhausted while not in test mode → warning red breathing."""

    state = derive_status(_ready_snapshot(mode=UiMode.SETTINGS, film_remaining=0))
    assert state.signal is StatusSignal.WARNING
    assert state.pattern is StatusPattern.BREATHING


def test_settings_inherits_searching_when_status_stale() -> None:
    """Status hasn't refreshed → searching yellow breathing in Settings too."""

    state = derive_status(
        _ready_snapshot(mode=UiMode.SETTINGS, printer_status_fresh=False)
    )
    assert state.signal is StatusSignal.SEARCHING


# ---------------------------------------------------------------------------
# Breath envelope + tint modulation
# ---------------------------------------------------------------------------


def test_breath_intensity_stays_in_bounds_across_one_period() -> None:
    samples = [breath_intensity(t * BREATH_PERIOD_S / 64) for t in range(65)]

    assert min(samples) == pytest.approx(BREATH_BASELINE, rel=1e-3)
    assert max(samples) == pytest.approx(BREATH_BASELINE + BREATH_AMPLITUDE, rel=1e-3)


def test_solid_tint_ignores_time() -> None:
    state = derive_status(_ready_snapshot())

    assert state.tint_at(0.0) == state.tint_at(1.7)
    assert state.tint_at(0.0) == state.base_color


def test_breathing_tint_modulates_with_time() -> None:
    state = derive_status(_ready_snapshot(mode=UiMode.PRINTING))

    # Trough at the breath envelope's minimum.
    trough = state.tint_at(1.5 * BREATH_PERIOD_S / 2)  # phase ≈ 3π/2 → -1
    peak = state.tint_at(0.5 * BREATH_PERIOD_S / 2)  # phase ≈ π/2 → +1

    assert max(peak) > max(trough)
    for channel in trough:
        assert channel <= max(state.base_color)


# ---------------------------------------------------------------------------
# Foreground luma picking
# ---------------------------------------------------------------------------


def test_foreground_is_white_on_green_and_red() -> None:
    green_state = StatusState(StatusSignal.READY, StatusPattern.SOLID, (0, 166, 118))
    red_state = StatusState(StatusSignal.ERROR, StatusPattern.SOLID, (225, 85, 84))

    assert green_state.foreground() == (255, 255, 255)
    assert red_state.foreground() == (255, 255, 255)


def test_foreground_is_black_on_yellow() -> None:
    yellow_state = StatusState(
        StatusSignal.NOT_READY, StatusPattern.SOLID, (242, 193, 78)
    )

    assert yellow_state.foreground() == (0, 0, 0)


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


def test_null_status_sink_accepts_states_silently() -> None:
    sink = NullStatusSink()

    sink.set(derive_status(_ready_snapshot()))


def test_gpio_status_sink_only_logs_on_state_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sink = GpioStatusSink()
    state = derive_status(_ready_snapshot())

    with caplog.at_level(logging.INFO, logger="instantlink_bridge.ui.status_indicator"):
        sink.set(state)
        sink.set(state)  # second call is a duplicate; must NOT log again

    transitions = [
        record for record in caplog.records if record.message.startswith("status.sink_change")
    ]
    assert len(transitions) == 1
