"""Flat-subset YAML config loader for the ccu plugin.

Runtime code is stdlib-only: no PyYAML. This hand-rolls the narrow YAML subset
the ccu config uses, following the `cobtask-common` frontmatter-parser
precedent. Only the documented flat-subset shape below is supported; anything
malformed raises `ConfigError` rather than guessing.

Supported shape::

    timezone: America/New_York
    active_hours:
      start: "09:00"
      end: "23:00"
    blackouts:
      - "2026-08-01"
      - { start: "2026-08-10", end: "2026-08-17" }
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Default active window: the whole day, so an unconfigured install still
# forecasts (every hour counts) instead of dividing by zero.
_DEFAULT_START = dt.time(0, 0)
_DEFAULT_END = dt.time(23, 59)
_DEFAULT_TZ = "UTC"

_INLINE_RANGE_RE = re.compile(
    r"^\{\s*start\s*:\s*(?P<start>.+?)\s*,\s*end\s*:\s*(?P<end>.+?)\s*\}$"
)


class ConfigError(Exception):
    """Raised when the config file cannot be parsed into a valid Config."""


@dataclass
class Config:
    tz: ZoneInfo
    active_start: dt.time
    active_end: dt.time
    blackouts: list[tuple[dt.date, dt.date]] = field(default_factory=list)


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _parse_time(raw: str) -> dt.time:
    val = _unquote(raw)
    try:
        hh, mm = val.split(":")
        return dt.time(int(hh), int(mm))
    except (ValueError, TypeError) as e:
        raise ConfigError(f"Invalid time {raw!r}; expected 24h HH:MM.") from e


def _parse_date(raw: str) -> dt.date:
    val = _unquote(raw)
    try:
        return dt.date.fromisoformat(val)
    except (ValueError, TypeError) as e:
        raise ConfigError(f"Invalid date {raw!r}; expected YYYY-MM-DD.") from e


def _parse_blackout_item(item: str) -> tuple[dt.date, dt.date]:
    item = item.strip()
    m = _INLINE_RANGE_RE.match(item)
    if m:
        start = _parse_date(m.group("start"))
        end = _parse_date(m.group("end"))
        if end < start:
            raise ConfigError(
                f"Blackout range end {end.isoformat()} precedes start "
                f"{start.isoformat()}."
            )
        return (start, end)
    d = _parse_date(item)
    return (d, d)


def load_config(path: Path) -> Config:
    """Parse the flat-subset YAML config at ``path`` into a Config.

    Unknown top-level keys are ignored. Missing keys fall back to defaults
    (UTC, whole-day active window, no blackouts). Malformed times/dates raise
    ConfigError.
    """
    try:
        # utf-8-sig strips a leading BOM so a BOM-prefixed first key still
        # parses at indent 0 instead of being silently misread as nested content.
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ConfigError(f"Could not read config at {path} ({e}).") from e
    except UnicodeDecodeError as e:
        raise ConfigError(f"Config at {path} is not valid UTF-8 ({e}).") from e

    tz_name = _DEFAULT_TZ
    active_start = _DEFAULT_START
    active_end = _DEFAULT_END
    blackouts: list[tuple[dt.date, dt.date]] = []

    # None = no section context yet (start of file); "ignore" = inside a block
    # whose content is intentionally skipped (timezone / unknown top-level key).
    section: str | None = None  # None | "ignore" | "active_hours" | "blackouts"

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if indent == 0:
            # A new top-level key ends any nested section.
            if stripped.startswith("- "):
                raise ConfigError(f"Unexpected top-level list item: {raw_line!r}")
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "timezone":
                section = "ignore"  # scalar key; nothing nests under it
                if value:
                    tz_name = _unquote(value)
            elif key == "active_hours":
                section = "active_hours"
            elif key == "blackouts":
                section = "blackouts"
            else:
                section = "ignore"  # unknown key: skip its block
            continue

        # Indented line belongs to the current section.
        if section is None:
            # Indentation before any top-level key (e.g. a stray leading tab or
            # space on the first key) would otherwise be silently dropped,
            # contradicting the fail-loud contract.
            raise ConfigError(f"Unexpected indented line before any key: {raw_line!r}")
        if section == "active_hours":
            key, _, value = stripped.partition(":")
            key = key.strip()
            if key == "start":
                active_start = _parse_time(value)
            elif key == "end":
                active_end = _parse_time(value)
        elif section == "blackouts":
            if not stripped.startswith("- "):
                raise ConfigError(f"Malformed blackouts entry: {raw_line!r}")
            blackouts.append(_parse_blackout_item(stripped[2:]))
        # else: indented content under an ignored/unknown key -> skip

    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ConfigError(f"Unknown timezone {tz_name!r}.") from e

    if active_end <= active_start:
        # A zero-width or inverted (overnight) window makes every day contribute
        # zero active seconds, which would silently forecast projected=inf
        # forever. Reject it rather than emit a plausible-but-wrong number.
        raise ConfigError(
            f"active_hours end {active_end.isoformat()} must be after start "
            f"{active_start.isoformat()}; overnight/wraparound windows are not "
            "supported."
        )

    return Config(
        tz=tz,
        active_start=active_start,
        active_end=active_end,
        blackouts=blackouts,
    )


def claude_root() -> Path:
    """The `.claude` root: ``$CLAUDE_CONFIG_DIR`` if set and non-empty, else ``~/.claude``."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env.strip()) if env and env.strip() else Path.home() / ".claude"


def default_config_dir() -> Path:
    """The default user config dir under the `.sct` plugin slot."""
    return claude_root() / ".sct" / "ccu" / "config"


def default_config_path() -> Path:
    """The default config file ccu reads when ``--config`` is not passed."""
    return default_config_dir() / "ccu.yaml"


def default_config() -> Config:
    """Built-in defaults: UTC, whole-day active window, no blackouts."""
    return Config(
        tz=ZoneInfo(_DEFAULT_TZ),
        active_start=_DEFAULT_START,
        active_end=_DEFAULT_END,
    )


def ensure_config_dir() -> Path:
    """Create the default config dir if absent and return it.

    Idempotent (``mkdir -p``-style). Seeds nothing: ccu runs on built-in
    defaults when no ``ccu.yaml`` is present, and the config shape is documented
    in the README and this module's docstring. Never clobbers a user's
    ``ccu.yaml``.
    """
    d = default_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d
