"""Config-loader tests for the ccu plugin."""

import datetime as dt

import pytest

from config import ConfigError, load_config


def test_default_config_path_honors_claude_config_dir(monkeypatch, tmp_path):
    from config import default_config_path

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert default_config_path() == tmp_path / ".sct" / "ccu" / "config" / "ccu.yaml"


def test_claude_root_strips_whitespace(monkeypatch, tmp_path):
    # A CLAUDE_CONFIG_DIR value with surrounding whitespace must resolve to the
    # intended absolute dir, not a bogus relative path built from the padded
    # string.
    from config import claude_root

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", f"  {tmp_path}  ")
    root = claude_root()
    assert root == tmp_path
    assert root.is_absolute()


def test_claude_root_matches_usage_source_root(monkeypatch, tmp_path):
    # config and usage_source must resolve the same .claude root for the same
    # env value (they share one helper), so the config path and the credentials
    # path cannot disagree -- including the whitespace-only edge.
    from config import claude_root
    from usage_source import _claude_config_dir

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", f"  {tmp_path}  ")
    assert claude_root() == _claude_config_dir()

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "   ")
    assert claude_root() == _claude_config_dir()


def test_ensure_config_dir_creates_without_seeding_idempotent(monkeypatch, tmp_path):
    from config import ensure_config_dir

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    d = ensure_config_dir()
    assert d.is_dir()
    assert not (d / "ccu.example.yaml").exists()  # seeds nothing

    (d / "ccu.yaml").write_text("timezone: UTC\n")  # user's own file
    ensure_config_dir()  # second run must not error or clobber
    assert (d / "ccu.yaml").read_text() == "timezone: UTC\n"


def test_load_config_parses_active_hours_and_blackouts(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text(
        "timezone: America/New_York\n"
        'active_hours:\n  start: "09:00"\n  end: "23:00"\n'
        "blackouts:\n"
        '  - "2026-08-01"\n'
        '  - { start: "2026-08-10", end: "2026-08-17" }\n'
    )
    cfg = load_config(p)
    assert cfg.active_start == dt.time(9, 0)
    assert cfg.active_end == dt.time(23, 0)
    assert cfg.blackouts == [
        (dt.date(2026, 8, 1), dt.date(2026, 8, 1)),
        (dt.date(2026, 8, 10), dt.date(2026, 8, 17)),
    ]
    assert str(cfg.tz) == "America/New_York"


def test_load_config_defaults_when_keys_absent(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text("timezone: UTC\n")
    cfg = load_config(p)
    assert str(cfg.tz) == "UTC"
    assert cfg.active_start == dt.time(0, 0)
    assert cfg.active_end == dt.time(23, 59)
    assert cfg.blackouts == []


def test_load_config_rejects_malformed_time(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text('active_hours:\n  start: "9 oclock"\n  end: "23:00"\n')
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_rejects_malformed_blackout_date(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text('blackouts:\n  - "not-a-date"\n')
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_rejects_overnight_window(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text('active_hours:\n  start: "22:00"\n  end: "06:00"\n')
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_rejects_zero_width_window(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text('active_hours:\n  start: "09:00"\n  end: "09:00"\n')
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_strips_leading_bom(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_bytes("﻿timezone: America/New_York\n".encode("utf-8"))
    cfg = load_config(p)
    assert str(cfg.tz) == "America/New_York"  # not silently defaulted to UTC


def test_load_config_rejects_indented_first_key(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text("\ttimezone: America/New_York\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_rejects_invalid_utf8(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_bytes(b"timezone: \xff\xfe\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_rejects_invalid_timezone(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text("timezone: Not/AZone\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_rejects_blackout_range_end_before_start(tmp_path):
    p = tmp_path / "ccu.yaml"
    p.write_text('blackouts:\n  - { start: "2026-08-17", end: "2026-08-10" }\n')
    with pytest.raises(ConfigError):
        load_config(p)
