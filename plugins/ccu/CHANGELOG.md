# Changelog

## 0.8.0 - 2026-11-12

- SCT-0083: new `cli/ccu_launch.py`, a stdlib-only launcher that names no ccu
  version. It reads `~/.claude/plugins/installed_plugins.json`
  (`$CLAUDE_CONFIG_DIR`-aware), picks the highest-version ccu install from
  either marketplace whose `cli/<script>` exists (the dev install wins a tie),
  and execs that script with the same arguments and stdin. When nothing
  resolves, it forwards stdin to the command after `--` (if any), otherwise
  prints nothing, and exits without breaking the status line.
- One-time setup: copy the launcher to `~/.claude/.sct/ccu/ccu_launch.py` and
  point `statusLine` or the ccstatusline widget at it. After that, a ccu
  upgrade needs no settings edit.
- The README gains an Install section with the marketplace install lines.

## 0.7.0 - 2026-11-12

- **Breaking (library only):** removed `AnthropicUsageAPISource` and all OAuth,
  Keychain and `.credentials.json` code. `resolve_source` now takes the
  keyword-only arguments `tap_file` and `cache_dir`. The CLI surface is
  unchanged, and `--prefer-cache` is still a no-op. To migrate library
  callers: drop the `cfg`, `prefer_api` and `fetcher` arguments; replace
  `cache_file=<path>` with `CcstatuslineCacheSource(<path>)`; and replace any
  `AnthropicUsageAPISource` use with `resolve_source()` (tap file, then
  ccstatusline cache) or `TapFileSource(<path>)` once `ccu_tap.py` is in
  `statusLine`.
- SCT-0080: new `cli/ccu_tap.py` reads Claude Code's statusline JSON on stdin
  and records `rate_limits` to `~/.claude/.sct/ccu/usage.json`, with an
  optional wrapped status line command after `--`. Writes are atomic, and
  input without a valid `seven_day` window (`used_percentage` from 0 to 100,
  numeric `resets_at`) or any file-recording failure keeps the previous file
  and still forwards stdin. The reader rejects out-of-range or non-finite
  percentages the same way.
- Usage source order is now `--used`/`--reset`, then the tap file, then the
  newest ccstatusline `usage*.json`. The tap's `resets_at` epoch seconds become
  the ISO reset the forecast already uses.
- With no source, the error shows both `statusLine` snippets for the tap.

## 0.6.0 - 2026-11-11

- **Breaking:** the CLI no longer reads the OAuth token or calls the Anthropic
  usage API. Without `--used`/`--reset`, usage now comes only from the
  ccstatusline cache, so a setup that relied on the API (no ccstatusline cache)
  now fails with "could not resolve usage". To keep forecasting, run Claude
  Code with ccstatusline as its status line so it populates
  `~/.cache/ccstatusline/`, or pass both `--used` and `--reset` explicitly.
  `--prefer-cache` is a no-op kept for compatibility. The API path is slated
  for removal (SCT-0080).
- SCT-0079: the cache source reads the newest `usage*.json` in
  `~/.cache/ccstatusline/` (skipping `block-cache-*` and `.bak` files). Newer
  ccstatusline writes `usage-<hash>.json`, so the fixed `usage.json` path went
  stale and the forecast lost its number. `CACHE_FILE` is replaced by
  `CACHE_DIR` and `newest_cache_file`.
- In the now-unused API path, the macOS Keychain secret is parsed as
  credentials JSON for `claudeAiOauth.accessToken`, and a 401 gets its own
  token-safe notice.

## 0.5.0 - 2026-08-25

- Removed the packaged `config/ccu.example.yaml` and the first-run seeding that
  copied it into the `.sct` config dir. `ensure_config_dir` now only creates the
  dir; ccu already runs on built-in defaults (UTC, whole-day active window, no
  blackouts) when no `ccu.yaml` is present, so the seeded starter was redundant.
  The config shape stays documented in the README and `lib/config.py` docstring.
- Dropped `test_example_config.py` and the seed assertions in
  `test_scaffold_smoke.py` / `test_config.py`; `lib/config.py` no longer imports
  `shutil` or `sys`.

## 0.4.0 - 2026-07-30

- Default config location: when `--config` is not passed, the CLI now reads
  `~/.claude/.sct/ccu/config/ccu.yaml`, resolving the `.claude` root from
  `$CLAUDE_CONFIG_DIR` when set. A missing file there is a soft miss (built-in
  defaults); a malformed one fails loud, same as `--config`. An explicit
  `--config` still overrides.
- On any run without `--config`, the CLI lazily creates the `.sct` config dir
  (`mkdir -p`-style, idempotent) and seeds a starter `ccu.example.yaml` into it,
  never clobbering an existing `ccu.yaml` or `ccu.example.yaml`. No activation
  hook is used: Claude Code has no one-shot install event, only per-session
  `SessionStart`, so dir-creation is folded into the CLI instead.
- New `config.py` helpers: `claude_root`, `default_config_dir`,
  `default_config_path`, `default_config`, `ensure_config_dir`. A subprocess
  smoke test asserts a clean `$CLAUDE_CONFIG_DIR` gets the dir + seed on a real
  run.
- The in-repo `config/ccu.yaml` is now gitignored; `config/ccu.example.yaml`
  remains the shipped seed source.
- Review hardening: `ensure_config_dir()` failures (unwritable
  `.claude`, `$CLAUDE_CONFIG_DIR` pointing at a file) now fail loud with a clean
  `Error: ...` + exit 1 instead of a raw traceback; `claude_root()` strips
  surrounding whitespace from `$CLAUDE_CONFIG_DIR`; `usage_source` now delegates
  to `claude_root()` so the config and credentials roots cannot diverge; a
  missing packaged seed emits a stderr warning instead of a silent no-op.

## 0.3.0 - 2026-07-28

- Wire the live Anthropic usage-API fetcher. `_default_anthropic_fetcher`
  now calls `GET https://api.anthropic.com/api/oauth/usage` with an
  `Authorization: Bearer` header plus `anthropic-beta: oauth-2025-04-20` and a
  5s timeout, and remaps the response's `seven_day.utilization` /
  `seven_day.resets_at` into the seam's `weeklyUsage` / `weeklyResetAt` keys.
  The direct-API path is no longer an inert stub.
- OAuth token resolves the same way ccstatusline does: macOS Keychain service
  `Claude Code-credentials` first, then `$CLAUDE_CONFIG_DIR/.credentials.json`
  (default `~/.claude/.credentials.json`); non-macOS reads the file only. No
  secrets are hardcoded. A malformed live payload still surfaces loudly through
  the existing seam guards rather than a silent cache swap.
- Review hardening: the Keychain read pins the absolute
  `/usr/bin/security` path and bounds itself with a 5s timeout (a locked
  keychain now falls through to the file instead of hanging); the credentials
  parser guards a null/non-dict `claudeAiOauth` (returns no token instead of
  raising); the live fetch refuses HTTP redirects so the Bearer token is never
  re-sent to a redirect target; and a connectivity/HTTP failure now prints a
  token-safe "usage API unavailable, using cache" notice (a missing-creds
  fallback stays quiet).

## 0.2.0 - 2026-07-27

Code-review fixes:

- Fix DST double-count: `active_seconds_between` / `_walk_active_forward` now
  subtract in UTC, so a spring-forward day is no longer over-counted by an hour.
- Reject overnight/zero-width `active_hours` windows (`end <= start`) with a
  `ConfigError` instead of silently forecasting `projected=inf` forever.
- Config loader reads `utf-8-sig` (strips BOM), rejects an indented first key,
  and converts `UnicodeDecodeError` to `ConfigError` (fail-loud contract).
- `resolve_source` warns on a malformed-payload fallback instead of silently
  masking it; not-wired/connectivity fallbacks stay quiet.
- Guard non-dict usage payloads and reject `bool` / negative `weeklyUsage` and
  negative `--used` with clear errors.
- Zero usage now projects to 0%, not `inf`, when no active time has elapsed.
- README: run commands now include the `cd plugins/ccu` step.
- Added 18 tests covering the above (DST, overnight window, BOM/indent/invalid
  UTF-8, invalid tz, blackout range, payload validation, `assess`, `parse_reset`).

## 0.1.0 - 2026-07-27

- Relocated the weekly-usage forecast from `personal-ops-scripts` into the `ccu`
  plugin. YAML-config-driven active hours + blackout days; usage sourced
  directly from the Anthropic usage API on the direct-TUI path (ccstatusline
  cache as fallback, ccstatusline-calls-script path still supported). Emits a
  projected finish percentage, per-active-day burn-down rate, and exhaustion
  date.
