# Changelog

## 1.0.1 - 2026-10-25

- Docs: the `Skills to invoke on resume` example names `/cobtask-workflow:cobtask-tdd-plan` and `cobtask-workflow:cobtask-gitops ship` instead of the retired `/sp:tdd-plan` and `sp:git-commit-open-pr`. CCC-0387.

## 1.0.0 - 2026-10-23

- Breaking: rename the skills to `handoff` and `resume`, so they run as `/context-window-manager:handoff` and `/context-window-manager:resume`. The old `context-window-handoff` and `context-window-resume` names are gone, with no shim skills. The handoff validator and the `PreCompact` failsafe stub now print `/context-window-manager:resume`. Handoffs written before 1.0.0 still carry `/context-window-manager:context-window-resume` under `# Resume Command`; resume never parses that line, and a regression test now locks that in. Reinstall to pick up the new names: `claude plugin marketplace update sandro-claude-tooling-dev`, then `claude plugin install context-window-manager@sandro-claude-tooling-dev`. Bump the plugin manifest and the marketplace catalog to 1.0.0. SCT-0070.

## 0.14.0 - 2026-10-23

- Print the plugin-qualified resume command. `/context-window-handoff` now prints `/context-window-manager:context-window-resume --context-file <abspath>` (handoff skill 0.9.0), and the `PreCompact` failsafe stub writes the same qualified name under `# Resume Command`. Its fresh-subprocess test now anchors on the full `# Resume Command` line, so a revert to the bare name fails instead of slipping past the old substring check. Resume skill 0.12.2 carries the matching prose fix. Bump the plugin manifest and the marketplace catalog to 0.14.0 so the version-keyed runtime cache reloads. SCT-0069.

## 0.13.1 - 2026-10-10

- Ship the bare resume line to the runtime. SCT-0068 (PR #68) changed `/context-window-handoff` to print only `/context-window-resume --context-file <abspath>` (handoff skill 0.8.0), dropping the `cd <recorded-cwd> then` prefix, but merged with `plugin.json` still at 0.13.0, so the version-keyed runtime cache kept serving the old line. Bump the plugin manifest and the marketplace catalog to 0.13.1 so `marketplace update` + `install` load the fix. A new pre-push guard (`scripts/check_plugin_version_bump.py`) now blocks a push that changes a plugin's files without changing its `plugin.json` version.

## 0.13.0 - 2026-09-11

- Ship the copy-pasteable resume line to the runtime. SCT-0064 (PR #58) taught `/context-window-handoff` step 5 to print a paste-ready resume line and taught the pre-flight validator to emit `resume_abspath` (the session's `save_dir` joined with `filename`) and `resume_command` (`cd <recorded-cwd> then /context-window-resume --context-file <resume_abspath>`), so a resuming session bootstraps that exact handoff and restores the recorded working directory rather than falling to newest-in-repo discovery. That work merged on 0.12.0 without a version bump, so the version-keyed runtime cache never reloaded and the invoked copy kept printing the bare `/context-window-resume`. Bump the plugin manifest and the marketplace catalog to 0.13.0 so `marketplace update` + `install` invalidate the stale 0.12.0 cache key and the resume line reaches the runtime. No behavior change beyond the cache refresh; the resume-line code already shipped in the repo at 0.12.0. (0.12.0 carried SCT-0055 / #53 / SCT-0062 without its own CHANGELOG entry; this entry is the first since 0.11.0.)

## 0.11.0 - 2026-09-05

- Add a `PreCompact` failsafe hook so a compaction that lands before a manual `/context-window-handoff` still leaves a resumable snapshot. `hooks/hooks.json` registers `hooks/precompact_handoff.py` for the `PreCompact` event (`matcher: "*"`, covering both manual and auto triggers). The hook reuses the shared `session_capture` + `context_schema` helpers to write a `CONTEXT-*.md` with an honest deterministic narrative stub and the standard helper-rendered Session Metadata block; it wraps every step, always exits 0 with `{"continue": true}`, and never fails or blocks compaction. The registered command quotes `"${CLAUDE_PLUGIN_ROOT}"` so an install path with spaces cannot break the hook. To keep "never block compaction" true for latency as well as aborts, the hook shares one `session_capture.Deadline` across `collect_git`, `collect_context`, and `save_dir` (common 0.7.2), so a degraded git environment degrades within roughly one timeout total rather than three in a row; because that deadline only gates git/gh, `hooks.json` also sets a hard command `timeout` as the outer bound covering filesystem hangs. The save directory (canonical git-toplevel slug) is resolved before the budget is spent so an exhausted deadline cannot write the failsafe under a cwd-subdirectory slug resume never searches, and the snapshot is published atomically (dot-prefixed temp file + `os.replace`) so a partial write cannot leave a truncated newest file. To avoid demoting a fresh handoff (resume defaults to the newest file), the hook skips its write when a `CONTEXT-*.md` was touched within the last 30 minutes. A capture or write failure still returns `{"continue": true}` but is now surfaced as a non-blocking `stderr` diagnostic (failure type only) instead of being silent. Bump the plugin and marketplace catalog to 0.11.0 so the version-keyed runtime cache reloads and the new hook activates.

## 0.8.0 - 2026-08-26

- Refresh the runtime cache for the completed explicit sibling-worktree resume workflow. The resolver accepts only an explicit absolute or tilde path inside a registered sibling worktree's canonical save directory, exposes the basename, canonical selected path, and explicit-sibling marker, validates the handoff's recorded CWD inside the sibling toplevel trust boundary, and runs drift, reference, and continuity checks from that recorded CWD. Invalid recorded-CWD metadata, including unknown-user tildes and symlink loops, returns a stable contract error before selection or report processing; valid explicit sibling selections bypass both candidate pickers, and basename symlink escapes remain rejected.

## 0.6.0 - 2026-08-20

- Enforce one resume path contract for explicit and default-selected context files: the canonical final target must be an existing `CONTEXT-*.md` directly inside the current save directory. Basename, absolute, and tilde paths remain supported; empty, non-context, outside-directory, escaping-symlink, broken, and unresolvable targets are rejected or ignored as appropriate. Synchronize the plugin manifest and marketplace catalog at 0.6.0 so the package release refreshes the version-keyed runtime cache.

## 0.5.0 - 2026-08-19

- Bump the plugin manifest version so the version-keyed runtime cache refreshes and the resume skill's working-directory-restoration contract reaches the invoked copy. The prior skill-only bump (0.6.1 -> 0.7.0) left `plugin.json` at 0.4.0, so `marketplace update` + `install` never invalidated the stale 0.4.0 cache key. Also aligns the `.claude-plugin/marketplace.json` catalog entry (was 0.1.0) to the manifest version and adds an install-seam smoke test that resolves the skill through the real `${CLAUDE_PLUGIN_ROOT}` boundary so a skill-only bump can never again ship a stale plugin.
