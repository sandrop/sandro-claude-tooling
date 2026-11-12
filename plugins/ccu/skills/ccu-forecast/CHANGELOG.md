# Changelog

## 0.4.0 - 2026-11-12

- Ships with ccu 0.7.0 (SCT-0080): the precedence section names the statusline
  tap file first, then the ccstatusline cache. The skill relays the CLI's tap
  setup error and never edits `settings.json`.

## 0.3.2 - 2026-11-11

- Ships with ccu 0.6.0 (SCT-0079): usage comes from the newest ccstatusline
  `usage*.json` by default, and ccu reads no credentials. Step 2 and the
  precedence section drop the stale "API fetch is not wired yet" text.

## 0.3.1 - 2026-08-25

- Doc consistency: step 1 no longer says the CLI seeds a starter
  `ccu.example.yaml` on first run (that seeding was removed in ccu 0.5.0); the
  dir is still scaffolded, a missing `ccu.yaml` still soft-misses to defaults.

## 0.3.0 - 2026-07-30

- Document the new default config location: step 1 now describes the
  `~/.claude/.sct/ccu/config/ccu.yaml` default (`$CLAUDE_CONFIG_DIR`-aware,
  soft-miss to defaults, first-run scaffold + seed) alongside the explicit
  `--config` override. No change to the skill's CLI surface.

## 0.2.0 - 2026-07-28

- Live Anthropic usage-API path is now wired: the direct-TUI source pulls weekly
  usage from the real endpoint instead of falling back to the ccstatusline cache
  every time. No change to the skill's CLI surface.

## 0.1.0 - 2026-07-27

- Initial ccu-forecast skill: runs the ccu forecast CLI to project weekly finish
  percentage, per-active-day burn-down rate, and exhaustion date from a YAML
  config.
