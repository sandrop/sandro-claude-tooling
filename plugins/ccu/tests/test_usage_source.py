"""Usage-source seam tests for the ccu plugin.

ccu reads usage from two files and nothing else: the statusline tap file
(``ccu_tap.py`` writes it) first, then ccstatusline's newest ``usage*.json``.
"""

import datetime as dt
import json
import os
import time

import pytest
from ccu_forecast import parse_reset
from usage_source import (
    CcstatuslineCacheSource,
    TapFileSource,
    Usage,
    newest_cache_file,
    resolve_source,
)

RESET_EPOCH = 1763000000


def _write_tap(path, seven_day, age_s=0.0):
    path.write_text(
        json.dumps(
            {
                "captured_at": time.time() - age_s,
                "rate_limits": {"seven_day": seven_day},
            }
        )
    )
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def _write_cache(path, used, age_s=0.0):
    path.write_text(
        json.dumps({"weeklyUsage": used, "weeklyResetAt": "2026-08-01T05:59:00Z"})
    )
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def test_tap_source_reads_seven_day_and_converts_epoch(tmp_path):
    tap = _write_tap(
        tmp_path / "usage.json", {"used_percentage": 17.0, "resets_at": RESET_EPOCH}
    )
    u = TapFileSource(tap).read()
    expected = dt.datetime.fromtimestamp(RESET_EPOCH, dt.timezone.utc).isoformat()
    assert u == Usage(17.0, expected)
    assert parse_reset(u.reset_at_iso).timestamp() == RESET_EPOCH


def test_tap_source_accepts_float_resets_at(tmp_path):
    tap = _write_tap(
        tmp_path / "usage.json", {"used_percentage": 3, "resets_at": RESET_EPOCH + 0.5}
    )
    assert TapFileSource(tap).read().used_pct == 3.0


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps(["not", "a", "dict"]),
        json.dumps({"captured_at": 1, "rate_limits": {}}),
        json.dumps({"captured_at": 1, "rate_limits": {"seven_day": "x"}}),
        json.dumps(
            {
                "rate_limits": {
                    "seven_day": {"used_percentage": True, "resets_at": RESET_EPOCH}
                }
            }
        ),
        json.dumps(
            {
                "rate_limits": {
                    "seven_day": {"used_percentage": 5, "resets_at": str(RESET_EPOCH)}
                }
            }
        ),
        json.dumps(
            {"rate_limits": {"seven_day": {"used_percentage": 5, "resets_at": True}}}
        ),
        json.dumps({"rate_limits": {"seven_day": {"resets_at": RESET_EPOCH}}}),
        json.dumps(
            {"rate_limits": {"seven_day": {"used_percentage": 5, "resets_at": 1e20}}}
        ),
    ],
)
def test_tap_source_rejects_bad_types_names_file(tmp_path, content):
    tap = tmp_path / "usage.json"
    tap.write_text(content)
    with pytest.raises(ValueError, match="usage.json"):
        TapFileSource(tap).read()


def test_tap_source_missing_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        TapFileSource(tmp_path / "usage.json").read()


def test_tap_source_stale_warns_with_path(tmp_path, capsys):
    tap = _write_tap(
        tmp_path / "usage.json",
        {"used_percentage": 5, "resets_at": RESET_EPOCH},
        age_s=20 * 60,
    )
    TapFileSource(tap).read()
    err = capsys.readouterr().err
    assert "20 min old" in err
    assert str(tap) in err


def test_tap_source_fresh_is_quiet(tmp_path, capsys):
    tap = _write_tap(
        tmp_path / "usage.json", {"used_percentage": 5, "resets_at": RESET_EPOCH}
    )
    TapFileSource(tap).read()
    assert capsys.readouterr().err == ""


def test_resolve_prefers_tap_when_present(tmp_path):
    tap = _write_tap(
        tmp_path / "usage.json", {"used_percentage": 17.0, "resets_at": RESET_EPOCH}
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir / "usage-x.json", 9)
    assert resolve_source(tap_file=tap, cache_dir=cache_dir).read().used_pct == 17.0


def test_resolve_falls_back_to_cache_without_tap(tmp_path):
    _write_cache(tmp_path / "usage-x.json", 9)
    src = resolve_source(tap_file=tmp_path / "absent.json", cache_dir=tmp_path)
    assert isinstance(src, CcstatuslineCacheSource)
    assert src.read().used_pct == 9.0


def test_resolve_defaults_to_claude_root_tap(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    tap = tmp_path / ".sct" / "ccu" / "usage.json"
    tap.parent.mkdir(parents=True)
    _write_tap(tap, {"used_percentage": 4.0, "resets_at": RESET_EPOCH})
    assert resolve_source(cache_dir=tmp_path / "none").read().used_pct == 4.0


def test_resolve_prefers_tap_even_when_stale_and_warns_with_path(tmp_path, capsys):
    tap = _write_tap(
        tmp_path / "usage.json",
        {"used_percentage": 17.0, "resets_at": RESET_EPOCH},
        age_s=3 * 3600,
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir / "usage-x.json", 9)
    assert resolve_source(tap_file=tap, cache_dir=cache_dir).read().used_pct == 17.0
    assert str(tap) in capsys.readouterr().err


def test_cache_source_reads_written_cache(tmp_path):
    cache = tmp_path / "usage.json"
    cache.write_text(
        json.dumps({"weeklyUsage": 42.0, "weeklyResetAt": "2026-08-01T05:59:00Z"})
    )
    u = CcstatuslineCacheSource(cache).read()
    assert u == Usage(42.0, "2026-08-01T05:59:00+00:00")


def test_cache_source_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        CcstatuslineCacheSource(tmp_path / "nope.json").read()


def test_cache_source_prefers_fresh_hashed_file_over_stale_legacy(tmp_path):
    # Newer ccstatusline writes usage-<hash>.json; the legacy usage.json goes stale.
    _write_cache(tmp_path / "usage.json", 1, age_s=6 * 86400)
    _write_cache(tmp_path / "usage-5b9e43b322e391a3.json", 9)
    assert CcstatuslineCacheSource(cache_dir=tmp_path).read().used_pct == 9.0


def test_newest_cache_file_legacy_only(tmp_path):
    legacy = _write_cache(tmp_path / "usage.json", 5)
    assert newest_cache_file(tmp_path) == legacy


def test_newest_cache_file_ignores_block_cache_and_bak(tmp_path):
    hashed = _write_cache(tmp_path / "usage-x.json", 5, age_s=3600)
    _write_cache(tmp_path / "block-cache-x.json", 7)
    _write_cache(tmp_path / "usage.lock.stuck-20260619T113809.bak", 7)
    _write_cache(tmp_path / "usage-x.bak.json", 7)
    assert newest_cache_file(tmp_path) == hashed


def test_newest_cache_file_two_hashed_newest_wins(tmp_path):
    _write_cache(tmp_path / "usage-new.json", 5)
    _write_cache(tmp_path / "usage-old.json", 5, age_s=3600)
    assert newest_cache_file(tmp_path).name == "usage-new.json"


def test_newest_cache_file_equal_mtime_is_deterministic(tmp_path):
    a = _write_cache(tmp_path / "usage-a.json", 5)
    b = _write_cache(tmp_path / "usage-b.json", 5)
    st = a.stat().st_mtime
    os.utime(b, (st, st))
    assert newest_cache_file(tmp_path) == b


@pytest.mark.parametrize("sub", ["", "absent"])
def test_cache_source_no_files_raises_filenotfound(tmp_path, sub):
    with pytest.raises(FileNotFoundError):
        CcstatuslineCacheSource(cache_dir=tmp_path / sub).read()


def test_cache_source_only_stale_files_warns(tmp_path, capsys):
    _write_cache(tmp_path / "usage-old.json", 5, age_s=2 * 3600)
    assert CcstatuslineCacheSource(cache_dir=tmp_path).read().used_pct == 5.0
    err = capsys.readouterr().err
    assert "Warning: ccstatusline cache is" in err
    assert "usage-old.json" in err


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps(["not", "a", "dict"]),
        json.dumps({"weeklyUsage": True, "weeklyResetAt": "2026-08-01T05:59:00Z"}),
        json.dumps({"weeklyUsage": 5}),
        json.dumps({"weeklyUsage": -5, "weeklyResetAt": "2026-08-01T05:59:00Z"}),
    ],
)
def test_cache_source_malformed_names_selected_file(tmp_path, content):
    _write_cache(tmp_path / "usage-old.json", 5, age_s=3600)
    bad = tmp_path / "usage-new.json"
    bad.write_text(content)
    with pytest.raises(ValueError, match="usage-new.json"):
        CcstatuslineCacheSource(cache_dir=tmp_path).read()
