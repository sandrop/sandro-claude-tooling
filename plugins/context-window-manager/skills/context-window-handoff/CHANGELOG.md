# Changelog

## 0.2.0 - 2026-07-19

- Move the save target from the project-local path to the home-scoped, per-repo path `~/.claude/.sct/context-window-manager/<repo-slug>/CONTEXT-*.md` (slug is now `<basename>-<hash>`, collision-proof across same-named repos). Validator now reports the resolved `save_dir`. Packaged into the context-window-manager plugin; invokes its validator via `${CLAUDE_PLUGIN_ROOT}`.
- Validator `_git` helper is now bounded by a timeout and degrades to a structured "not a git repo" result instead of hanging. Test conftests no longer put each skill's own dir on `sys.path` (only the shared lib), removing the identically-named-`validator.py` import foot-gun (review hardening, pre-release).
- Genericized skill copy (second-person `you`, no personal names) for public distribution (pre-release).

## 0.1.0 - 2026-07-02

- Promote the context-window-handoff command to a full skill. Add a Session Metadata block capturing git HEAD SHA (full + short) and branch into the CONTEXT file so context-window-resume can report drift. Deterministic logic lives in the shared context-window-common library.
