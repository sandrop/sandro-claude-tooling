# Changelog

## 0.2.0 - 2026-07-19

- Read the most recent `CONTEXT-*.md` from the home-scoped, per-repo path `~/.claude/.sct/context-window-manager/<repo-slug>/` instead of the retired project-local path. Packaged into the context-window-manager plugin; invokes its validator via `${CLAUDE_PLUGIN_ROOT}`.
- Genericized skill copy (second-person `you`, no personal names) for public distribution (pre-release).

## 0.1.0 - 2026-07-02

- Promote the context-window-resume command to a full skill. Before the Confirm Direction prompt, run a drift check (stored SHA vs current HEAD ahead/behind, branch-exists) and a stale-reference verification (cited paths + line numbers) via the shared context-window-common library. Degrades gracefully outside a git repo.
