---
name: context-window-handoff
version: 0.2.0
version-date: 2026-07-19
description: This skill should be used when Sandro runs "/context-window-handoff", asks to "save the session", "hand off the context window", or wants to clear and later resume the current session. Saves session state to ~/.claude/.sct/context-window-manager/<repo-slug>/CONTEXT-{timestamp}.md including a Session Metadata block with the git HEAD SHA (full and short) and branch for resume-time drift detection.
---

# Context Window Handoff

Save the current session state to a timestamped `CONTEXT-*.md` file so the context window can be cleared and resumed later via `/context-window-resume`. The skill decides what session content to capture; a shared helper renders the deterministic Session Metadata block (git SHA and branch) that resume reads to detect drift.

## Triggers

- `/context-window-handoff`.
- "save the session", "hand off the context window", "checkpoint before I clear context".
- Wrapping up a working session that Sandro intends to resume in a fresh window.

## Output File

Save to: `~/.claude/.sct/context-window-manager/<repo-slug>/CONTEXT-{YYYY-MM-DD-HH-MM-SS}.md`, where `<repo-slug>` is `<basename>-<hash>`: the basename of the current repo's git toplevel (or the cwd basename outside a git repo) plus a short path-derived hash so two repos sharing a basename never collide in the same directory. Resolve the directory and create it with the shared helper `context_schema.save_dir(Path.cwd(), create=True)` rather than hand-building the path. Generate the timestamp with `date +%Y-%m-%d-%H-%M-%S`. The validator reports the resolved directory as `checks.save_dir`.

## Process

1. **Pre-flight.** Run the validator:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/context-window-handoff/validator.py"
   ```
   It prints JSON `{ok, checks:{common_import, save_dir, is_git, head_resolvable}, errors}`. `common_import` confirms the shared context-window-common library is importable, `save_dir` is the resolved per-repo save directory, `is_git` reports whether cwd is a git repo, and `head_resolvable` reports whether HEAD resolves. If `ok` is false, surface the captured errors verbatim and stop.

2. **Generate the timestamp.** Run `date +%Y-%m-%d-%H-%M-%S` and build the output path.

3. **Capture the session sections.** Write the CONTEXT file with these required sections, in this order:

   - **Resume Command.** At the very top, the exact command to run in a fresh session: `/context-window-resume`.
   - **Session Summary.** High-level goal, current working directory, and branch name (if in a git repo).
   - **Completed Work.** Tasks and changes completed this session; files created or modified; key decisions made.
   - **Work In Progress.** The current task, what step it was on, and any pending questions or decisions.
   - **Next Steps.** Immediate next actions, blockers or dependencies, and open questions.
   - **Key Context.** Important technical details, relevant file paths and line numbers, error messages encountered, and any conventions being followed.
   - **Todo List State.** If there are active todos, list each with id, subject, status, and description.

4. **Append the Session Metadata block.** After the session sections, append a `### Session Metadata` block so resume can compute drift. Read the git values, guarded by a repo check:
   ```bash
   git rev-parse --is-inside-work-tree
   git rev-parse HEAD
   git rev-parse --short HEAD
   git branch --show-current
   ```
   Pass the values to `context_schema.render_metadata_block(sha, sha_short, branch)` and append its output to the CONTEXT file. When cwd is not a git repo (the repo check fails), render the block with `n/a` values via the same helper so the block is always present and well-formed. This block is exactly what context-window-resume reads to compute drift, so it must be written by the helper, not hand-formatted.

5. **Confirm and print the resume command.** Confirm the file was saved and display the exact command Sandro should run to resume:
   ```
   Context saved to: ~/.claude/.sct/context-window-manager/<repo-slug>/CONTEXT-<timestamp>.md

   To resume after clearing context, run:

   /context-window-resume
   ```

## Rules

- The Session Metadata block is always written through `context_schema.render_metadata_block`; never hand-format it, so its shape stays in lockstep with the resume-time parser.
- Never skip a required section silently. If a section has no content, write the heading with an explicit "None" rather than omitting it.
- Handoff is read-and-write of the CONTEXT file only. It does not commit, push, or run any git-mutating command.

## Version history

See [CHANGELOG.md](CHANGELOG.md).
