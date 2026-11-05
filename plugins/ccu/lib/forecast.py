"""Pure forecast math for the ccu plugin.

Weights elapsed time by the configured active window (working hours minus
blackout days) rather than raw wall-clock, then projects weekly quota usage
forward to a finish percentage, a per-active-day burn-down rate, and a
projected exhaustion datetime. Everything here is deterministic given its
arguments (no clock reads, no I/O) so the math is fully testable against
injected `now`/`reset` values.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from config import Config

WEEK_SECONDS = 7 * 24 * 3600


@dataclass
class Forecast:
    projected_pct: float
    burn_down_pct_per_active_day: float
    exhaustion_dt: dt.datetime | None
    remaining_active_seconds: float
    frac_active_elapsed: float


def _in_blackout(day: dt.date, blackouts: list[tuple[dt.date, dt.date]]) -> bool:
    return any(lo <= day <= hi for lo, hi in blackouts)


def _active_day_seconds(cfg: Config) -> float:
    anchor = dt.date(2000, 1, 1)
    start = dt.datetime.combine(anchor, cfg.active_start)
    end = dt.datetime.combine(anchor, cfg.active_end)
    return max((end - start).total_seconds(), 0.0)


_UTC = dt.timezone.utc


def _span_seconds(lo: dt.datetime, hi: dt.datetime) -> float:
    """Real elapsed seconds between two aware datetimes.

    Converts to UTC before subtracting so a DST offset change inside ``[lo, hi)``
    is not dropped. Subtracting two datetimes that share the same ``tzinfo``
    object takes CPython's naive-comparison shortcut and silently ignores the
    offset change (over-counting a spring-forward day by an hour), which is why
    the endpoints are normalized to UTC here rather than subtracted directly.
    """
    return (hi.astimezone(_UTC) - lo.astimezone(_UTC)).total_seconds()


def active_seconds_between(start: dt.datetime, end: dt.datetime, cfg: Config) -> float:
    """Seconds in ``[start, end)`` inside the active window and outside blackouts.

    The active window is ``[active_start, active_end)`` on each calendar day in
    ``cfg.tz``; blackout days contribute zero.
    """
    if end <= start:
        return 0.0

    tz = cfg.tz
    start_l = start.astimezone(tz)
    end_l = end.astimezone(tz)

    total = 0.0
    day = start_l.date()
    last = end_l.date()
    while day <= last:
        if not _in_blackout(day, cfg.blackouts):
            win_start = dt.datetime.combine(day, cfg.active_start, tzinfo=tz)
            win_end = dt.datetime.combine(day, cfg.active_end, tzinfo=tz)
            lo = max(win_start, start_l)
            hi = min(win_end, end_l)
            if hi > lo:
                total += _span_seconds(lo, hi)
        day += dt.timedelta(days=1)
    return total


def _walk_active_forward(
    now: dt.datetime,
    reset_dt: dt.datetime,
    seconds_needed: float,
    cfg: Config,
) -> dt.datetime | None:
    """Datetime at which ``seconds_needed`` active seconds accumulate past ``now``.

    Returns ``None`` if the reset arrives before that many active seconds pass.
    """
    tz = cfg.tz
    now_l = now.astimezone(tz)
    reset_l = reset_dt.astimezone(tz)

    acc = 0.0
    day = now_l.date()
    last = reset_l.date()
    while day <= last:
        if not _in_blackout(day, cfg.blackouts):
            win_start = dt.datetime.combine(day, cfg.active_start, tzinfo=tz)
            win_end = dt.datetime.combine(day, cfg.active_end, tzinfo=tz)
            lo = max(win_start, now_l)
            hi = min(win_end, reset_l)
            if hi > lo:
                seg = _span_seconds(lo, hi)
                if acc + seg >= seconds_needed:
                    hit = lo.astimezone(_UTC) + dt.timedelta(
                        seconds=seconds_needed - acc
                    )
                    return hit.astimezone(tz)
                acc += seg
        day += dt.timedelta(days=1)
    return None


def forecast(
    used_pct: float,
    reset_dt: dt.datetime,
    now: dt.datetime,
    cfg: Config,
) -> Forecast:
    """Project weekly usage forward, weighting time by the active window."""
    week_start = reset_dt - dt.timedelta(seconds=WEEK_SECONDS)
    active_elapsed = active_seconds_between(week_start, now, cfg)
    active_remaining = active_seconds_between(now, reset_dt, cfg)
    active_total = active_elapsed + active_remaining

    frac = active_elapsed / active_total if active_total > 0 else 0.0
    if frac > 0:
        projected = used_pct / frac
    else:
        # No active time has elapsed yet: nothing has been spent to project
        # from. Zero usage projects to zero, not the vacuous "infinite pace".
        projected = float("inf") if used_pct > 0 else 0.0

    day_seconds = _active_day_seconds(cfg)
    elapsed_active_days = active_elapsed / day_seconds if day_seconds > 0 else 0.0
    burn_down = used_pct / elapsed_active_days if elapsed_active_days > 0 else 0.0

    exhaustion: dt.datetime | None = None
    if used_pct >= 100.0:
        exhaustion = now
    elif used_pct > 0 and active_elapsed > 0:
        burn_per_active_second = used_pct / active_elapsed
        seconds_needed = (100.0 - used_pct) / burn_per_active_second
        exhaustion = _walk_active_forward(now, reset_dt, seconds_needed, cfg)

    return Forecast(
        projected_pct=projected,
        burn_down_pct_per_active_day=burn_down,
        exhaustion_dt=exhaustion,
        remaining_active_seconds=active_remaining,
        frac_active_elapsed=frac,
    )
