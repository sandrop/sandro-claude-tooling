"""CLI wiring tests for the ccu plugin."""

import datetime as dt
import json
from zoneinfo import ZoneInfo

from ccu_forecast import assess, main, parse_reset

CFG_TEXT = (
    "timezone: America/New_York\n" 'active_hours:\n  start: "09:00"\n  end: "23:00"\n'
)


def _write_cfg(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text(CFG_TEXT)
    return p


def test_main_prints_forecast_with_injected_usage(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    reset = (dt.datetime.now(ZoneInfo("UTC")) + dt.timedelta(days=2)).isoformat()
    rc = main(["--config", str(cfg), "--used", "50", "--reset", reset])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Projected finish:" in out
    assert "Burn-down:" in out
    assert "Exhaustion:" in out


def test_main_default_path_miss_uses_defaults(monkeypatch, tmp_path, capsys):
    # No --config and no config file at the default path: soft-miss to
    # built-in defaults, and the CLI scaffolds the .sct config dir.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    reset = (dt.datetime.now(ZoneInfo("UTC")) + dt.timedelta(days=2)).isoformat()
    rc = main(["--used", "50", "--reset", reset])
    assert rc == 0
    assert "Projected finish:" in capsys.readouterr().out
    assert (tmp_path / ".sct" / "ccu" / "config").is_dir()


def test_main_default_path_malformed_fails_loud(monkeypatch, tmp_path, capsys):
    # A malformed file at the default path fails loud, same as --config does.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    d = tmp_path / ".sct" / "ccu" / "config"
    d.mkdir(parents=True)
    (d / "ccu.yaml").write_text('active_hours:\n  start: "nope"\n  end: "23:00"\n')
    reset = (dt.datetime.now(ZoneInfo("UTC")) + dt.timedelta(days=2)).isoformat()
    rc = main(["--used", "50", "--reset", reset])
    assert rc == 1
    assert "Error" in capsys.readouterr().err


def test_main_explicit_config_overrides_default(monkeypatch, tmp_path):
    # An explicit --config wins even when a (malformed) default file exists.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    d = tmp_path / ".sct" / "ccu" / "config"
    d.mkdir(parents=True)
    (d / "ccu.yaml").write_text('active_hours:\n  start: "bad"\n  end: "x"\n')
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        'timezone: America/New_York\nactive_hours:\n  start: "09:00"\n  end: "23:00"\n'
    )
    reset = (dt.datetime.now(ZoneInfo("UTC")) + dt.timedelta(days=2)).isoformat()
    rc = main(["--config", str(explicit), "--used", "50", "--reset", reset])
    assert rc == 0  # explicit read; malformed default ignored


def test_main_default_path_hit_loads_config(monkeypatch, tmp_path, capsys):
    # A valid ccu.yaml at the default path (no --config) is actually loaded and
    # its content consumed -- the AC #5 "default-path hit" branch. The miss and
    # malformed tests above only prove the path is attempted, not that valid
    # content is honored, so spy on load_config to confirm the default file is
    # the one read.
    import ccu_forecast

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    d = tmp_path / ".sct" / "ccu" / "config"
    d.mkdir(parents=True)
    (d / "ccu.yaml").write_text(CFG_TEXT)

    seen = {}
    real_load = ccu_forecast.load_config

    def _spy(path):
        seen["path"] = path
        return real_load(path)

    monkeypatch.setattr(ccu_forecast, "load_config", _spy)
    reset = (dt.datetime.now(ZoneInfo("UTC")) + dt.timedelta(days=2)).isoformat()
    rc = main(["--used", "50", "--reset", reset])
    assert rc == 0
    assert "Projected finish:" in capsys.readouterr().out
    assert seen["path"] == d / "ccu.yaml"  # default path resolved and loaded
    assert seen["path"].read_text() == CFG_TEXT  # valid content consumed


def test_main_config_dir_creation_failure_fails_loud(monkeypatch, tmp_path, capsys):
    # If the config dir cannot be created (here CLAUDE_CONFIG_DIR points at a
    # file, so mkdir raises NotADirectoryError), the CLI fails loud with a clean
    # Error + exit 1, not a raw unhandled traceback.
    root_file = tmp_path / "not_a_dir"
    root_file.write_text("")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root_file))
    reset = (dt.datetime.now(ZoneInfo("UTC")) + dt.timedelta(days=2)).isoformat()
    rc = main(["--used", "50", "--reset", reset])
    assert rc == 1
    assert "Error" in capsys.readouterr().err


def test_main_prefer_cache_reads_cache_file(tmp_path, capsys, monkeypatch):
    cfg = _write_cfg(tmp_path)
    cache = tmp_path / "usage.json"
    reset = (dt.datetime.now(ZoneInfo("UTC")) + dt.timedelta(days=2)).isoformat()
    cache.write_text(json.dumps({"weeklyUsage": 30.0, "weeklyResetAt": reset}))
    # Point the module's default cache at our tmp cache.
    import ccu_forecast

    monkeypatch.setattr(ccu_forecast, "CACHE_FILE", cache)
    rc = main(["--config", str(cfg), "--prefer-cache"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Current weekly usage: 30.0%" in out


def test_main_errors_when_reset_in_past(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    past = (dt.datetime.now(ZoneInfo("UTC")) - dt.timedelta(days=1)).isoformat()
    rc = main(["--config", str(cfg), "--used", "50", "--reset", past])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Error" in err


def test_main_rejects_negative_used(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    reset = (dt.datetime.now(ZoneInfo("UTC")) + dt.timedelta(days=2)).isoformat()
    rc = main(["--config", str(cfg), "--used", "-5", "--reset", reset])
    err = capsys.readouterr().err
    assert rc == 1
    assert "non-negative" in err


def test_assess_thresholds():
    assert "Comfortable" in assess(80.0)
    assert "Right at the line" in assess(100.0)
    assert "throttle" in assess(115.0)
    assert "significant throttling" in assess(130.0)
    assert "cut back hard" in assess(200.0)


def test_parse_reset_human_format():
    got = parse_reset("Resets Jun 16 at 1:59am (America/New_York)")
    assert (got.month, got.day, got.hour, got.minute) == (6, 16, 1, 59)
    assert str(got.tzinfo) == "America/New_York"


def test_parse_reset_rejects_naive_iso():
    import pytest

    with pytest.raises(ValueError):
        parse_reset("2026-08-01T05:59:00")  # no timezone
