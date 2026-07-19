# Changelog

## 0.2.0 - 2026-07-19

- Add the save-path contract: `SAVE_ROOT` (`~/.claude/.sct/context-window-manager`, overridable via `CWM_SAVE_ROOT`), `repo_slug(cwd)`, and `save_dir(cwd, create=False)`. Remove the retired `CONTEXT_DIR` constant. Packaged into the context-window-manager plugin.
- `repo_slug` now returns `<basename>-<hash>` (8-hex path-derived suffix) so two repos sharing a directory basename no longer collide in the same save dir. Its git call is bounded by `_GIT_TIMEOUT` and degrades to the cwd fallback on hang/missing-git. `compute_drift` params are now type-hinted (review hardening, pre-release).
- Relocated from `skills/context-window-common/` to `lib/context-window-common/` so it is no longer discovered as an invokable slash command; it is a shared library, not a skill. `SKILL.md` renamed to `README.md`. Consumer validators/conftests updated to import from the new path. Genericized copy (pre-release).

## 0.1.0 - 2026-07-02

- Initial extraction. context_schema, git_drift, verify_refs helpers with tests, shared by context-window-handoff and context-window-resume.
