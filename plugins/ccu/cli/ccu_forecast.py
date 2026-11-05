#!/usr/bin/env python3
"""ccu weekly-usage forecast CLI.

Projects the weekly Claude quota finish percentage from current usage and the
next reset time, weighting elapsed time by a configured active window (working
hours minus blackout days) rather than raw wall-clock. Emits a projected finish
percentage, a per-active-day burn-down rate, and a projected exhaustion date.

Usage sources, in order: explicit --used/--reset > Anthropic usage API
(direct-TUI path) > ccstatusline cache (--prefer-cache or API-unavailable
fallback).
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

# Direct-execution bootstrap: put the sibling lib/ on sys.path so `import
# config` etc. resolve when this file is run as a script (tests get the path
# from conftest.py instead).
_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from config import (  # noqa: E402
    ConfigError,
    default_config,
    default_config_path,
    ensure_config_dir,
    load_config,
)
from forecast import forecast  # noqa: E402
from usage_source import CACHE_FILE, Usage, resolve_source  # noqa: E402

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_DISPLAY_RE = re.compile(
    r"(?:Resets\s+)?(\w{3})\w*\s+(\d{1,2})\s+at\s+"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^)]+)\)",
    re.IGNORECASE,
)


def parse_reset(s: str) -> dt.datetime:
    """Parse 'Resets Jun 16 at 1:59am (America/New_York)' or ISO 8601."""
    s = s.strip()
    m = _DISPLAY_RE.match(s)
    if m:
        month_str, day, hour, minute, ampm, tz_name = m.groups()
        minute = minute or "0"
        month = _MONTHS.get(month_str.lower()[:3])
        if month is None:
            raise ValueError(f"Unknown month {month_str!r}.")
        hour = int(hour)
        if ampm.lower() == "pm" and hour != 12:
            hour += 12
        elif ampm.lower() == "am" and hour == 12:
            hour = 0
        try:
            tz = ZoneInfo(tz_name.strip())
        except Exception as e:
            raise ValueError(
                f"Unknown timezone {tz_name!r}; use an IANA name like "
                "'America/New_York'."
            ) from e
        now = dt.datetime.now(tz)
        candidate = dt.datetime(now.year, month, int(day), hour, int(minute), tzinfo=tz)
        if candidate < now:  # year is implicit; roll forward if already past
            candidate = candidate.replace(year=now.year + 1)
        return candidate

    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(
            f"Could not parse {s!r}. Expected 'Resets Jun 23 at 2am "
            "(America/New_York)' or ISO 8601 like "
            f"'2026-06-16T01:59:00-04:00'. ({e})"
        ) from e
    if parsed.tzinfo is None:
        raise ValueError(
            f"Reset time {s!r} has no timezone. Add an offset or use IANA form."
        )
    return parsed


def assess(projected_pct: float) -> str:
    if projected_pct <= 90:
        return f"On pace to finish ~{projected_pct:.1f}%. Comfortable."
    if projected_pct <= 105:
        return f"On pace ~{projected_pct:.1f}%. Right at the line; ease off late-week."
    if projected_pct <= 120:
        return f"On pace ~{projected_pct:.1f}%. Over budget; throttle usage."
    if projected_pct <= 140:
        return (
            f"On pace ~{projected_pct:.1f}%. Well over; significant throttling needed."
        )
    return f"On pace ~{projected_pct:.1f}%. Far over budget; cut back hard now."


def _resolve_usage(args) -> tuple[float, str]:
    if args.used is not None and args.reset is not None:
        return float(args.used), args.reset
    source = resolve_source(prefer_api=not args.prefer_cache, cache_file=CACHE_FILE)
    usage: Usage = source.read()
    used = float(args.used) if args.used is not None else usage.used_pct
    reset = args.reset if args.reset is not None else usage.reset_at_iso
    return used, reset


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default=None, help="Path to ccu YAML config.")
    p.add_argument(
        "--used",
        type=float,
        default=None,
        help="Current weekly %% used (e.g. 56.0). Default: from usage source.",
    )
    p.add_argument(
        "--reset",
        default=None,
        help="Reset time (ISO 8601 or 'Resets Jun 30 at 2am (America/New_York)').",
    )
    p.add_argument(
        "--prefer-cache",
        action="store_true",
        help="Source usage from the ccstatusline cache instead of the API.",
    )
    args = p.parse_args(argv)

    if args.used is not None and args.used < 0:
        print(f"Error: --used must be non-negative, got {args.used}.", file=sys.stderr)
        return 1

    # Resolve the config path: explicit --config wins; otherwise scaffold the
    # default .sct config dir and read ccu.yaml from it if present. A missing
    # default file is a soft miss (built-in defaults); a malformed one at either
    # path fails loud.
    if args.config is not None:
        cfg_path = Path(args.config)
    else:
        try:
            ensure_config_dir()
        except OSError as e:
            print(
                f"Error: could not create the ccu config dir ({e}). Fix "
                "permissions on your .claude directory, or pass --config.",
                file=sys.stderr,
            )
            return 1
        default_path = default_config_path()
        cfg_path = default_path if default_path.exists() else None

    if cfg_path is not None:
        try:
            cfg = load_config(cfg_path)
        except ConfigError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    else:
        cfg = default_config()

    try:
        used, reset_str = _resolve_usage(args)
    except (FileNotFoundError, OSError, ValueError) as e:
        print(
            f"Error: could not resolve usage ({e}). Pass --used and --reset, "
            "or --prefer-cache with a populated ccstatusline cache.",
            file=sys.stderr,
        )
        return 1

    try:
        reset_dt = parse_reset(reset_str)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    now = dt.datetime.now(dt.timezone.utc)
    if reset_dt <= now:
        print(
            f"Error: reset time {reset_dt.isoformat()} is in the past. The "
            "cache may be stale; refresh it or pass --reset.",
            file=sys.stderr,
        )
        return 1

    fc = forecast(used_pct=used, reset_dt=reset_dt, now=now, cfg=cfg)

    local_now = now.astimezone()
    local_reset = reset_dt.astimezone()
    print(f"Run at:    {local_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Resets at: {local_reset.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Current weekly usage: {used:.1f}%")
    projected = (
        "inf" if fc.projected_pct == float("inf") else f"{fc.projected_pct:.1f}%"
    )
    print(f"Projected finish: {projected}")
    print(f"Burn-down: {fc.burn_down_pct_per_active_day:.2f}%/active-day")
    if fc.exhaustion_dt is not None:
        local_exh = fc.exhaustion_dt.astimezone()
        print(f"Exhaustion: {local_exh.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    else:
        print("Exhaustion: not before reset at current pace")
    print()
    print(assess(fc.projected_pct))
    return 0


if __name__ == "__main__":
    sys.exit(main())
