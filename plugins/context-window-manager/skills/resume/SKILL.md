---
name: resume
version: 1.0.0
version-date: 2026-10-23
description: Use when resuming a session after /context-window-manager:handoff, continuing after a context clear, or restoring a task whose active working directory may differ from the recorded handoff CWD.
---

# Context Window Resume

Find and read the most recent `CONTEXT-*.md` handoff file, restore its recorded working directory, run drift and stale-reference checks there, present that report, then ask you how to proceed. The skill never auto-starts work.

## Triggers

- `/context-window-manager:resume`.
- "resume the session", "pick up where we left off", "continue from the last handoff".
- Starting a fresh window after a `/context-window-manager:handoff`.

## Process

### Step 0: Bootstrap the recorded code CWD (explicit `--context-file` only)

Run this step **only** when the user supplied an explicit `--context-file` (an absolute or tilde path to a handoff that may live in another repository). With no explicit path, skip Step 0 entirely and run Step 1 unchanged, exactly as before.

An explicit cross-repository handoff records its own code working directory in `Session Metadata.cwd`. The Step 1 validator resolves candidates relative to the *current* repo's `save_dir`, so a handoff from an unrelated repo B, resumed from repo A, is rejected before its CWD is ever restored. This step resolves and restores that recorded directory first, then runs the existing validator unchanged from there. It treats the handoff and its recorded CWD as untrusted data: the resolved path is bound by `python -c` and command substitution and never interpolated into shell syntax, mirroring the Step 3 `# cwd-bind-contract` block.

Bind the explicit path to `explicit_context_file`, then run:

```bash
# cwd-bootstrap-contract
bootstrap_report="$(mktemp "${TMPDIR:-/tmp}/cwm-resume-bootstrap.XXXXXX")"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/resume/bootstrap_cwd.py" --context-file "$explicit_context_file" > "$bootstrap_report"
boot_ok="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["ok"]).lower())' "$bootstrap_report")"
boot_do="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["bootstrap"]).lower())' "$bootstrap_report")"
boot_cwd="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["cwd"] or "")' "$bootstrap_report")"
python3 -c 'import json,sys; [print("warning:", w) for w in json.load(open(sys.argv[1]))["warnings"]]' "$bootstrap_report"
if [ "$boot_ok" != "true" ]; then
  python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["errors"]))' "$bootstrap_report" >&2
  exit 1
fi
if [ "$boot_do" = "true" ]; then
  if ! cd -- "$boot_cwd"; then
    printf 'could not restore recorded code working directory: %s\n' "$boot_cwd" >&2
    exit 1
  fi
  restored_boot_cwd="$(pwd -P)"
  if [ "$restored_boot_cwd" != "$boot_cwd" ]; then
    printf 'restored code working directory does not match resolved path: %s\n' "$boot_cwd" >&2
    exit 1
  fi
  printf 'bootstrapped code CWD: %s\n' "$restored_boot_cwd"
fi
# end-cwd-bootstrap-contract
```

Then interpret the result:

- **`ok` is false**: the recorded CWD is missing, relative, inaccessible, or not a git working tree (or the file is not an existing `CONTEXT-*.md`). Surface the captured `errors` verbatim and stop before Step 1, Step 2, Step 3, and Confirm Direction.
- **`bootstrap` is true**: the recipe restored the recorded directory (`cd`, verified with `pwd -P`). Run Step 1's validator with `--context-file "$explicit_context_file"` **from this restored directory**, so the validator resolves the handoff against repo B, not the repo you started in. Any `warnings` (an advisory prose `Working directory (code)` line diverging from `Session Metadata.cwd`) are printed but never fatal; `Session Metadata.cwd` stays authoritative.
- **`bootstrap` is false** (legacy handoff with no recorded code CWD): proceed to Step 1 unchanged; the current same-repo behavior is preserved.

The `--context-file` value passed to `bootstrap_cwd.py` and to the Step 1 validator is exactly the bound `$explicit_context_file`: a bare path with nothing after it. Never append cd hints or instruction prose to the argument.

### Step 1: Select the context file (the validator is authoritative)

Run the resume validator once, preserve its stdout in a unique temporary report file, print the file path and JSON for inspection, and treat its `checks.context_path` field as the authoritative selected file. This field is the canonical absolute path or null; `checks.context_file` is its basename label.

Shell state does not survive across Bash tool calls, so bind the recipe's inputs here before running it, exactly as Step 3 rebinds `validator_report_file`. These two bindings are independent. Bind `explicit_context_file` whenever the user supplied `--context-file` in ANY form (a basename, an absolute path, or a tilde path), so the recipe always forwards the requested selection to the validator instead of falling back to the newest handoff; a basename resolves against the current repo's `save_dir`. Bind `boot_cwd` only when Step 0 actually ran and bootstrapped (an absolute or tilde path with a recorded code CWD): set it to the directory Step 0 printed as `bootstrapped code CWD`, so the recipe re-enters repo B before validating. A basename selection skips Step 0 and leaves `boot_cwd` unset (no cross-repo `cd`), while still forwarding the name. With no `--context-file` at all, leave both unset and the recipe runs the argument-less discovery form unchanged:

```bash
validator_report_file="$(mktemp "${TMPDIR:-/tmp}/cwm-resume.XXXXXX")"
if [ -n "${explicit_context_file:-}" ]; then
  # Step 0 bootstrapped from an explicit --context-file. Its `cd` did not
  # persist into this shell, so re-enter the bootstrapped directory here (bind
  # boot_cwd to the path Step 0 printed as 'bootstrapped code CWD') before
  # running the validator, then forward the exact bound explicit path. The
  # validator resolves the handoff against repo B (the restored repo) and
  # selects the requested file, not whatever is newest in its save_dir.
  if [ -n "${boot_cwd:-}" ]; then
    if ! cd -- "$boot_cwd"; then
      printf 'could not re-enter bootstrapped code CWD: %s\n' "$boot_cwd" >&2
      exit 1
    fi
  fi
  python3 "${CLAUDE_PLUGIN_ROOT}/skills/resume/validator.py" --context-file "$explicit_context_file" > "$validator_report_file"
else
  python3 "${CLAUDE_PLUGIN_ROOT}/skills/resume/validator.py" > "$validator_report_file"
fi
printf 'validator report file: %s\n' "$validator_report_file"
python3 -m json.tool "$validator_report_file"
```

Record the printed `validator_report_file` path. A later Bash call must explicitly rebind that exact generated path and quote it; never rely on a shell variable surviving across tool calls. `mktemp` supplies the safe filename, while the file carries untrusted JSON only as data.

**Validator error gate (before selection):** Inspect the captured JSON immediately. If `ok` is false, or if `report` is null while `checks.context_path` is non-null, surface the captured `errors` verbatim and stop before candidate selection, Step 2, Step 3, or Confirm Direction. Do not process `checks.explicit_sibling`, either candidate list, or any `report` field first. If `ok` is true, `report` is null, and `checks.context_path` is null, continue to the existing no-context handling below; that state is not an error.

The validator deterministically selects the most recent `~/.config/sct/context-window-manager/<repo-slug>/CONTEXT-*.md` as the default `checks.context_file`, and also reports `checks.context_candidates`: every handoff written within 30 minutes (inclusive) of the newest one, newest-first, each as `{name, timestamp, summary}`.

Callers may pass a basename or an absolute/tilde path that resolves to an existing `CONTEXT-*.md` directly inside the current `save_dir`. An absolute/tilde path that instead resolves inside the canonical `save_dir` of a *registered sibling git worktree* (any linked worktree of this repo other than the current checkout) is now also accepted: the validator exposes its basename in `checks.context_file`, its canonical absolute path in `checks.context_path`, and the selection in `checks.explicit_sibling`. The sibling worktree's toplevel is the trust boundary; a valid recorded CWD inside that boundary becomes the drift/refs/continuity `report_cwd`, while missing, relative, inaccessible, or out-of-boundary recorded CWD metadata returns an explicit error. Any other context-file value still returns the explicit context-file contract error.

- **`checks.explicit_sibling` is true**: skip both the `checks.context_candidates` and `checks.cross_worktree_candidates` pickers and continue with that explicit `checks.context_path` selection.
- **`len(checks.context_candidates) <= 1`** (the unambiguous case): behavior is unchanged. Use `checks.context_path` as-is for the Step 2 summary and the Step 3 drift report, and display `checks.context_file` as its basename label. Do not run a separate `ls -t ... | head -1`; the validator's selection wins.
- **`len(checks.context_candidates) > 1`** (parallel-session collision): present an `AskUserQuestion` picker with one option per candidate, labeled `<name> | <timestamp> | <summary>`. If the chosen candidate is not the current `checks.context_file`, re-run the validator with `--context-file <name>` to recompute the drift/refs report for that file before continuing to Step 2/3:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/skills/resume/validator.py" --context-file "<name>" > "$validator_report_file"
  python3 -m json.tool "$validator_report_file"
  ```
- **`checks.cross_worktree_candidates` non-empty** (a sibling git worktree holds a strictly-newer handoff than this checkout's selection): after resolving the same-slug selection above, present each cross-worktree candidate via `AskUserQuestion`, labeled `<name> | <timestamp> | <summary> | worktree: <toplevel>`. These live under a different `save_dir`; the discovery picker still never auto-switches. If the user picks one, instruct them to `cd <toplevel>` and re-run `/context-window-manager:resume` there so its drift/refs/continuity report is computed against the correct worktree. (Distinct from candidate discovery: an *explicitly supplied* absolute/tilde sibling-worktree path passed to `--context-file` is now resolved directly and reported against that worktree. Discovery still declines to auto-switch.) This is the cross-worktree analogue of the same-slug `context_candidates` parallel-session picker; the same-slug selection remains the default when the user declines.

**Bare-path-only argument.** Whenever you re-run the validator with `--context-file` (a candidate `<name>` here, or the bound `$selected_context_file` in Step 3), the value is ONLY that bare path or filename with nothing after it. Never append instruction prose (cd hints, "and make sure ..." guidance) to the argument; guidance text stays in your reasoning, never inside the value. The validator rejects a value whose first whitespace token ends in `.md` with a distinct "extra text was appended" diagnostic, so a glued-on instruction fails loudly instead of resuming the wrong file.

If `checks.context_path` is null, no context files exist (see Error Handling) and you stop. The report file's full JSON payload (`{ok, checks:{context_file, context_path, explicit_sibling, context_candidates, cross_worktree_candidates}, report:{meta, drift, refs, continuity}, errors}`) feeds Step 3.

### Step 2: Display the previous session summary

Read `checks.context_path` and display its Session Summary, Work In Progress, and Next Steps sections, along with `checks.context_file` as the basename label and its timestamp. Highlight any pending questions or blockers, and restore awareness of any todo list.

### Step 3: Restore the working directory, then check drift

Explicitly rebind `validator_report_file` to the exact `mktemp` path printed in Step 1. Bind both paths by parsing that file through Python and command substitution; do not interpolate persisted metadata into shell syntax. Run this recipe as one shell operation:

```bash
# cwd-bind-contract
recorded_cwd="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["report"]["meta"]["cwd"] or "")' "$validator_report_file")"
selected_context_file="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["checks"]["context_path"] or "")' "$validator_report_file")"
# end-cwd-bind-contract

# cwd-restore-contract
current_cwd="$(pwd -P)"
cwd_changed=0
if [ "$current_cwd" != "$recorded_cwd" ]; then
  if ! test -d "$recorded_cwd" || ! test -x "$recorded_cwd"; then
    printf 'recorded working directory unavailable: %s\n' "$recorded_cwd" >&2
    exit 1
  fi
  previous_cwd="$current_cwd"
  if ! cd -- "$recorded_cwd"; then
    printf 'could not restore recorded working directory: %s\n' "$recorded_cwd" >&2
    exit 1
  fi
  restored_cwd="$(pwd -P)"
  if [ "$restored_cwd" != "$recorded_cwd" ]; then
    printf 'restored working directory does not match recorded path: %s\n' "$recorded_cwd" >&2
    exit 1
  fi
  cwd_changed=1
  printf 'previous CWD: %s\nrestored CWD: %s\n' "$previous_cwd" "$restored_cwd"
fi
# end-cwd-restore-contract

if [ "$cwd_changed" -eq 1 ]; then
  python3 "${CLAUDE_PLUGIN_ROOT}/skills/resume/validator.py" --context-file "$selected_context_file" > "$validator_report_file"
  python3 -m json.tool "$validator_report_file"
fi
```

When the paths match, the recipe does not run `cd` or refresh the already-correct report. After a mismatch, it reports both paths, then must re-run the validator with `--context-file` set to the safely bound `selected_context_file` and replace the captured JSON with this fresh result. If the recipe or refreshed run fails, or no longer resolves that context file, surface the exact recorded path plus any validator errors and stop before Confirm Direction. Never surface or reuse checks computed against the previous directory.

The `--context-file` value in the refresh re-run is exactly the bound `$selected_context_file` and nothing else: a bare path with nothing after it. Never append cd hints or instruction prose to the argument.

Use the restored directory for every subsequent tool call, including the refreshed drift report and any work the user chooses after Confirm Direction. When a tool accepts an explicit `cwd` or `workdir`, pass the restored path instead of assuming that a one-shot shell `cd` changed the tool's default directory. Parse the active validator JSON and surface:

| CWD state | Action |
| --- | --- |
| Active path matches recorded path | Continue without `cd` |
| Recorded path exists and differs | Restore, verify, refresh the validator report |
| Recorded path is unavailable | Show the exact path and stop before Confirm Direction |

- **Git drift** (from `report.drift`): the ahead/behind counts of the current HEAD versus the stored SHA, whether the stored SHA still resolves (`stored_resolvable`), whether the stored branch still exists (`branch_exists`), and the current branch versus the stored branch. When `is_git` is false (resume is running outside a git repo), say so and skip the git-specific numbers rather than inventing them; the `note` field explains the degraded case.
- **Reference verification** (from `report.refs`): for each cited reference, its `path`, whether it still exists (`exists`), and whether the cited line number is still in range (`line_ok`). Call out any path where `exists` is false or `line_ok` is false as a stale reference you should re-check before relying on it.
- **Continuity verification** (from `report.continuity`): the resume-side check of every expanded handoff field. Surface each before the Confirm Direction menu:
  - **cwd** (`continuity.cwd`): whether the resuming session's working directory matches the one stored at handoff (`match`); call out a mismatch, since every other path check is relative to it.
  - **Commits** (`continuity.commits`): for each recorded commit, its `sha`/`subject` and whether it is still `resolvable`; an unresolvable commit means a rebase or force-push dropped it since handoff.
  - **Working tree** (`continuity.working_tree`): the stored versus current state and whether it `diverged` (e.g. handoff was dirty, now clean). A `note` explains an unknown baseline.
  - **Remote** (`continuity.remote`): stored versus recomputed ahead/behind, upstream ref, and push state, with `changed` true when any of them differs. The recompute reuses the same prober capture used and carries the stored upstream in as a last resort, so a branch pushed without `-u` is still resolved rather than reported unknown; the `note` explains a genuinely missing upstream.
  - **PR** (`continuity.pr`): the recorded PR number/url and the re-queried `current_state` (e.g. `MERGED`) of that specific PR. The lookup is bound to the recorded number AND the recorded repository (derived from the recorded pr_url, passed as `gh pr view <number> --repo owner/repo`), never the resume cwd's current branch, so resuming on a different branch cannot report another PR's state as the recorded one and resuming in a different repo that also has that PR number cannot masquerade as it; a `note` covers a missing `gh`, an unresolvable recorded PR, a resolved-number mismatch, or a resolved-url/repo mismatch (state withheld in each case). The handoff stores no PR state, so this is live evidence, not a stored diff.
  - **Paths** (`continuity.paths`): existence of the recorded cobtask, TDD-plan, and ADR paths; flag any where `exists` is false as stale.

  Every continuity probe degrades to a `note` rather than raising, so a missing `gh`, an offline network, or a deleted PR/branch never breaks the report.

If `ok` is false, surface the captured errors verbatim and stop before the Confirm Direction prompt.

### Step 4: Confirm Direction

Only after the Step 3 drift check has been displayed, ask you how to proceed so the choice is evidence-based:

```
Ready to continue. What would you like to do?
1. Continue with the next steps listed above
2. Review completed work first
3. Start something different
```

Do not automatically start working; wait for your direction.

## Error Handling

If no context files exist:
```
No context files found in ~/.config/sct/context-window-manager/<repo-slug>/

To create one, run: /context-window-manager:handoff
```

If the recorded working directory is missing, inaccessible, or cannot be verified after `cd`, show the exact recorded path and stop before displaying path-relative checks or Confirm Direction.

## Rules

- The Confirm Direction menu is shown only after the drift check is displayed. Never ask how to proceed before you have seen the drift and stale-reference evidence.
- Always show which file is being resumed from.
- Never auto-start work; wait for an explicit choice.
- The handoff's recorded `cwd` is authoritative. Never continue path-relative reads, git checks, or implementation commands from a mismatched directory.
- Carry the restored path into every tool's explicit `cwd` or `workdir`; a standalone `cd` is not proof that another tool call inherited the directory.
- Resume is read-only over the working tree and the CONTEXT file; it runs no git-mutating command and no `gh` write. The continuity PR check issues only a read-only `gh pr view`.

## Version history

See [CHANGELOG.md](CHANGELOG.md).
