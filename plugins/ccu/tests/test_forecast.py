"""Forecast-math tests for the ccu plugin."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from config import Config
from forecast import active_seconds_between, forecast

NY = ZoneInfo("America/New_York")


def _cfg(blackouts=None):
    return Config(
        tz=NY,
        active_start=dt.time(9, 0),
        active_end=dt.time(23, 0),
        blackouts=blackouts or [],
    )


def test_active_seconds_excludes_off_hours_and_blackouts():
    # 48h window spanning two active days; the middle day is blacked out, so
    # only one 14h active window (08-10) is counted, not two.
    start = dt.datetime(2026, 8, 10, 0, 0, tzinfo=NY)
    end = dt.datetime(2026, 8, 12, 0, 0, tzinfo=NY)
    cfg = _cfg(blackouts=[(dt.date(2026, 8, 11), dt.date(2026, 8, 11))])
    secs = active_seconds_between(start, end, cfg)
    assert secs == pytest.approx(14 * 3600)


def test_active_seconds_no_blackout_counts_both_days():
    start = dt.datetime(2026, 8, 10, 0, 0, tzinfo=NY)
    end = dt.datetime(2026, 8, 12, 0, 0, tzinfo=NY)
    secs = active_seconds_between(start, end, _cfg())
    assert secs == pytest.approx(2 * 14 * 3600)


def test_forecast_emits_exhaustion_and_burndown():
    # Week 08-10 00:00 .. 08-17 00:00 (EDT, no DST change). `now` is placed so
    # exactly half the week's active seconds (49h of 98h) have elapsed.
    reset_dt = dt.datetime(2026, 8, 17, 0, 0, tzinfo=NY)
    now = dt.datetime(2026, 8, 13, 16, 0, tzinfo=NY)  # 49h active elapsed
    cfg = _cfg()

    fc = forecast(used_pct=50.0, reset_dt=reset_dt, now=now, cfg=cfg)

    assert fc.projected_pct == pytest.approx(100.0, abs=0.1)
    assert fc.frac_active_elapsed == pytest.approx(0.5, abs=0.001)
    assert fc.burn_down_pct_per_active_day == pytest.approx(50.0 / 3.5, abs=0.1)
    assert fc.exhaustion_dt is not None
    assert now <= fc.exhaustion_dt <= reset_dt
    # Exhaustion lands inside (or at the edge of) an active window.
    local = fc.exhaustion_dt.astimezone(NY)
    assert cfg.active_start <= local.timetz().replace(tzinfo=None) <= cfg.active_end


def test_forecast_no_exhaustion_when_under_pace():
    # Very low usage with half the week elapsed never reaches 100% before reset.
    reset_dt = dt.datetime(2026, 8, 17, 0, 0, tzinfo=NY)
    now = dt.datetime(2026, 8, 13, 16, 0, tzinfo=NY)
    fc = forecast(used_pct=1.0, reset_dt=reset_dt, now=now, cfg=_cfg())
    assert fc.exhaustion_dt is None
    assert fc.projected_pct == pytest.approx(2.0, abs=0.1)


def test_active_seconds_not_double_counted_across_dst_spring_forward():
    # NY spring-forward 2027-03-14 skips 02:00->03:00, so a whole-day
    # [00:00, 23:59) window is 22h59m of real time, not 23h59m. A naive
    # same-tzinfo subtraction would wrongly report 23h59m.
    full_day = Config(tz=NY, active_start=dt.time(0, 0), active_end=dt.time(23, 59))
    start = dt.datetime(2027, 3, 14, 0, 0, tzinfo=NY)
    end = dt.datetime(2027, 3, 15, 0, 0, tzinfo=NY)
    secs = active_seconds_between(start, end, full_day)
    assert secs == pytest.approx(22 * 3600 + 59 * 60)  # 82740, not 86340


def test_forecast_zero_usage_projects_zero_not_inf():
    # At week start no active time has elapsed; 0% used must project to 0%,
    # not the vacuous inf that would print "Far over budget" at zero usage.
    reset_dt = dt.datetime(2026, 8, 17, 0, 0, tzinfo=NY)
    week_start = reset_dt - dt.timedelta(days=7)
    fc = forecast(used_pct=0.0, reset_dt=reset_dt, now=week_start, cfg=_cfg())
    assert fc.projected_pct == pytest.approx(0.0)
    assert fc.exhaustion_dt is None
