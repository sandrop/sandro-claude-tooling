# ccu

Forecast weekly Claude Code quota usage. Given current weekly usage and the
next reset time, `ccu` projects a **finish percentage**, a **per-active-day
burn-down rate**, and a **projected exhaustion date**, weighting elapsed time by
a configured active window (working hours minus blackout days) rather than raw
wall-clock.

## Why active-window weighting

Raw wall-clock projection assumes you burn quota evenly around the clock. You
don't. `ccu` only counts hours inside `active_hours` and skips `blackouts`
(travel, vacation), so the projection reflects the pace you actually set during
the hours you work.

## Config

### Default location

When you run `ccu` without `--config`, it reads
`~/.claude/.sct/ccu/config/ccu.yaml` (or `$CLAUDE_CONFIG_DIR/.sct/ccu/config/ccu.yaml`
when that env var is set). On first run the CLI creates that dir but seeds
nothing; to customize, create `ccu.yaml` there using the shape below. A missing
`ccu.yaml` is a soft miss (it falls back to built-in defaults); a malformed one
fails loud, the same as an explicit `--config`. An explicit `--config <path>`
always overrides the default.

The loader is a stdlib-only flat-subset YAML parser (no PyYAML); only the
documented shape is supported.

```yaml
timezone: America/New_York     # IANA tz for the window + blackout dates
active_hours:
  start: "09:00"               # 24h HH:MM
  end: "23:00"
blackouts:                     # days fully excluded
  - "2026-08-01"                       # single day
  - { start: "2026-08-10", end: "2026-08-17" }   # inclusive range
```

Missing keys fall back to defaults: `UTC`, a whole-day active window
(`00:00`–`23:59`), and no blackouts.

## Running

Run these from the plugin directory (`cd` there first); the paths below are
relative to `plugins/ccu/`:

```bash
cd plugins/ccu

# Explicit inputs (no usage source needed):
python3 cli/ccu_forecast.py --used 56 --reset "2026-08-01T05:59:00Z"

# Source usage from the ccstatusline cache:
python3 cli/ccu_forecast.py --config myccu.yaml --prefer-cache
```

### Usage sources (precedence)

1. **Explicit** `--used` / `--reset` — always win.
2. **Anthropic usage API** (direct-TUI path) — the default. Decoupled from
   ccstatusline; see live verification below.
3. **ccstatusline cache** (`~/.cache/ccstatusline/usage.json`) — used with
   `--prefer-cache`, or automatically when the API path is unavailable.

The `ccstatusline`-calls-this-script path still works: ccstatusline can invoke
the CLI directly. Only the direct-TUI path is ccstatusline-independent.

## Live verification

The direct-API path is live: `usage_source._default_anthropic_fetcher` calls
`GET https://api.anthropic.com/api/oauth/usage` with an `Authorization: Bearer
<token>` header plus `anthropic-beta: oauth-2025-04-20` and a 5s timeout, and
remaps the response's `seven_day` bucket (`utilization`, `resets_at`) into the
seam's `weeklyUsage` / `weeklyResetAt` keys.

The OAuth token resolves the same way ccstatusline does: on macOS the Keychain
service `Claude Code-credentials` first, then `$CLAUDE_CONFIG_DIR/.credentials.json`
(default `~/.claude/.credentials.json`); other platforms read the file only. No
secrets are stored by this plugin. Log in to Claude Code, then smoke-test:

```bash
python3 cli/ccu_forecast.py --config myccu.yaml   # no --prefer-cache
```

A live-sourced run prints a forecast with no ccstatusline stale-cache warning.
When no token is available (or the endpoint is unreachable) the direct path
falls back to the ccstatusline cache; use `--prefer-cache` or pass
`--used`/`--reset` to source usage explicitly.

## Tests

Run from the repo root so the PR gate includes root tests and this plugin:

```bash
./run_tests.sh ccu
```

A direct `pytest plugins/ccu/` run omits mandatory root tests and is not the PR gate.
