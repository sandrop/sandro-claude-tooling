"""Usage-source seam for the ccu plugin.

Decouples the forecast from ccstatusline. The direct-TUI path pulls weekly
usage straight from the Anthropic usage API via an injected fetcher; the
ccstatusline cache is only a fallback. Both providers yield the same `Usage`
shape so the CLI is source-agnostic.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

# ccstatusline populates this on each render (short TTL) from the OAuth usage
# API. It is the fallback source, not the primary one.
CACHE_FILE = Path.home() / ".cache" / "ccstatusline" / "usage.json"
MAX_CACHE_AGE_SECONDS = 15 * 60

# Live Anthropic usage endpoint + OAuth details, mirrored from ccstatusline's
# usage fetch (host "api.anthropic.com", path "/api/oauth/usage", 5s timeout).
USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_API_BETA_HEADER = "oauth-2025-04-20"
USAGE_API_TIMEOUT_S = 5.0
# macOS Keychain generic-password service ccstatusline stores the token under.
CREDENTIALS_KEYCHAIN_SERVICE = "Claude Code-credentials"
# Absolute path to the macOS `security` CLI (avoid a $PATH search for a binary
# that hands back an OAuth secret; CWE-426) and a bound on the Keychain read so
# a locked keychain or a stuck `securityd` cannot hang the whole forecast.
SECURITY_BIN = "/usr/bin/security"
KEYCHAIN_TIMEOUT_S = 5.0


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


def _coerce_used(raw: object) -> float:
    # bool is an int subclass; reject it so `true` is not coerced to 1.0.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("Usage payload missing a numeric 'weeklyUsage'.")
    val = float(raw)
    if val < 0:
        raise ValueError(f"Usage 'weeklyUsage' must be non-negative, got {val}.")
    return val


def _require_mapping(data: object, source: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError(
            f"{source} payload must be a JSON object, got {type(data).__name__}."
        )
    return data


class AnthropicUsageAPISource:
    """Primary source: usage straight from the Anthropic usage API.

    The network + OAuth boundary is injected as ``fetcher`` (a zero-arg callable
    returning the API's JSON dict) so the mapping is fully testable and the one
    un-mockable seam stays isolated. The first successful read is memoized so a
    probe by ``resolve_source`` does not cost a second request.
    """

    def __init__(self, fetcher: Callable[[], dict]):
        self._fetcher = fetcher
        self._cached: Usage | None = None

    def read(self) -> Usage:
        if self._cached is None:
            payload = _require_mapping(self._fetcher(), "Anthropic usage API")
            self._cached = Usage(
                used_pct=_coerce_used(payload.get("weeklyUsage")),
                reset_at_iso=_normalize_reset(payload.get("weeklyResetAt")),
            )
        return self._cached


class CcstatuslineCacheSource:
    """Fallback source: ccstatusline's on-disk usage cache.

    Ports the cache read + staleness warning from the original
    claude-weekly-forecast.py verbatim. Raises FileNotFoundError / OSError /
    JSONDecodeError when the cache is missing or unreadable so the caller can
    fail loud.
    """

    def __init__(self, cache_file: Path = CACHE_FILE):
        self._cache_file = Path(cache_file)

    def read(self) -> Usage:
        mtime = self._cache_file.stat().st_mtime  # FileNotFoundError if absent
        data = _require_mapping(
            json.loads(self._cache_file.read_text(encoding="utf-8")),
            "ccstatusline cache",
        )

        age = time.time() - mtime
        if age > MAX_CACHE_AGE_SECONDS:
            print(
                f"Warning: ccstatusline cache is {int(age // 60)} min old "
                f"({self._cache_file}); numbers may be stale. Open Claude Code "
                "to refresh, or pass values manually.",
                file=sys.stderr,
            )

        return Usage(
            used_pct=_coerce_used(data.get("weeklyUsage")),
            reset_at_iso=_normalize_reset(data.get("weeklyResetAt")),
        )


def _claude_config_dir() -> Path:
    """Claude Code config dir root.

    Delegates to :func:`config.claude_root` so this credentials path and the
    config path resolve ``$CLAUDE_CONFIG_DIR`` identically (one source of
    truth, including whitespace-only handling).
    """
    from config import claude_root

    return claude_root()


def _read_token_from_credentials_file(config_dir: Path) -> str | None:
    """Parse ``<config_dir>/.credentials.json`` -> ``claudeAiOauth.accessToken``.

    Returns None (never raises) on any miss so the caller can try the next
    source or fail with one clear message.
    """
    try:
        data = json.loads(
            (config_dir / ".credentials.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    # `claudeAiOauth` can be present-but-null mid token-refresh; guard the type
    # so a non-dict value returns None rather than raising AttributeError (this
    # helper documents "never raises").
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    return token if isinstance(token, str) and token else None


def _default_keychain_reader(service: str) -> str:
    """Read a macOS Keychain generic-password secret via the ``security`` CLI.

    Uses an absolute path (no $PATH search for a secret-bearing binary) and a
    timeout so a locked keychain or wedged ``securityd`` raises TimeoutExpired
    (which the caller treats as a miss) rather than hanging the forecast.
    """
    return subprocess.run(
        [SECURITY_BIN, "find-generic-password", "-s", service, "-w"],
        capture_output=True,
        text=True,
        check=True,
        timeout=KEYCHAIN_TIMEOUT_S,
    ).stdout


def _resolve_oauth_token(
    *,
    platform: str = sys.platform,
    config_dir: Path | None = None,
    keychain_reader: Callable[[str], str] = _default_keychain_reader,
) -> str:
    """Resolve the Anthropic OAuth token the same way ccstatusline does.

    macOS tries the Keychain service ``Claude Code-credentials`` first, then the
    credentials file; other platforms read the file only. Raises RuntimeError
    (no secret in the message) when no source yields a token.
    """
    config_dir = config_dir or _claude_config_dir()
    if platform == "darwin":
        try:
            tok = (keychain_reader(CREDENTIALS_KEYCHAIN_SERVICE) or "").strip()
        except Exception:
            tok = ""
        if tok:
            return tok
    tok = _read_token_from_credentials_file(config_dir)
    if tok:
        return tok
    raise RuntimeError(
        "No Anthropic OAuth token found (Keychain 'Claude Code-credentials' or "
        "~/.claude/.credentials.json). Log in to Claude Code, or use "
        "--prefer-cache / --used / --reset."
    )


def _map_usage_api_response(raw: object) -> dict:
    """Remap the raw ``/api/oauth/usage`` payload to the seam's dict shape.

    The live API reports weekly usage under ``seven_day`` (``utilization`` is a
    0-100 percent, ``resets_at`` an ISO timestamp), not the ``weeklyUsage`` /
    ``weeklyResetAt`` keys the seam consumes. Values pass through un-coerced so a
    malformed payload trips the seam's ``_coerce_used`` / ``_normalize_reset``
    guards loudly rather than swapping to the cache silently.
    """
    data = _require_mapping(raw, "Anthropic usage API")
    seven_day = data.get("seven_day")
    if not isinstance(seven_day, dict):
        raise ValueError("Anthropic usage API payload missing a 'seven_day' object.")
    return {
        "weeklyUsage": seven_day.get("utilization"),
        "weeklyResetAt": seven_day.get("resets_at"),
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the Bearer token is never re-sent to a redirect
    target. urllib's default opener retains the ``Authorization`` header across
    a 3xx (including cross-host), so a TLS-intercepting proxy could 302 the
    request and capture the OAuth token. This endpoint should never redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # do not follow; the 3xx response falls through to callers


def _build_no_redirect_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect)


def _default_anthropic_fetcher() -> dict:
    """Live Anthropic usage-API fetch: token -> GET -> remap to the seam dict.

    The one un-TDD-able seam (the actual network + OAuth call). Its pieces
    (`_resolve_oauth_token`, `_map_usage_api_response`) are unit-tested; only
    this glue stays a manual smoke. A failure here (no creds, network down) is
    caught by ``resolve_source``, which falls back to the ccstatusline cache.
    """
    token = _resolve_oauth_token()
    req = urllib.request.Request(
        USAGE_API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": USAGE_API_BETA_HEADER,
        },
        method="GET",
    )
    opener = _build_no_redirect_opener()
    with opener.open(req, timeout=USAGE_API_TIMEOUT_S) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    return _map_usage_api_response(raw)


def resolve_source(
    cfg=None,
    *,
    prefer_api: bool = True,
    fetcher: Callable[[], dict] | None = None,
    cache_file: Path = CACHE_FILE,
) -> UsageSource:
    """Pick the usage source: Anthropic API first, ccstatusline cache fallback.

    ``cfg`` is accepted for forward compatibility (future per-config source
    selection); it is not consulted today. When ``prefer_api`` is true the API
    source is probed once; if the probe raises (no creds, network down), the
    ccstatusline cache source is returned instead.
    """
    del cfg  # reserved for future per-config routing
    if prefer_api:
        api = AnthropicUsageAPISource(fetcher or _default_anthropic_fetcher)
        try:
            api.read()  # probe (memoized, so no double fetch)
            return api
        except ValueError as e:
            # A successful fetch that returned a malformed payload is a real
            # API/mapping bug, not a connectivity miss. Surface it before
            # falling back so it is not silently masked by the cache.
            print(
                f"Warning: Anthropic usage API returned malformed data ({e}); "
                "falling back to the ccstatusline cache.",
                file=sys.stderr,
            )
            return CcstatuslineCacheSource(cache_file)
        except RuntimeError:
            # No OAuth token (not logged in): the expected, quiet fallback.
            # Do not print the exception text: it is scrubbed of the token, but
            # keeping the no-creds path silent avoids a warning on every render.
            return CcstatuslineCacheSource(cache_file)
        except Exception:
            # Connectivity / HTTP / timeout failure: a live source that was
            # meant to work did not. Surface a token-safe notice (never the
            # exception text, which can carry the OAuth token) so the fallback
            # to possibly-stale cache is not invisible.
            print(
                "Note: Anthropic usage API unavailable; using the ccstatusline "
                "cache (run with --prefer-cache to silence).",
                file=sys.stderr,
            )
            return CcstatuslineCacheSource(cache_file)
    return CcstatuslineCacheSource(cache_file)
