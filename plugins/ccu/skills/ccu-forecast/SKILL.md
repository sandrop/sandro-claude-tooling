---
name: ccu-forecast
description: This skill should be used when Sandro asks to "forecast my Claude usage", "how much weekly quota is left", "am I on pace to blow my weekly limit", "when will I run out of Claude quota", or invokes the ccu plugin. Runs the ccu forecast CLI to project a weekly finish percentage, per-active-day burn-down rate, and exhaustion date, weighting elapsed time by configured active hours and blackout days.
version: 0.3.1
version-date: 2026-08-25
---

# ccu Weekly-Usage Forecast

Project weekly Claude Code quota usage from current usage and the next reset,
weighting elapsed time by a configured active window (working hours minus
blackout days). Emits a projected finish percentage, a per-active-day burn-down
rate, and a projected exhaustion date.

## Triggers

- "forecast my Claude usage", "am I on pace this week", "when do I run out of quota".
- Direct invocation of the ccu plugin.

## Process

1. **Locate config.** An explicit `--config <path>` always wins. Otherwise the
   CLI reads the default config at `~/.claude/.sct/ccu/config/ccu.yaml`
   (`$CLAUDE_CONFIG_DIR`-aware), scaffolding that dir on first run but seeding
   nothing; a missing `ccu.yaml` there is a soft miss that falls back to
   built-in defaults. The config sets `timezone`, `active_hours` (24h
   `start`/`end`), and `blackouts` (single days or inclusive ranges).

2. **Run the CLI** from the plugin root:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/cli/ccu_forecast.py" --config <path/to/ccu.yaml>
   ```
   Add `--prefer-cache` to source usage from the ccstatusline cache, or pass
   `--used <pct> --reset <iso-or-display>` to supply both explicitly (these win
   over any usage source).

3. **Relay the output** verbatim: run time, reset time, current usage,
   projected finish, burn-down rate, exhaustion date (or "not before reset"),
   and the one-line pace assessment.

## Usage-source precedence

Explicit `--used`/`--reset` > Anthropic usage API (direct-TUI path) >
ccstatusline cache. The API fetch is not wired yet (`NotImplementedError`), so
the direct path currently falls back to the cache; use `--prefer-cache` or
explicit values until the fetcher lands. See the plugin README's live
verification note.

## Rules

- Never fabricate usage numbers. If no usage source resolves and no explicit
  `--used`/`--reset` is given, surface the CLI error verbatim and stop.
- The CLI is the only compute path; this skill never re-implements the math.

## Version history

See [CHANGELOG.md](CHANGELOG.md).
