"""Battery-life estimation for a connected Instax printer.

The bridge polls printer status (battery percent + charging flag) every
``printer.keepalive_interval_s`` seconds while a printer is connected. This module turns that
stream of ``(monotonic_timestamp, battery_percent, is_charging)`` samples into a smoothed
minutes-remaining estimate while the printer is discharging.

Design goals:

- Pure and deterministic: the clock is injected and all state lives on the instance, so the
  estimator is fully unit-testable by feeding synthetic samples.
- Robust to noise: a single noisy battery reading should not produce a wild estimate. The drain
  rate is fit with least-squares over a sliding time window, and the reported minutes value is
  exponentially smoothed.
- Charge-aware: while charging we never report a drain estimate, and the discharge history is
  reset so a post-charge estimate is not polluted by pre-charge samples. A battery that climbs
  (a recharge that the printer never flagged as charging) also resets the discharge window.
- Graceful when data is thin: until there are enough samples spanning a meaningful window the
  estimator returns ``None`` so callers can show "estimating...".
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

# Minimum number of samples before any drain fit is attempted.
DEFAULT_MIN_SAMPLES = 3
# Minimum elapsed time (seconds) the retained samples must span before a fit is trusted; a few
# samples bunched into one keepalive tick are not enough to extrapolate a battery life from.
DEFAULT_MIN_WINDOW_S = 90.0
# Samples older than this (seconds) are dropped so the fit tracks recent drain, not the whole
# session. Sized to hold several keepalive ticks (default 10 s) worth of history.
DEFAULT_WINDOW_S = 1800.0
# A battery reading that climbs by more than this many percent over the retained window is treated
# as a recharge and resets the discharge history. One percent of slop absorbs gauge jitter.
DEFAULT_RECHARGE_TOLERANCE_PCT = 1
# Exponential smoothing factor for the reported minutes value (0 < alpha <= 1). Lower is smoother.
DEFAULT_SMOOTHING_ALPHA = 0.4
# Drain rates at or below this (percent/hour) are treated as "not meaningfully discharging" so we
# do not divide by ~0 and report an absurd multi-day estimate.
DEFAULT_MIN_DRAIN_PCT_PER_HOUR = 0.5


class BatteryEstimateState(StrEnum):
    """Why an estimate is or is not available."""

    DISCHARGING = "discharging"
    CHARGING = "charging"
    ESTIMATING = "estimating"


@dataclass(frozen=True, slots=True)
class BatteryEstimate:
    """Result of feeding one battery sample to the estimator."""

    state: BatteryEstimateState
    minutes_remaining: int | None
    drain_percent_per_hour: float | None

    @property
    def is_charging(self) -> bool:
        """Return whether the printer was charging on the latest sample."""

        return self.state is BatteryEstimateState.CHARGING


@dataclass(frozen=True, slots=True)
class _Sample:
    timestamp: float
    battery: int


class BatteryLifeEstimator:
    """Estimate minutes of battery life remaining from a stream of status samples."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        min_window_s: float = DEFAULT_MIN_WINDOW_S,
        window_s: float = DEFAULT_WINDOW_S,
        recharge_tolerance_pct: int = DEFAULT_RECHARGE_TOLERANCE_PCT,
        smoothing_alpha: float = DEFAULT_SMOOTHING_ALPHA,
        min_drain_percent_per_hour: float = DEFAULT_MIN_DRAIN_PCT_PER_HOUR,
    ) -> None:
        if min_samples < 2:
            raise ValueError("min_samples must be at least 2")
        if min_window_s <= 0 or window_s <= 0:
            raise ValueError("window durations must be positive")
        if window_s < min_window_s:
            raise ValueError("window_s must be >= min_window_s")
        if not 0.0 < smoothing_alpha <= 1.0:
            raise ValueError("smoothing_alpha must be in (0, 1]")
        if min_drain_percent_per_hour <= 0:
            raise ValueError("min_drain_percent_per_hour must be positive")
        self._clock = clock
        self._min_samples = min_samples
        self._min_window_s = min_window_s
        self._window_s = window_s
        self._recharge_tolerance_pct = recharge_tolerance_pct
        self._smoothing_alpha = smoothing_alpha
        self._min_drain_percent_per_hour = min_drain_percent_per_hour
        self._samples: deque[_Sample] = deque()
        self._smoothed_minutes: float | None = None

    def reset(self) -> None:
        """Drop all retained history (e.g. on disconnect or a fresh charge cycle)."""

        self._samples.clear()
        self._smoothed_minutes = None

    def add_sample(
        self,
        battery_percent: int,
        *,
        is_charging: bool,
        timestamp: float | None = None,
    ) -> BatteryEstimate:
        """Record one status reading and return the current estimate.

        ``timestamp`` defaults to the injected clock so production callers need not pass it.
        """

        now = self._clock() if timestamp is None else timestamp
        bounded_battery = max(0, min(100, battery_percent))

        if is_charging:
            # Charging invalidates any discharge trend; clear so the next discharge starts clean.
            self.reset()
            return BatteryEstimate(
                state=BatteryEstimateState.CHARGING,
                minutes_remaining=None,
                drain_percent_per_hour=None,
            )

        self._discard_recharge(bounded_battery)
        self._samples.append(_Sample(timestamp=now, battery=bounded_battery))
        self._evict_old(now)

        drain = self._drain_percent_per_hour()
        if drain is None or drain < self._min_drain_percent_per_hour:
            return BatteryEstimate(
                state=BatteryEstimateState.ESTIMATING,
                minutes_remaining=None,
                drain_percent_per_hour=drain,
            )

        raw_minutes = bounded_battery / drain * 60.0
        self._smoothed_minutes = self._smooth(raw_minutes)
        return BatteryEstimate(
            state=BatteryEstimateState.DISCHARGING,
            minutes_remaining=max(0, round(self._smoothed_minutes)),
            drain_percent_per_hour=round(drain, 2),
        )

    def _discard_recharge(self, battery_percent: int) -> None:
        """Reset the window if the battery has climbed beyond gauge slop (an untagged recharge)."""

        if not self._samples:
            return
        lowest = min(sample.battery for sample in self._samples)
        if battery_percent > lowest + self._recharge_tolerance_pct:
            self.reset()

    def _evict_old(self, now: float) -> None:
        cutoff = now - self._window_s
        while len(self._samples) > self._min_samples and self._samples[0].timestamp < cutoff:
            self._samples.popleft()

    def _drain_percent_per_hour(self) -> float | None:
        """Return the discharge rate (percent/hour) via least-squares, or ``None`` if unready."""

        if len(self._samples) < self._min_samples:
            return None
        span_s = self._samples[-1].timestamp - self._samples[0].timestamp
        if span_s < self._min_window_s:
            return None

        # Least-squares slope of battery-vs-time. Time is recentred to the first sample to keep the
        # arithmetic well-conditioned. ``slope`` is percent-per-second; a discharging battery has a
        # negative slope, so drain (a positive percent/hour rate) is ``-slope * 3600``.
        base = self._samples[0].timestamp
        xs = [sample.timestamp - base for sample in self._samples]
        ys = [float(sample.battery) for sample in self._samples]
        n = float(len(xs))
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom == 0:
            return None
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denom
        drain_per_hour = -slope * 3600.0
        if drain_per_hour <= 0:
            # Flat or rising trend: not discharging in any usable sense.
            return 0.0
        return drain_per_hour

    def _smooth(self, raw_minutes: float) -> float:
        if self._smoothed_minutes is None:
            return raw_minutes
        alpha = self._smoothing_alpha
        return alpha * raw_minutes + (1.0 - alpha) * self._smoothed_minutes
