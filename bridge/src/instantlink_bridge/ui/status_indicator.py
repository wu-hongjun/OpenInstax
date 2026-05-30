"""Unified status indicator vocabulary.

Both surfaces — the LCD top bar and the future headless RGB LED — consume the
same six-state vocabulary so users learn one signal language across SKUs:

    Green  solid     Ready to print
    Green  breathing Printing (image received → print complete)
    Yellow solid     Not ready (printer not connected)
    Yellow breathing Searching for printer
    Red    solid     Error
    Red    breathing Non-fatal warning (e.g. no film)

The mapping is a pure function of the snapshot — no globals, no side effects —
so it is trivially testable. Surface-specific bindings (LCD tint, GPIO PWM)
read the resulting :class:`StatusState` and decide how to render it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from instantlink_bridge.ui.models import UiMode, UiSnapshot

__all__ = [
    "BREATH_AMPLITUDE",
    "BREATH_BASELINE",
    "BREATH_PERIOD_S",
    "GpioStatusSink",
    "NullStatusSink",
    "StatusPattern",
    "StatusSignal",
    "StatusSink",
    "StatusState",
    "breath_intensity",
    "derive_status",
]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class StatusSignal(StrEnum):
    """High-level health signal a user can recognise at a glance."""

    READY = "ready"
    PRINTING = "printing"
    NOT_READY = "not_ready"
    SEARCHING = "searching"
    ERROR = "error"
    WARNING = "warning"
    NEUTRAL = "neutral"  # Informational overlay (e.g. SETTINGS) — rendered blue


class StatusPattern(StrEnum):
    """How the signal is animated. SOLID = full intensity; BREATHING = pulse."""

    SOLID = "solid"
    BREATHING = "breathing"


# Base RGB triplets at full intensity. Kept here (not imported from render.py)
# so the GPIO driver can consume them without pulling in PIL.
_GREEN_RGB: tuple[int, int, int] = (0, 166, 118)  # #00a676
_YELLOW_RGB: tuple[int, int, int] = (242, 193, 78)  # #f2c14e
_RED_RGB: tuple[int, int, int] = (225, 85, 84)  # #e15554
_BLUE_RGB: tuple[int, int, int] = (10, 132, 255)  # #0A84FF (iOS systemBlue dark)


# Breath curve: 2 s cycle, intensity scaled 60 % → 100 %. Gentle enough to read
# as "alive" without distracting from the body content.
BREATH_PERIOD_S: float = 2.0
BREATH_BASELINE: float = 0.35
BREATH_AMPLITUDE: float = 0.65


def breath_intensity(t: float) -> float:
    """Return the breath envelope value in ``[BASELINE, 1.0]`` at time ``t``."""

    phase = (1.0 + math.sin(2.0 * math.pi * t / BREATH_PERIOD_S)) * 0.5
    return BREATH_BASELINE + BREATH_AMPLITUDE * phase


@dataclass(frozen=True, slots=True)
class StatusState:
    """Resolved status indicator state.

    ``signal`` and ``pattern`` are surface-agnostic; ``base_color`` is the
    fully-saturated RGB for rendering surfaces that care (LCD tint, GPIO LED).
    Use :meth:`tint_at` for the time-modulated colour and :meth:`foreground`
    for a legible text colour on top of the (peak) tint.
    """

    signal: StatusSignal
    pattern: StatusPattern
    base_color: tuple[int, int, int]

    def tint_at(self, t: float) -> tuple[int, int, int]:
        """Return RGB modulated by the breath envelope at time ``t``."""

        if self.pattern is StatusPattern.SOLID:
            return self.base_color
        scale = breath_intensity(t)
        r, g, b = self.base_color
        return (int(r * scale), int(g * scale), int(b * scale))

    def foreground(self) -> tuple[int, int, int]:
        """Return a legible text colour on top of the peak tint.

        Chosen against the *brightest* (full-saturation) background so the
        choice is stable across the breath cycle: the breath only dims the
        background, which can only improve legibility.
        """

        return _foreground_for(self.base_color)


# ---------------------------------------------------------------------------
# Mode → state mapping
# ---------------------------------------------------------------------------


_READY_SOLID = StatusState(StatusSignal.READY, StatusPattern.SOLID, _GREEN_RGB)
_PRINTING_BREATH = StatusState(StatusSignal.PRINTING, StatusPattern.BREATHING, _GREEN_RGB)
_NOT_READY_SOLID = StatusState(StatusSignal.NOT_READY, StatusPattern.SOLID, _YELLOW_RGB)
_SEARCHING_BREATH = StatusState(StatusSignal.SEARCHING, StatusPattern.BREATHING, _YELLOW_RGB)
_ERROR_SOLID = StatusState(StatusSignal.ERROR, StatusPattern.SOLID, _RED_RGB)
_WARNING_BREATH = StatusState(StatusSignal.WARNING, StatusPattern.BREATHING, _RED_RGB)
_SETTINGS_SOLID = StatusState(StatusSignal.NEUTRAL, StatusPattern.SOLID, _BLUE_RGB)


def derive_status(snapshot: UiSnapshot) -> StatusState:
    """Return the status indicator state for a snapshot.

    SETTINGS is treated as an overlay — the underlying bridge health is
    inferred from the non-mode fields (paired_printer, printer_status_fresh,
    film_remaining) so the bar still reflects reality while the user is
    configuring.
    """

    mode = snapshot.mode

    if mode is UiMode.SETTINGS:
        # SETTINGS is an informational overlay — always render the pill blue
        # (plan 034 item 1a). Yellow ≡ warning in the signal vocabulary, so
        # inheriting the underlying bridge health caused the pill to turn
        # yellow/red whenever the user opened settings from a non-ready state.
        return _SETTINGS_SOLID

    if mode is UiMode.READY:
        # Ready solid only when the readiness backing is fresh; otherwise the
        # user is effectively "waiting" (no film path or stale status), which
        # is a not-ready condition.
        if _can_accept(snapshot):
            return _READY_SOLID
        return _NOT_READY_SOLID

    if mode is UiMode.PRINT_COMPLETE:
        return _READY_SOLID

    if mode in {UiMode.IMAGE_RECEIVED, UiMode.AWAITING_CONFIRM, UiMode.PRINTING}:
        return _PRINTING_BREATH

    if mode is UiMode.NO_FILM:
        return _WARNING_BREATH

    if mode is UiMode.ERROR:
        return _ERROR_SOLID

    if mode in {UiMode.PRINTER_OFFLINE, UiMode.NEEDS_PAIRING, UiMode.PAIR_FAILED}:
        return _NOT_READY_SOLID

    if mode is UiMode.PRINTER_SEARCHING:
        # PRINTER_SEARCHING covers two distinct user-perceived states:
        #   * actively probing (BLE scan, connect, response wait) — breathing
        #     yellow with "Searching" matches the live work.
        #   * passively waiting on the user (no BLE signal returned, body says
        #     "Turn printer on") — solid yellow with "Disconnected" reads as a
        #     stable not-ready condition the user must act on. Breathing the
        #     bar here was misleading because the bridge isn't doing anything.
        if _is_waiting_for_user(snapshot.printer_status_message):
            return _NOT_READY_SOLID
        return _SEARCHING_BREATH

    if mode in {UiMode.BOOTING, UiMode.PAIRING, UiMode.VALIDATION}:
        return _SEARCHING_BREATH

    # Defensive default: an unknown mode is treated as "not ready" rather than
    # hiding the bar entirely — a solid yellow band asks the user to look.
    return _NOT_READY_SOLID


def _settings_inherit(snapshot: UiSnapshot) -> StatusState:
    """Infer the underlying health while the SETTINGS overlay is open."""

    if snapshot.paired_printer is None:
        return _NOT_READY_SOLID
    if snapshot.film_remaining == 0 and not snapshot.allow_print_without_film:
        return _WARNING_BREATH
    if snapshot.printer_status_fresh and _can_accept(snapshot):
        return _READY_SOLID
    return _SEARCHING_BREATH


# PRINTER_SEARCHING messages that mean the bridge has stopped finding any
# BLE advertisement and is now waiting on the user (the body line is "Turn
# printer on and keep awake"). These collapse the indicator to NOT_READY
# solid + "Disconnected" rather than the breathing "Searching" state used
# while the bridge is actively probing.
_WAITING_FOR_USER_MESSAGES: frozenset[str] = frozenset(
    {
        "No printer signal",
        "Scanning: 0 printers",
    }
)


def _is_waiting_for_user(message: str | None) -> bool:
    """Return True when PRINTER_SEARCHING is passively awaiting user action."""

    return message is not None and message in _WAITING_FOR_USER_MESSAGES


def _can_accept(snapshot: UiSnapshot) -> bool:
    """Mirror of ``render.can_accept_images`` without the circular import.

    The bar must agree with the READY body about whether prints can be sent:
    both an FTP receive path and a healthy printer with film are required.
    Kept in lockstep with ``render.printer_ready`` + ``render.camera_link_ready``.
    """

    if not snapshot.camera_receive_ready:
        return False
    if snapshot.paired_printer is None:
        return False
    if not snapshot.printer_status_fresh:
        return False
    if snapshot.film_remaining is None:
        return False
    if snapshot.film_remaining <= 0 and not snapshot.allow_print_without_film:
        return False
    return True


def _foreground_for(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pick black or white text based on background luminance.

    Uses the Rec. 709 luma coefficients on the linear-ish 0-255 channels. The
    threshold (160) was tuned by eyeballing the three palette tints — yellow
    falls above and ends up with black text, red and green fall below and use
    white. Re-tune if the palette changes.
    """

    r, g, b = rgb
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if luma > 160:
        return (0, 0, 0)
    return (255, 255, 255)


# ---------------------------------------------------------------------------
# Sinks (LCD is implicit via render.py; GPIO/null surface here)
# ---------------------------------------------------------------------------


class StatusSink(Protocol):
    """Receives the resolved :class:`StatusState` on every render tick.

    Implementations should be cheap and idempotent — they are called even
    when the state has not changed.
    """

    def set(self, state: StatusState) -> None:
        """Push the current state to the sink."""


class NullStatusSink:
    """Default sink that drops every state. Used when no surface is wired."""

    def set(self, state: StatusState) -> None:
        return None


class GpioStatusSink:
    """Placeholder GPIO RGB-LED sink.

    Phase 5 (headless SKU) wires this to ``gpiozero.RGBLED`` with PWM. Today
    it only logs state transitions so deploys with the config flag enabled
    surface in journalctl without pulling in hardware-only dependencies.
    """

    def __init__(self) -> None:
        self._last: StatusState | None = None

    def set(self, state: StatusState) -> None:
        if state == self._last:
            return
        self._last = state
        # Deferred: gpiozero.RGBLED + PWM duty modulation for the breath cycle.
        # Logging here is intentionally cheap; replace in Phase 5.
        import logging

        logging.getLogger(__name__).info(
            "status.sink_change signal=%s pattern=%s color=%s",
            state.signal.value,
            state.pattern.value,
            state.base_color,
        )
