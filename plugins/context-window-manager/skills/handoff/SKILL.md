---
name: handoff
version: 1.2.0
version-date: 2026-11-11
description: This skill should be used when you run "/context-window-manager:handoff", ask to "save the session", "hand off the context window", or want to clear and later resume the current session. Saves session state to ~/.config/sct/context-window-manager/<repo-slug>/CONTEXT-{timestamp}-{suffix}.md (a random suffix keeps two same-second parallel handoffs from colliding) including a Session Metadata block that records the git HEAD SHA and branch, the working directory, the working-tree and remote state, the commits made, the task and PR being worked, and any ADRs recorded, so resume can verify rather than guess.
---

# Context Window Handoff

Save the current session state to a timestamped `CONTEXT-*.md` file so the context window can be cleared and resumed later via `/context-window-manager:resume`. The skill decides what session content to capture; a shared helper resolves and renders the deterministic Session Metadata block that resume reads back.

That block is the only part of the handoff a resuming session can verify. Prose can say anything; the metadata block is machine-written and machine-read, so anything that matters at resume time belongs in it. It records twenty values: the git SHA (full and short), branch, and working directory; the task id and file path; the commits this branch added, with subjects; the PR number and URL; a link to an external TDD plan when the task file does not already hold one; the working-tree state with staged, unstaged, untracked, and stash counts; the upstream name with ahead/behind and whether the branch is pushed; and the ADRs recorded this session.

## Triggers

- `/context-window-manager:handoff`.
- "save the session", "hand off the context window", "checkpoint before I clear context".
- Wrapping up a working session that you intend to resume in a fresh window.

## Output File

Save to: `~/.config/sct/context-window-manager/<repo-slug>/<filename>`, where `<repo-slug>` is `<basename>-<hash>`: the basename of the current repo's git toplevel (or the cwd basename outside a git repo) plus a short path-derived hash so two repos sharing a basename never collide in the same directory. `<filename>` is `CONTEXT-{YYYY-MM-DD-HH-MM-SS}-{6-hex-suffix}.md`, generated once by the pre-flight validator (`checks.filename`, via `context_schema.context_filename()`) so two parallel sessions handing off within the same second never overwrite each other's file. The validator resolves and creates the owner-only directory through `context_schema.save_dir(Path.cwd(), create=True)` and reports it as `checks.save_dir`; never hand-build or recreate the directory.

## Process

1. **Pre-flight and capture.** Run the validator once, passing the values only this session knows:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/handoff/validator.py" \
     [--cobtask-id <TASK-ID>] [--cobtask-path /abs/path/to/task.md] \
     [--tdd-plan-path /abs/path/to/plan.md] \
     [--adrs /abs/path/to/first-decision.md,/abs/path/to/second-decision.md] \
     [--vault-root /abs/path/to/task/vault]
   ```
   Every argument is optional; omit any you do not have. Do not invent values to fill them. An omitted argument renders as `n/a`, which is the honest answer; a guessed one poisons the resume. Pass `--cobtask-id` only when you actually know it; otherwise the validator derives it from the branch name, where a leading `<type>/<three-letter-prefix>-<four-digit-number>-<slug>` yields the upper-cased id. Pass `--vault-root` (or set `COBTASK_VAULT_ROOT`) to have the task file path resolved from `<vault>/items/**/<id>-*.md`. Pass `--adrs` only for decision records written during THIS session.

   It prints JSON `{ok, checks, metadata_block, errors}`:
   - `checks.common_import` confirms the shared context-window-common library is importable.
   - `checks.save_dir` is the resolved per-repo save directory, already created with owner-only permissions.
   - `checks.filename` is the freshly generated collision-resistant `CONTEXT-*.md` name for this handoff.
   - `checks.is_git` / `checks.head_resolvable` report whether cwd is a git repo and whether HEAD resolves.
   - `checks.capture` is a human-readable digest of what was recorded (`working_tree`, `staged`, `unstaged`, `untracked`, `stash_count`, `commit_count`, `remote_upstream`, `remote_pushed`, `cobtask_id`, `pr_number`, `adr_count`) for surfacing in step 5.
   - `checks.retention` is the once/day retention summary (`files_moved`, `dirs_moved`, or `skipped: already-pruned-today`): the pre-flight archives `CONTEXT-*.md` handoffs older than `SCT_CWM_RETENTION_DAYS` days (default 30) into the terminal `zArchives/` sibling under the save root, at most once per calendar day. A bad `SCT_CWM_RETENTION_DAYS` value surfaces loudly as `checks.retention_error` and never aborts the handoff. Move-only; nothing is ever deleted.
   - `metadata_block` is the finished, ready-to-append Session Metadata block.

   If `ok` is false, surface the captured errors verbatim and stop.

   The validator resolves the git values, working-tree counts, stash depth, upstream state, and commit list itself, and looks up the PR with `gh pr view --json number,url`. Every probe degrades: no git repo, no remote, no PR, no `gh` binary, and no task file each render as `n/a` rather than failing the handoff. Do not run these commands yourself and do not hand-assemble their output.

2. **Build the output path.** Join `checks.save_dir` and `checks.filename` from Step 1. The validator has already created `checks.save_dir` with owner-only permissions; do not call `mkdir` or recreate it. Do not regenerate the filename by hand (e.g. a fresh `date` call) — the pre-flight validator already produced a collision-resistant name; reusing it keeps the same handoff from getting two different names across steps.

3. **Capture the session sections.** Write the CONTEXT file with these required sections, in this order:

   - **Resume Command.** At the very top, the exact command to run in a fresh session: `/context-window-manager:resume`.
   - **Session Summary.** High-level goal, current working directory, and branch name (if in a git repo).
   - **Completed Work.** Tasks and changes completed this session; files created or modified; key decisions made.
   - **Work In Progress.** The current task, what step it was on, and any pending questions or decisions.
   - **Next Steps.** Immediate next actions, blockers or dependencies, and open questions.
   - **Key Context.** Important technical details, relevant file paths and line numbers, error messages encountered, and any conventions being followed. Never cite a versioned plugin cache path (`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/...`); name the skill or slash command, or write the command relative to `${CLAUDE_PLUGIN_ROOT}`, because an upgrade leaves the old version directory in place.
   - **Traps / dead ends.** Approaches already tried and abandoned, and why; do-NOT-touch zones the resuming session should leave alone. Saves a resume from re-walking a path this session already ruled out.
   - **Skills to invoke on resume.** The named skills or slash commands the resuming session should run to continue (e.g. `/cobtask-workflow:cobtask-tdd-plan`, `cobtask-workflow:cobtask-gitops ship`), in the order they apply. Name the skill, not a versioned plugin cache path to its launcher.
   - **Todo List State.** If there are active todos, list each with id, subject, status, and description.

4. **Append the Session Metadata block.** Append the `metadata_block` string from Step 1 to the end of the CONTEXT file, verbatim. Do not re-render it, reorder its keys, drop `n/a` lines, or edit any value. It is already complete and already helper-rendered; this block is exactly what the resume skill parses, so any hand-editing breaks the contract between the two skills.

5. **Confirm and print the resume command.** Confirm the file was saved, show a one-line digest of what was captured (drawn from `checks.capture`), and display the single copy-pasteable resume line. If `checks.retention_error` is present, surface it verbatim to the user on its own line (e.g. `Retention skipped: <error>`) before the digest: the prune is non-blocking by design, so `ok` stays true and the handoff still succeeds, but a bad `SCT_CWM_RETENTION_DAYS` or a failed archive move must be shown, not swallowed. Do not abort the handoff for it.
   ```
   Context saved to: <checks.resume_abspath>
   Captured: branch <branch> @ <short-sha>, tree <working_tree> (<staged>/<unstaged>/<untracked>), <commit_count> commit(s), task <cobtask_id>, PR <pr_number>

   To resume, paste this to Claude in a fresh session:

   <checks.resume_command>
   ```
   The saved-path line is `checks.resume_abspath` and the paste line is `checks.resume_command`, both rendered by the pre-flight validator (`resume_abspath` = the session's `save_dir` joined with `filename`; `resume_command` = `/context-window-manager:resume --context-file <resume_abspath> and continue`), so the printed line is deterministic rather than reassembled here. The `--context-file` selector is what makes resume bootstrap this exact handoff and restore the recorded working directory from it, rather than falling to its no-argument discovery path, which would pick the newest handoff in the current repo. The trailing `and continue` tells resume to start the Next Steps without its menu when the drift check is clean; on any drift it still shows the menu. Call out a dirty working tree explicitly. A resuming session that believes the tree was clean will misread everything that follows.

6. **Spawn the next session.** After Step 5 has printed the paste line, run:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/handoff/spawn.py" --resume-command "$(cat <<'CWM_RESUME'
   <checks.resume_command>
   CWM_RESUME
   )"
   ```
   Put `checks.resume_command` verbatim on its own line between the markers, and start the closing `CWM_RESUME` line with no leading spaces. Never splice the line into a double-quoted or bare argument instead: it holds the repo directory name, and the quoted `'CWM_RESUME'` delimiter is what keeps the shell from running a `$(...)`, backtick or `$` in that name. That protection holds only while the value is one line. A repo directory name can contain a line break, and a line break lets the value put its own `CWM_RESUME` line inside the heredoc, end it early, and run whatever follows as shell. So if `checks.resume_command` contains a line break, do not run the command: print `Spawn skipped: the resume line spans more than one line` and skip the rest of Step 6.

   Inside cmux, this opens the next workspace right after the current one in its group. It boots claude there with your `--model` and `--effort`, runs `/rename <name>`, waits for `Session renamed to: <name>`, and sends the resume line, so the new session starts the Next Steps with no menu. If a workspace with the target name already sits in the group, spawn reuses it and sends only the resume line. The current workspace is never changed. The script always exits 0 and prints JSON `{status, name, ref, reason, choices, notices}`. Print one line for the status:
   - `spawned`: `Spawned <name> (<ref>)`.
   - `reused`: `Resumed in existing <name>`.
   - `skipped`: `Spawn skipped: <reason>`. The handoff still succeeded and the paste line above still works.
   - `needs-choice`: ask one AskUserQuestion with one option per `choices` entry, labeled `<suffix> (<color>)`, then rerun the same command with `--stage <suffix>` added after the closing `)"`.

   Show each `notices` line verbatim. A rename not confirmed within 30s skips the follow-ups with a notice telling you to paste the line by hand.

   Naming. With no config, a title ending in `-N` becomes `-(N+1)`, any other title gets `-2`, and the new sidebar inherits the caller's color. An optional `~/.config/sct/context-window-manager/config.toml` switches to stage advance:
   ```toml
   [[spawn.stages]]
   suffix = "A-plan"
   color = "blue"
   [[spawn.stages]]
   suffix = "B-implement"
   color = "yellow"
   sidebar = "#7D6608"
   [[spawn.stages]]
   suffix = "C-review"
   color = "green"
   ```
   A title ending in a stage suffix advances to the next stage's suffix and color, and the new session also runs `/color <color>`. `color` uses claude's `/color` names and also sets the cmux sidebar. cmux's sidebar palette has no `yellow`, so a stage can set `sidebar` (`#RRGGBB` or a cmux color name) to override the sidebar color alone. A sidebar color cmux rejects is reported as a notice and does not stop the spawn. The last stage, or a title matching no stage, returns `needs-choice`. A malformed config skips the spawn with a reason naming the file.

   Opt out with `SCT_CWM_SPAWN=0`. Skip Step 6 entirely when you ask for no spawn. Outside cmux (no `CMUX_WORKSPACE_ID`) the step reports `skipped` and changes nothing.

## Rules

- The Session Metadata block is always written through `context_schema.render_metadata_block`, which the validator calls for you; never hand-format it, so its shape stays in lockstep with the resume-time parser.
- Never invent a metadata value. An unknown field renders as `n/a`, and `n/a` is a useful answer: it tells a resuming session "this was not established", which a plausible-looking guess would hide.
- Never skip a required section silently. If a section has no content, write the heading with an explicit "None" rather than omitting it.
- Handoff is read-and-write of the CONTEXT file only. It does not commit, push, stash, or run any git-mutating command. Every git call behind the capture is a read (`rev-parse`, `status`, `log`, `stash list`, `rev-list`, `remote`), and the `gh` call is `pr view`. The only exception is Step 6's `cmux` calls, which create or message a sibling workspace and never touch the CONTEXT file or the current workspace.
- The output filename always comes from the pre-flight validator's `checks.filename` (`context_schema.context_filename()`), never a hand-built `date` call, so two parallel sessions handing off within the same second never silently overwrite each other's file.

## PreCompact failsafe

If a compaction (autocompaction or `/compact`) lands before you run this skill, the plugin's `PreCompact` hook (`hooks/precompact_handoff.py`) writes a failsafe `CONTEXT-*.md` to the same save directory. The hook cannot run the model, so its narrative sections are an honest auto-capture stub; the Session Metadata block is rendered through the same `context_schema.render_metadata_block` helper this skill uses, so `/context-window-manager:resume` reads it back identically. The hook never fails or blocks compaction. A manual handoff still produces the richer, model-authored snapshot; the failsafe only guarantees a resumable floor.

## Version history

See [CHANGELOG.md](CHANGELOG.md).
