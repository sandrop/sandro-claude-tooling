---
name: context-window-resume
version: 0.2.0
version-date: 2026-07-19
description: This skill should be used when Sandro runs "/context-window-resume", asks to "resume the session", "pick up where we left off", or wants to continue after a context clear. Reads the most recent ~/.claude/.sct/context-window-manager/<repo-slug>/CONTEXT-*.md file and reports git drift plus stale-reference verification before asking Sandro how to proceed, so the direction is evidence-based.
---

# Context Window Resume

Find and read the most recent `CONTEXT-*.md` handoff file, run a drift and stale-reference check against the current working tree, present that report, then ask Sandro how to proceed. The skill never auto-starts work.

## Triggers

- `/context-window-resume`.
- "resume the session", "pick up where we left off", "continue from the last handoff".
- Starting a fresh window after a `/context-window-handoff`.

## Process

### Step 1: Select the context file (the validator is authoritative)

Run the resume validator once and treat its `checks.context_file` field as the authoritative selected file:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/context-window-resume/validator.py"
```

The validator deterministically selects the most recent `~/.claude/.sct/context-window-manager/<repo-slug>/CONTEXT-*.md` and reports it as `checks.context_file`. Use that exact filename for the Step 2 summary and the Step 3 drift report so both reference the same file the validator reported. Do not run a separate `ls -t ... | head -1`; the validator's selection wins.

If `checks.context_file` is null, no context files exist (see Error Handling) and you stop. If other context files also exist, note them (see Multiple files). The full JSON payload from this same run (`{ok, checks:{context_file}, report:{meta, drift, refs}, errors}`) feeds Step 3.

### Step 2: Display the previous session summary

Read the file and display its Session Summary, Work In Progress, and Next Steps sections, along with which file is being resumed from and its timestamp. Highlight any pending questions or blockers, and restore awareness of any todo list.

### Step 3: Drift check

Reuse the validator JSON already captured in Step 1 (`{ok, checks:{context_file}, report:{meta, drift, refs}, errors}`); the `report` describes the same file named in `checks.context_file`. Parse `report` and surface:

- **Git drift** (from `report.drift`): the ahead/behind counts of the current HEAD versus the stored SHA, whether the stored SHA still resolves (`stored_resolvable`), whether the stored branch still exists (`branch_exists`), and the current branch versus the stored branch. When `is_git` is false (resume is running outside a git repo), say so and skip the git-specific numbers rather than inventing them; the `note` field explains the degraded case.
- **Reference verification** (from `report.refs`): for each cited reference, its `path`, whether it still exists (`exists`), and whether the cited line number is still in range (`line_ok`). Call out any path where `exists` is false or `line_ok` is false as a stale reference Sandro should re-check before relying on it.

If `ok` is false, surface the captured errors verbatim and stop before the Confirm Direction prompt.

### Step 4: Confirm Direction

Only after the Step 3 drift check has been displayed, ask Sandro how to proceed so the choice is evidence-based:

```
Ready to continue. What would you like to do?
1. Continue with the next steps listed above
2. Review completed work first
3. Start something different
```

Do not automatically start working; wait for Sandro's direction.

## Error Handling

If no context files exist:
```
No context files found in ~/.claude/.sct/context-window-manager/<repo-slug>/

To create one, run: /context-window-handoff
```

## Multiple files

If more than one context file exists, use the most recent and note the rest:
```
Found {N} context files. Using most recent: {filename}
Other available files:
- {list other files with dates}
```

## Rules

- The Confirm Direction menu is shown only after the drift check is displayed. Never ask how to proceed before Sandro has seen the drift and stale-reference evidence.
- Always show which file is being resumed from.
- Never auto-start work; wait for an explicit choice.
- Resume is read-only over the working tree and the CONTEXT file; it runs no git-mutating command.

## Version history

See [CHANGELOG.md](CHANGELOG.md).
