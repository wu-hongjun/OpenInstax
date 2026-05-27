"""Unit tests for the printer battery-life estimator."""

from __future__ import annotations

from instantlink_bridge.power.battery_estimator import (
    BatteryEstimateState,
    BatteryLifeEstimator,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _feed_steady_drain(
    estimator: BatteryLifeEstimator,
    clock: _FakeClock,
    *,
    start_battery: int,
    drain_per_hour: float,
    step_s: float,
    steps: int,
) -> None:
    """Feed samples that discharge at a fixed rate, one keepalive tick apart."""

    for index in range(steps):
        elapsed_hours = (index * step_s) / 3600.0
        battery = round(start_battery - drain_per_hour * elapsed_hours)
        estimator.add_sample(battery, is_charging=False, timestamp=clock.now)
        clock.advance(step_s)


def test_insufficient_samples_returns_none() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(clock=clock, min_samples=3, min_window_s=90.0)

    first = estimator.add_sample(80, is_charging=False)
    clock.advance(10.0)
    second = estimator.add_sample(79, is_charging=False)

    assert first.state is BatteryEstimateState.ESTIMATING
    assert first.minutes_remaining is None
    assert second.state is BatteryEstimateState.ESTIMATING
    assert second.minutes_remaining is None


def test_window_too_short_still_estimating() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(clock=clock, min_samples=3, min_window_s=120.0)

    # Three samples but only 20 s of span: below min_window_s, so no fit yet.
    for battery in (90, 89, 88):
        estimator.add_sample(battery, is_charging=False, timestamp=clock.now)
        clock.advance(10.0)
    result = estimator.add_sample(88, is_charging=False, timestamp=clock.now)

    assert result.state is BatteryEstimateState.ESTIMATING
    assert result.minutes_remaining is None


def test_steady_drain_produces_sane_estimate() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(clock=clock, min_samples=3, min_window_s=90.0)

    # 50% battery draining at 10%/hour -> 5 hours -> 300 minutes remaining.
    _feed_steady_drain(
        estimator,
        clock,
        start_battery=50,
        drain_per_hour=10.0,
        step_s=60.0,
        steps=20,
    )
    final = estimator.add_sample(46, is_charging=False, timestamp=clock.now)

    assert final.state is BatteryEstimateState.DISCHARGING
    assert final.drain_percent_per_hour is not None
    assert 9.0 <= final.drain_percent_per_hour <= 11.0
    assert final.minutes_remaining is not None
    # ~46% / 10%/h ~= 276 min; allow generous slack for rounding/smoothing.
    assert 230 <= final.minutes_remaining <= 320


def test_charging_never_reports_drain_estimate() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(clock=clock, min_samples=3, min_window_s=90.0)

    result = estimator.add_sample(40, is_charging=True, timestamp=clock.now)

    assert result.state is BatteryEstimateState.CHARGING
    assert result.is_charging is True
    assert result.minutes_remaining is None
    assert result.drain_percent_per_hour is None


def test_charge_resets_discharge_history() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(clock=clock, min_samples=3, min_window_s=90.0)

    _feed_steady_drain(
        estimator,
        clock,
        start_battery=50,
        drain_per_hour=10.0,
        step_s=60.0,
        steps=10,
    )
    # A charge cycle wipes the pre-charge trend.
    estimator.add_sample(70, is_charging=True, timestamp=clock.now)
    clock.advance(60.0)

    # Post-charge discharge: only two fresh samples, so no estimate leaks from before.
    estimator.add_sample(70, is_charging=False, timestamp=clock.now)
    clock.advance(60.0)
    after = estimator.add_sample(69, is_charging=False, timestamp=clock.now)

    assert after.state is BatteryEstimateState.ESTIMATING
    assert after.minutes_remaining is None


def test_recharge_without_charging_flag_resets_window() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(
        clock=clock,
        min_samples=3,
        min_window_s=90.0,
        recharge_tolerance_pct=1,
    )

    _feed_steady_drain(
        estimator,
        clock,
        start_battery=50,
        drain_per_hour=10.0,
        step_s=60.0,
        steps=20,
    )
    rising = estimator.add_sample(80, is_charging=False, timestamp=clock.now)

    # Battery jumped up well past the tolerance: history is discarded, so we are estimating again.
    assert rising.state is BatteryEstimateState.ESTIMATING
    assert rising.minutes_remaining is None


def test_noisy_samples_are_smoothed() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(
        clock=clock,
        min_samples=3,
        min_window_s=90.0,
        smoothing_alpha=0.3,
    )

    # Establish a steady 10%/hour trend first.
    _feed_steady_drain(
        estimator,
        clock,
        start_battery=50,
        drain_per_hour=10.0,
        step_s=60.0,
        steps=20,
    )
    steady = estimator.add_sample(46, is_charging=False, timestamp=clock.now)
    clock.advance(60.0)
    # Inject one noisy dip; the smoothed minutes value must not collapse toward the spike.
    noisy = estimator.add_sample(30, is_charging=False, timestamp=clock.now)

    assert steady.minutes_remaining is not None
    assert noisy.minutes_remaining is not None
    # A raw fit would swing hard on the spike; smoothing keeps the change bounded.
    assert abs(noisy.minutes_remaining - steady.minutes_remaining) < steady.minutes_remaining


def test_flat_battery_is_not_meaningfully_discharging() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(
        clock=clock,
        min_samples=3,
        min_window_s=90.0,
        min_drain_percent_per_hour=0.5,
    )

    for _ in range(10):
        estimator.add_sample(60, is_charging=False, timestamp=clock.now)
        clock.advance(60.0)
    flat = estimator.add_sample(60, is_charging=False, timestamp=clock.now)

    assert flat.state is BatteryEstimateState.ESTIMATING
    assert flat.minutes_remaining is None


def test_reset_clears_state() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(clock=clock, min_samples=3, min_window_s=90.0)

    _feed_steady_drain(
        estimator,
        clock,
        start_battery=50,
        drain_per_hour=10.0,
        step_s=60.0,
        steps=20,
    )
    estimator.reset()
    clock.advance(60.0)
    after = estimator.add_sample(40, is_charging=False, timestamp=clock.now)

    assert after.state is BatteryEstimateState.ESTIMATING
    assert after.minutes_remaining is None


def test_battery_percent_is_bounded() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(clock=clock, min_samples=2, min_window_s=10.0)

    estimator.add_sample(150, is_charging=False, timestamp=clock.now)
    clock.advance(60.0)
    result = estimator.add_sample(-20, is_charging=False, timestamp=clock.now)

    # Clamped to 0..100; a 100 -> 0 fall is a steep but finite drain, never a negative estimate.
    assert result.minutes_remaining is None or result.minutes_remaining >= 0


def test_uses_injected_clock_by_default() -> None:
    clock = _FakeClock()
    estimator = BatteryLifeEstimator(clock=clock, min_samples=3, min_window_s=90.0)

    estimator.add_sample(50, is_charging=False)
    clock.advance(60.0)
    estimator.add_sample(49, is_charging=False)
    clock.advance(60.0)
    estimator.add_sample(48, is_charging=False)
    clock.advance(60.0)
    result = estimator.add_sample(47, is_charging=False)

    assert result.state is BatteryEstimateState.DISCHARGING
    assert result.minutes_remaining is not None
