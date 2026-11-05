"""Usage-source seam tests for the ccu plugin.

These lock the Human-Note acceptance criterion: the direct-TUI path sources
usage straight from the Anthropic usage API with no ccstatusline dependency,
and only falls back to the ccstatusline cache when the API path is unavailable.
"""

import datetime as dt
import json
import subprocess
import urllib.error
from zoneinfo import ZoneInfo

import pytest

from config import Config
from usage_source import (
    AnthropicUsageAPISource,
    CcstatuslineCacheSource,
    Usage,
    _map_usage_api_response,
    _read_token_from_credentials_file,
    _resolve_oauth_token,
    resolve_source,
)


def _cfg():
    return Config(
        tz=ZoneInfo("UTC"),
        active_start=dt.time(9, 0),
        active_end=dt.time(23, 0),
        blackouts=[],
    )


def test_api_source_read_maps_fetcher_payload():
    src = AnthropicUsageAPISource(
        fetcher=lambda: {
            "weeklyUsage": 56.0,
            "weeklyResetAt": "2026-08-01T05:59:00Z",
        }
    )
    assert src.read() == Usage(56.0, "2026-08-01T05:59:00+00:00")


def test_api_source_read_raises_on_missing_field():
    src = AnthropicUsageAPISource(fetcher=lambda: {"weeklyUsage": 56.0})
    with pytest.raises(ValueError):
        src.read()


def test_resolve_prefers_api_falls_back_to_cache(tmp_path):
    def boom():
        raise RuntimeError("no OAuth creds")

    src = resolve_source(
        _cfg(), prefer_api=True, fetcher=boom, cache_file=tmp_path / "usage.json"
    )
    assert isinstance(src, CcstatuslineCacheSource)


def test_resolve_returns_api_source_when_fetcher_ok(tmp_path):
    src = resolve_source(
        _cfg(),
        prefer_api=True,
        fetcher=lambda: {"weeklyUsage": 10.0, "weeklyResetAt": "2026-08-01T05:59:00Z"},
        cache_file=tmp_path / "usage.json",
    )
    assert isinstance(src, AnthropicUsageAPISource)
    assert src.read().used_pct == 10.0


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


def test_coerce_rejects_bool_usage():
    src = AnthropicUsageAPISource(
        fetcher=lambda: {"weeklyUsage": True, "weeklyResetAt": "2026-08-01T05:59:00Z"}
    )
    with pytest.raises(ValueError):
        src.read()


def test_coerce_rejects_negative_usage():
    src = AnthropicUsageAPISource(
        fetcher=lambda: {"weeklyUsage": -5, "weeklyResetAt": "2026-08-01T05:59:00Z"}
    )
    with pytest.raises(ValueError):
        src.read()


def test_non_dict_payload_raises_valueerror():
    src = AnthropicUsageAPISource(fetcher=lambda: ["not", "a", "dict"])
    with pytest.raises(ValueError):
        src.read()


def test_resolve_warns_and_falls_back_on_malformed_payload(tmp_path, capsys):
    # A successful fetch returning a malformed payload is a real bug, not a
    # connectivity miss: resolve_source must warn (not silently mask it).
    src = resolve_source(
        _cfg(),
        prefer_api=True,
        fetcher=lambda: ["not", "a", "dict"],
        cache_file=tmp_path / "usage.json",
    )
    assert isinstance(src, CcstatuslineCacheSource)
    assert "malformed" in capsys.readouterr().err.lower()


def test_resolve_falls_back_silently_when_no_creds(tmp_path, capsys):
    # No OAuth token (RuntimeError) is the expected not-logged-in path: fall
    # back to cache with no noise so it does not warn on every render.
    def no_creds():
        raise RuntimeError("No Anthropic OAuth token found")

    src = resolve_source(
        _cfg(), prefer_api=True, fetcher=no_creds, cache_file=tmp_path / "usage.json"
    )
    assert isinstance(src, CcstatuslineCacheSource)
    assert capsys.readouterr().err == ""  # no-creds path is deliberately quiet


def test_resolve_notifies_on_connectivity_failure(tmp_path, capsys):
    # A real connectivity/HTTP failure (not a missing-creds RuntimeError) still
    # falls back to cache, but emits a token-safe notice so the swap is visible.
    def offline():
        raise urllib.error.URLError("connection refused")

    src = resolve_source(
        _cfg(), prefer_api=True, fetcher=offline, cache_file=tmp_path / "usage.json"
    )
    assert isinstance(src, CcstatuslineCacheSource)
    err = capsys.readouterr().err
    assert "Anthropic usage API unavailable" in err
    assert "connection refused" not in err  # never echo the exception text


# --- live usage-API fetcher (remap + OAuth resolution) ---


def test_map_usage_api_response_remaps_seven_day():
    raw = {
        "five_hour": {"utilization": 12.0, "resets_at": "2026-07-28T18:00:00Z"},
        "seven_day": {"utilization": 56.0, "resets_at": "2026-08-01T05:59:00Z"},
        "seven_day_opus": {"utilization": 40.0, "resets_at": "2026-08-01T05:59:00Z"},
    }
    assert _map_usage_api_response(raw) == {
        "weeklyUsage": 56.0,
        "weeklyResetAt": "2026-08-01T05:59:00Z",
    }


def test_map_usage_api_response_missing_seven_day_raises():
    with pytest.raises(ValueError):
        _map_usage_api_response({"five_hour": {"utilization": 1.0}})


def test_map_usage_api_response_non_dict_raises():
    with pytest.raises(ValueError):
        _map_usage_api_response(["not", "a", "dict"])


def test_mapped_payload_feeds_seam_and_missing_utilization_raises():
    good = _map_usage_api_response(
        {"seven_day": {"utilization": 33.0, "resets_at": "2026-08-01T05:59:00Z"}}
    )
    assert AnthropicUsageAPISource(fetcher=lambda: good).read() == Usage(
        33.0, "2026-08-01T05:59:00+00:00"
    )
    bad = _map_usage_api_response({"seven_day": {"resets_at": "2026-08-01T05:59:00Z"}})
    with pytest.raises(ValueError):
        AnthropicUsageAPISource(fetcher=lambda: bad).read()


def test_resolve_oauth_token_reads_credentials_file(tmp_path):
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok-abc"}})
    )
    assert _resolve_oauth_token(platform="linux", config_dir=tmp_path) == "tok-abc"


def test_resolve_oauth_token_prefers_keychain_on_darwin(tmp_path):
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "file-tok"}})
    )
    tok = _resolve_oauth_token(
        platform="darwin",
        config_dir=tmp_path,
        keychain_reader=lambda service: "keychain-tok\n",
    )
    assert tok == "keychain-tok"


def test_resolve_oauth_token_darwin_falls_back_to_file(tmp_path):
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "file-tok"}})
    )

    def boom(_service):
        raise OSError("keychain unavailable")

    assert (
        _resolve_oauth_token(
            platform="darwin", config_dir=tmp_path, keychain_reader=boom
        )
        == "file-tok"
    )


def test_resolve_oauth_token_raises_when_absent(tmp_path):
    with pytest.raises(RuntimeError):
        _resolve_oauth_token(platform="linux", config_dir=tmp_path)


def test_resolve_oauth_token_darwin_timeout_falls_back_to_file(tmp_path):
    # A locked keychain / wedged securityd surfaces as TimeoutExpired; it must
    # be treated as a miss and fall through to the credentials file, not hang.
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "file-tok"}})
    )

    def slow(_service):
        raise subprocess.TimeoutExpired(cmd="security", timeout=5.0)

    assert (
        _resolve_oauth_token(
            platform="darwin", config_dir=tmp_path, keychain_reader=slow
        )
        == "file-tok"
    )


def test_read_token_credentials_file_null_oauth_returns_none(tmp_path):
    # `claudeAiOauth` present-but-null (seen mid token-refresh) must return None,
    # not raise AttributeError (the helper documents "never raises").
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": None}))
    assert _read_token_from_credentials_file(tmp_path) is None


def test_resolve_oauth_token_null_oauth_raises_runtimeerror(tmp_path):
    # The masked-AttributeError path: a non-dict claudeAiOauth must surface the
    # clean RuntimeError guidance, not an AttributeError.
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": None}))
    with pytest.raises(RuntimeError):
        _resolve_oauth_token(platform="linux", config_dir=tmp_path)


def test_default_fetcher_builds_request_and_remaps(monkeypatch):
    import io

    import usage_source as us

    captured = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    def fake_open(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["timeout"] = timeout
        body = json.dumps(
            {"seven_day": {"utilization": 77.0, "resets_at": "2026-08-01T05:59:00Z"}}
        ).encode()
        return _Resp(body)

    class _FakeOpener:
        # The fetcher builds a no-redirect opener and calls .open(); patch that
        # seam so the request-construction assertions still run offline.
        def open(self, req, timeout=None):
            return fake_open(req, timeout=timeout)

    monkeypatch.setattr(us, "_resolve_oauth_token", lambda: "tok-xyz")
    monkeypatch.setattr(us, "_build_no_redirect_opener", lambda: _FakeOpener())

    out = us._default_anthropic_fetcher()
    assert out == {"weeklyUsage": 77.0, "weeklyResetAt": "2026-08-01T05:59:00Z"}
    assert captured["url"] == "https://api.anthropic.com/api/oauth/usage"
    assert captured["headers"]["Authorization"] == "Bearer tok-xyz"
    assert captured["headers"]["Anthropic-beta"] == "oauth-2025-04-20"
    assert captured["timeout"] == 5.0
