"""Usage-source seam for the ccu plugin.

ccu reads weekly usage from local files only; it reads no credentials and
makes no network call. Sources, in order: the statusline tap file that
``cli/ccu_tap.py`` records from Claude Code's ``rate_limits``, then
ccstatusline's newest ``usage*.json`` cache. Both yield the same `Usage` shape
so the CLI is source-agnostic.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from config import default_tap_file

# ccstatusline populates a usage cache here on each render (short TTL). Newer
# ccstatusline writes usage-<hash>.json; older builds wrote usage.json.
CACHE_DIR = Path.home() / ".cache" / "ccstatusline"
LEGACY_CACHE_NAME = "usage.json"
MAX_CACHE_AGE_SECONDS = 15 * 60


@dataclass
class Usage:
    used_pct: float
    reset_at_iso: str


class UsageSource(Protocol):
    def read(self) -> Usage: ...


def _normalize_reset(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError("Usage payload missing a string 'weeklyResetAt'.")
    return raw[:-1] + "+00:00" if raw.endswith("Z") else raw


def _coerce_used(raw: object, field: str = "weeklyUsage") -> float:
    # bool is an int subclass; reject it so `true` is not coerced to 1.0.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"Usage payload missing a numeric '{field}'.")  # noqa: TRY004
    val = float(raw)
    if val < 0:
        raise ValueError(f"Usage '{field}' must be non-negative, got {val}.")
    return val


def _coerce_percentage(raw: object, field: str) -> float:
    """``_coerce_used`` plus the 0-100 bound; rejects inf and NaN too."""
    val = _coerce_used(raw, field)
    if not math.isfinite(val) or val > 100:
        raise ValueError(
            f"Usage '{field}' must be a finite percentage from 0 to 100, got {val}."
        )
    return val


def _require_mapping(data: object, source: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError(  # noqa: TRY004
            f"{source} payload must be a JSON object, got {type(data).__name__}."
        )
    return data


def _warn_if_stale(path: Path, mtime: float, label: str) -> None:
    age = time.time() - mtime
    if age > MAX_CACHE_AGE_SECONDS:
        print(
            f"Warning: {label} is {int(age // 60)} min old ({path}); numbers "
            "may be stale. Open Claude Code to refresh, or pass values manually.",
            file=sys.stderr,
        )


def usage_from_seven_day(week: dict) -> Usage:
    """Convert a statusline ``seven_day`` window to `Usage`.

    ``used_percentage`` must be a finite number from 0 to 100 and ``resets_at``
    a number of epoch seconds. Raises ValueError otherwise.
    """
    resets_at = week.get("resets_at")
    if isinstance(resets_at, bool) or not isinstance(resets_at, (int, float)):
        raise ValueError("Usage payload missing a numeric 'resets_at'.")  # noqa: TRY004
    try:
        reset = dt.datetime.fromtimestamp(resets_at, dt.timezone.utc)
    except (OverflowError, OSError) as e:
        raise ValueError(f"'resets_at' {resets_at} is out of range.") from e
    return Usage(
        used_pct=_coerce_percentage(week.get("used_percentage"), "used_percentage"),
        reset_at_iso=reset.isoformat(),
    )


class TapFileSource:
    """Primary usage source: the file ``ccu_tap.py`` records.

    The tap writes it from the latest render that carries a valid ``seven_day``
    window; renders without one leave the previous file and its mtime
    unchanged.

    Shape: ``{"captured_at": <epoch>, "rate_limits": {"seven_day":
    {"used_percentage": 0-100, "resets_at": <epoch seconds>}, ...}}``. Raises
    FileNotFoundError when absent and ValueError naming the file when malformed.
    """

    def __init__(self, tap_file: Path):
        self._tap_file = Path(tap_file)

    def read(self) -> Usage:
        mtime = self._tap_file.stat().st_mtime  # FileNotFoundError if absent
        try:
            data = _require_mapping(
                json.loads(self._tap_file.read_text(encoding="utf-8")), "ccu tap"
            )
            rate_limits = _require_mapping(
                data.get("rate_limits"), "ccu tap rate_limits"
            )
            week = _require_mapping(rate_limits.get("seven_day"), "ccu tap seven_day")
            usage = usage_from_seven_day(week)
        except ValueError as e:
            raise ValueError(f"{self._tap_file}: {e}") from e
        _warn_if_stale(self._tap_file, mtime, "ccu tap file")
        return usage


def newest_cache_file(cache_dir: Path = CACHE_DIR) -> Path:
    """Newest ``usage*.json`` in ``cache_dir`` by (mtime, name).

    Skips any ``.bak`` name (the glob already excludes ``block-cache-*``). With
    no match it returns the legacy ``usage.json`` path so the caller's read
    raises FileNotFoundError.
    """
    cache_dir = Path(cache_dir)
    candidates = [p for p in cache_dir.glob("usage*.json") if ".bak" not in p.name]
    if not candidates:
        return cache_dir / LEGACY_CACHE_NAME
    return max(candidates, key=lambda p: (p.stat().st_mtime, p.name))


class CcstatuslineCacheSource:
    """Fallback usage source: ccstatusline's on-disk usage cache.

    Ports the cache read + staleness warning from the original
    claude-weekly-forecast.py. Raises FileNotFoundError / OSError when the cache
    is missing or unreadable, and ValueError naming the file read when its
    content is malformed, so the caller can fail loud.
    """

    def __init__(self, cache_file: Path | None = None, cache_dir: Path = CACHE_DIR):
        self._cache_file = Path(cache_file) if cache_file else None
        self._cache_dir = Path(cache_dir)

    def read(self) -> Usage:
        # Resolved at read time so the staleness warning names the file read.
        cache_file = self._cache_file or newest_cache_file(self._cache_dir)
        mtime = cache_file.stat().st_mtime  # FileNotFoundError if absent
        try:
            data = _require_mapping(
                json.loads(cache_file.read_text(encoding="utf-8")),
                "ccstatusline cache",
            )
            usage = Usage(
                used_pct=_coerce_used(data.get("weeklyUsage")),
                reset_at_iso=_normalize_reset(data.get("weeklyResetAt")),
            )
        except ValueError as e:
            # Name the file: discovery picks among several candidates.
            raise ValueError(f"{cache_file}: {e}") from e

        _warn_if_stale(cache_file, mtime, "ccstatusline cache")
        return usage


def resolve_source(
    *, tap_file: Path | None = None, cache_dir: Path = CACHE_DIR
) -> UsageSource:
    """The tap file when it exists, else the ccstatusline cache.

    A stale tap still wins over a fresh cache; its staleness warning names the
    tap path so a user who removed the tap from ``statusLine`` can see why.
    """
    tap = Path(tap_file) if tap_file else default_tap_file()
    if tap.exists():
        return TapFileSource(tap)
    return CcstatuslineCacheSource(None, cache_dir)
