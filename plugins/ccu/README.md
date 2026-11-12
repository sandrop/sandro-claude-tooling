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

# Source usage from the tap file, else the ccstatusline cache (the default):
python3 cli/ccu_forecast.py --config myccu.yaml
```

### Usage sources (precedence)

1. **Explicit** `--used` / `--reset` — always win.
2. **Tap file**: `~/.claude/.sct/ccu/usage.json` (`$CLAUDE_CONFIG_DIR`-aware),
   written by `cli/ccu_tap.py` on every status line render. See
   [Status line tap](#status-line-tap).
3. **ccstatusline cache**: the newest `~/.cache/ccstatusline/usage*.json`
   (the legacy `usage.json` is still read), used only when no tap file exists.

A tap file or cache older than 15 minutes prints a staleness warning that names
the file. A stale tap file still wins over a fresh cache, so if you remove the
tap from `statusLine`, the warning shows which file ccu read.

ccu reads no credentials and makes no network call. `--prefer-cache` is still
accepted and changes nothing.

### Status line tap

Claude Code pipes a JSON object to the `statusLine` command on every render.
For Claude.ai Pro and Max subscribers it carries
`rate_limits.seven_day.used_percentage` (0-100) and `resets_at` (Unix epoch
seconds), after the session's first API response. `ccu_tap.py` records that
object as:

```json
{"captured_at": 1763000000.0, "rate_limits": {"seven_day": {"used_percentage": 17.0, "resets_at": 1763500000}}}
```

The write is atomic. Bad input, a missing `seven_day` window, or an unwritable
`.sct` dir leave the previous file in place, and the status line keeps working.

Add the tap to `statusLine` in `~/.claude/settings.json` by hand. ccu never
edits that file. With no other status line, the tap prints nothing:

```json
"statusLine": { "type": "command", "command": "python3 <path>/ccu_tap.py" }
```

To keep an existing status line, put the tap in front of it after `--`. The tap
forwards stdin byte for byte and passes through the command's output and exit
code:

```json
"statusLine": { "type": "command", "command": "python3 <path>/ccu_tap.py -- npx -y ccstatusline@latest" }
```

`<path>` is the plugin's `cli/` dir. For an installed plugin that is a
versioned cache path (`~/.claude/plugins/cache/<marketplace>/ccu/<version>/cli`),
so point the command at the new path after each ccu upgrade.

#### As a ccstatusline widget

If ccstatusline already owns your `statusLine`, you can leave `settings.json`
alone and run the tap from a ccstatusline `custom-command` widget instead.
ccstatusline pipes the same statusline JSON to the widget's command. Wrap the
forecast so the widget records usage and shows the projected finish in one
step. In `~/.config/ccstatusline/settings.json`:

```json
{
  "type": "custom-command",
  "commandPath": "python3 <path>/ccu_tap.py -- python3 <path>/ccu_forecast.py | awk '/Projected finish/{print $3}'"
}
```

The tap writes the file before it runs the forecast, so the widget shows the
number from the current render (for example `78.0%`).

Do not wrap `npx -y ccstatusline@latest` (or any other ccstatusline command)
inside a ccstatusline widget. ccstatusline would render the widget, the widget
would run ccstatusline, and every render would start another nested render.
Wrap ccstatusline only from Claude Code's `statusLine`, as shown above.

## Tests

Run from the repo root so the PR gate includes root tests and this plugin:

```bash
./run_tests.sh ccu
```

A direct `pytest plugins/ccu/` run omits mandatory root tests and is not the PR gate.
