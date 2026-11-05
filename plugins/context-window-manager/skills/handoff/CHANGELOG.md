# Changelog

## 1.0.1 - 2026-10-25

- Docs: the `Skills to invoke on resume` example names `/cobtask-workflow:cobtask-tdd-plan` and `cobtask-workflow:cobtask-gitops ship` instead of the retired `/sp:tdd-plan` and `sp:git-commit-open-pr`. CCC-0387.

## 1.0.0 - 2026-10-23

- Breaking: rename the skill from `context-window-handoff` to `handoff` (`/context-window-manager:handoff`). The pre-flight validator now renders `resume_command` as `/context-window-manager:resume --context-file <resume_abspath>`, and the `SKILL.md` prose and trigger use the new names. SCT-0070.

## 0.9.0 - 2026-10-23

- Qualify the copy-pasteable resume line with the plugin namespace. The pre-flight validator now renders `resume_command` as `/context-window-manager:context-window-resume --context-file <resume_abspath>`, so the pasted line resolves to this plugin's skill even when another installed plugin or a user-scope skill shares the short name. The `SKILL.md` prose (overview, Resume Command section, Step 5 contract and the PreCompact failsafe note) names the qualified command too. `test_reports_resume_command_as_bare_slash_paste` is replaced by `test_reports_resume_command_as_qualified_slash_paste`. SCT-0069.

## 0.8.0 - 2026-10-09

- Drop the `cd`-prefix from the copy-pasteable resume line. The pre-flight validator now renders `resume_command` as the bare `/context-window-resume --context-file <resume_abspath>` unconditionally, instead of branching on `meta.cwd` to prefix `cd <recorded-cwd> then`. The prefix was redundant: `/context-window-resume`'s own bootstrap already restores the recorded working directory from the context file (SCT-0040, hardened SCT-0058), so the `cd` only duplicated that restore and added noise to the pasted prompt. Step 5 of `SKILL.md` keeps printing `checks.resume_command` verbatim; its prose drops the "paste as prompt not shell / Claude reads the cd intent" explanation that only existed to justify the prefix, and keeps the point that `--context-file` is what makes resume restore the recorded cwd. Scope is the handoff validator render only; resume-side behavior and the `PreCompact` hook are untouched. The former `test_reports_resume_command_as_cd_then_slash_paste` is replaced by `test_reports_resume_command_as_bare_slash_paste`; `test_resume_command_is_child_of_save_dir` and `test_reports_resume_abspath_joining_save_dir_and_filename` still pass unchanged. SCT-0068.

## 0.7.0 - 2026-09-10

- Print a single copy-pasteable resume line at handoff time. The pre-flight validator now emits two new deterministic `checks` fields: `resume_abspath` (the session's `save_dir` joined with the collision-resistant `filename`, so the finished absolute handoff path is resolved in one place) and `resume_command` (`cd <recorded-cwd> then /context-window-resume --context-file <resume_abspath>`, degrading to the bare `/context-window-resume --context-file <resume_abspath>` when `meta.cwd` is falsy). The `--context-file` selector is deliberate: it makes resume bootstrap that exact handoff and restore the recorded working directory from it, rather than falling to its no-argument discovery path (which selects the newest handoff in the current repo). Step 5 of `SKILL.md` now prints the saved path from `resume_abspath` and the paste line from `resume_command`, instead of reassembling either at write time. The line is pasted as a prompt to Claude, not a shell command: Claude Code only parses a slash command at message start, so Claude reads the `cd` intent and runs `/context-window-resume --context-file <resume_abspath>`, whose own bootstrap performs the real directory restore. Scope is Step 5 only; the `PreCompact` failsafe hook is untouched. Covered by `test_reports_resume_abspath_joining_save_dir_and_filename`, `test_reports_resume_command_as_cd_then_slash_paste`, and `test_resume_command_is_child_of_save_dir` in `test_handoff_validator.py`.

## 0.6.0 - 2026-09-07

- Wire an opportunistic once/day retention prune into the pre-flight validator. After the save directory resolves, the validator calls `retention.maybe_prune` (new `lib/context-window-common/retention.py`), which archives `CONTEXT-*.md` handoffs older than `SCT_CWM_RETENTION_DAYS` days (default 30) into the terminal `zArchives/` sibling under the save root, gated to at most once per calendar day via a `.last_prune` marker. The move is `shutil.move` only, never a delete, and `zArchives/` is never re-scanned or auto-pruned. A whole per-repo-slug dir is archived only when every `CONTEXT-*.md` under it is stale; a dir with any live handoff moves only its stale files. An invalid `SCT_CWM_RETENTION_DAYS` surfaces loudly as `checks.retention_error` and never aborts the handoff; the summary is reported as `checks.retention`. `context-window-resume` already excludes `zArchives/` structurally (single-dir non-recursive glob, real-slug-only cross-worktree scan), locked by `test_resume_excludes_archive.py`. Covered by `test_handoff_prune.py`, `test_handoff_prune_smoke.py`, and the library's `test_retention.py`.
- Make the non-blocking retention failure loud at the surface. Step 5 now instructs the skill to print `checks.retention_error` verbatim to the user (on its own line, before the digest) when present, so a bad `SCT_CWM_RETENTION_DAYS` or a failed archive move is shown rather than swallowed behind an `ok: true` pre-flight. The prune stays non-blocking: the handoff still succeeds and is never aborted for a retention error.
- Pass the freshly created save dir to `retention.maybe_prune` as `keep_dir`, so the pre-flight never relocates this session's own handoff directory. Previously a repo idle longer than the retention window could have its save dir moved into `zArchives/` between the validator reporting it and the skill writing to it, failing the handoff. The prune tests are now hermetic against a developer-exported `SCT_CWM_RETENTION_DAYS`: the `run_handoff_validator` fixture and the standalone smoke test drop the inherited value unless a test sets it explicitly.

## 0.5.1 - 2026-09-05

- Add two prose sections to the Step 3 handoff-capture list: "Traps / dead ends" (approaches already tried and abandoned, and do-NOT-touch zones) and "Skills to invoke on resume" (the named skills or slash commands the resuming session should run next). Both inherit the existing explicit-None rule, so an empty section is written with a "None" heading rather than dropped. Prose-only change to `SKILL.md`; the machine-rendered Session Metadata block and the `context-window-resume` parser contract are untouched. Guarded by `test_handoff_template_sections.py`.

## 0.5.0 - 2026-09-05

- Add a plugin-level `PreCompact` failsafe hook (`hooks/precompact_handoff.py`). When a compaction (autocompaction or `/compact`) lands before a manual handoff, the hook writes a resumable `CONTEXT-*.md` to the same save directory, anchored on the PreCompact payload's `cwd` (falling back to the process cwd). Its narrative sections are an honest deterministic stub; the Session Metadata block is rendered through the shared `context_schema.render_metadata_block`, so resume parses it identically to a manual handoff. The hook wraps every step and always exits 0 with `{"continue": true}`, so it can never fail or block compaction, and it degrades honestly (each unknown field renders `n/a`) outside a git repo or on malformed/empty stdin.

## 0.4.1 - 2026-09-01

- Move the save target to `~/.config/sct/context-window-manager/<repo-slug>/` instead of the retired `~/.claude/.sct/context-window-manager/`. The pre-flight now creates the per-repo directory through `context_schema.save_dir(create=True)`, which enforces owner-only permissions before the skill writes a handoff (common 0.7.1).

## 0.4.0 - 2026-08-08

- Expand the Session Metadata block from 3 fields to 20. Alongside the existing `git_sha` / `git_sha_short` / `git_branch` it now records `cwd`, `cobtask_id` + `cobtask_path`, `commits` (short sha plus subject, scoped to the branch's divergence from its base and capped at 20), `pr_number` + `pr_url`, a conditional `tdd_plan_path`, `working_tree` with `staged` / `unstaged` / `untracked` / `stash_count`, `remote_upstream` + `remote_ahead` / `remote_behind` / `remote_pushed`, and `adrs`. The working-tree fields close the gap where a HEAD-only block handed off a dirty tree looking clean.
- The validator now resolves the whole block and returns it as `metadata_block`, ready to append verbatim; the skill no longer runs git itself. It also reports a `checks.capture` digest for surfacing. New optional arguments carry the values only the live session knows: `--cobtask-id`, `--cobtask-path`, `--tdd-plan-path`, `--adrs`, `--vault-root`.
- `tdd_plan_path` is recorded only when the resolved task file does not already hold a populated `## TDD Plan`, so the link never duplicates a plan that is already in the task.
- Every probe degrades to an explicit `n/a`: no git repo, no remote, no PR, no `gh` binary, no task file. Nothing is fabricated and no probe can fail the handoff.

## 0.3.0 - 2026-08-08

- Collision-resistant output filenames: the pre-flight validator now generates the `CONTEXT-*.md` filename (timestamp plus a random 6-hex-char suffix, via `context_schema.context_filename()`) and reports it as `checks.filename`, replacing the freehand `date +%Y-%m-%d-%H-%M-%S` step. Two parallel sessions handing off within the same second no longer silently overwrite each other's handoff file.

## 0.2.0 - 2026-07-19

- Move the save target from the project-local path to the home-scoped, per-repo path `~/.claude/.sct/context-window-manager/<repo-slug>/CONTEXT-*.md` (slug is now `<basename>-<hash>`, collision-proof across same-named repos). Validator now reports the resolved `save_dir`. Packaged into the context-window-manager plugin; invokes its validator via `${CLAUDE_PLUGIN_ROOT}`.
- Validator `_git` helper is now bounded by a timeout and degrades to a structured "not a git repo" result instead of hanging. Test conftests no longer put each skill's own dir on `sys.path` (only the shared lib), removing the identically-named-`validator.py` import foot-gun (review hardening, pre-release).
- Genericized skill copy (second-person `you`, no personal names) for public distribution (pre-release).

## 0.1.0 - 2026-07-02

- Promote the context-window-handoff command to a full skill. Add a Session Metadata block capturing git HEAD SHA (full + short) and branch into the CONTEXT file so context-window-resume can report drift. Deterministic logic lives in the shared context-window-common library.
