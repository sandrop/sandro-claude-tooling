# Changelog

## 1.0.0 - 2026-10-23

- Breaking: rename the skill from `context-window-resume` to `resume` (`/context-window-manager:resume`). The `SKILL.md` recipes call `${CLAUDE_PLUGIN_ROOT}/skills/resume/`, and the `mktemp` prefixes become `cwm-resume.XXXXXX` and `cwm-resume-bootstrap.XXXXXX`. The trigger bullet uses the qualified name, since a bare `/resume` collides with the built-in command. Handoffs whose `# Resume Command` line names the old skill still resume; `test_old_qualified_resume_line_still_accepted` locks that in. SCT-0070.

## 0.12.2 - 2026-10-23

- Prose only: the cross-worktree picker now tells the user to re-run the plugin-qualified `/context-window-manager:context-window-resume`. The trigger bullet keeps the bare `/context-window-resume`, since users may type the short name. No behavior change. SCT-0069.

## 0.12.1 - 2026-09-04

- Review hardening for the pre-validator CWD bootstrap. Because shell state does not persist across the resume's separate command steps, the Step 1 recipe now re-enters the bootstrapped directory (a rebound `boot_cwd`) and forwards the explicit `--context-file` (a rebound `explicit_context_file`) after Step 0 bootstraps, so the validator runs from the restored repo B and selects the requested file rather than running back in repo A (which would reject the cross-repo handoff) or selecting the newest handoff discovered in repo B; the argument-less discovery form is unchanged when no `--context-file` was supplied. The two Step 1 bindings are independent: `explicit_context_file` is forwarded whenever the user supplied `--context-file` in any form (a basename resolves against the current repo's save_dir, so a supported basename selection is no longer dropped into the newest-handoff fallback), while the `boot_cwd` re-entry happens only when Step 0 actually bootstrapped (an absolute or tilde cross-repo path). `bootstrap_cwd.py` applies the validator's exact filename contract (any `CONTEXT-*.md` via `CONTEXT_GLOB`, not only timestamp-shaped names), so an explicit cross-repo handoff named e.g. `CONTEXT-latest.md` is no longer rejected by the bootstrap before the validator sees it. It also decodes the handoff strictly (an invalid UTF-8 byte fails loud instead of being silently stripped), keeps its always-JSON exit-0 contract for a missing or malformed `--context-file` (argparse failure rides in `errors` instead of a bare stderr `SystemExit`), and escapes control characters in any untrusted handoff text placed into warnings or errors so a crafted handoff cannot inject terminal escape sequences. Tests assert the exit-0 protocol via subprocess return code, cover the strict-decode, missing-argument, and control-character paths, and lock both the explicit-only Step 0 guard and the explicit-context-file Step 1 recipe.

## 0.12.0 - 2026-09-04

- Pre-validator CWD bootstrap for explicit cross-repository handoffs. A new `bootstrap_cwd.py` helper parses an explicitly supplied `--context-file` handoff as data, resolves its recorded `Session Metadata.cwd`, and strictly verifies it (absolute, canonical, an accessible directory, a git working tree) before printing a JSON envelope (`{ok, bootstrap, cwd, recorded, warnings, errors}`, exit 0 always). `SKILL.md` gains a pre-Step-1 "Step 0" bootstrap contract block (`# cwd-bootstrap-contract`) that fires only on an explicit `--context-file`: it restores the recorded directory, then runs the existing Step 1 validator unchanged from there so a handoff from an unrelated repo is validated against its own repository instead of being rejected. A legacy handoff with no recorded code CWD is a bootstrap no-op (same-repo behavior preserved); a missing, relative, inaccessible, or non-git recorded CWD fails loud with the exact recorded value in the error. An advisory prose `Working directory (code)` line diverging from `Session Metadata.cwd` is warned, never fatal. `validator.py` is unchanged.

## 0.11.1 - 2026-09-01

- Prose sweep for the save-root relocation: the skill copy and the no-context error message now name `~/.config/sct/context-window-manager/<repo-slug>/` instead of the retired `~/.claude/.sct/context-window-manager/`. Resolution still runs through the shared `context_schema.save_dir()` helper (common 0.7.1); no behavior change.

## 0.11.0 - 2026-08-31

- Resume validator now flags a `--context-file` value that has instruction prose appended after an otherwise-valid `CONTEXT-*.md` path with a distinct "extra text was appended" diagnostic, checked before the generic contract error so the clearer message wins. A value is treated as appended-text when it splits into two or more whitespace tokens and the first token ends in `.md`; a well-formed single-token path, basename, absolute, or tilde value is unaffected. Both `--context-file` recipes in `SKILL.md` (Step 1 candidate re-run, Step 3 cwd-restore re-run) gain an explicit bare-path-only rule so a calling agent never merges guidance prose into the argument.

## 0.10.1 - 2026-08-26

- Gate validator errors before explicit-sibling selection, candidate handling, and report access while preserving the successful no-context path. Normalize unknown-user tildes and symlink-resolution loops in sibling handoffs to the stable recorded-CWD contract error.

## 0.10.0 - 2026-08-26

- Complete explicit sibling selection end to end: expose the canonical selected path and explicit-sibling marker, validate the handoff's recorded CWD inside the sibling worktree boundary and use it for drift/reference/continuity checks, fail explicitly for invalid recorded-CWD metadata, and bypass both candidate pickers for an explicit sibling selection. Strengthen the fresh-subprocess smoke so worktree-toplevel routing fails on a subdirectory-relative reference.

## 0.9.1 - 2026-08-26

- Restrict the registered sibling-worktree fallback to explicit absolute or tilde `--context-file` paths. A bare basename that resolves through an escaping symlink remains rejected by the strict same-checkout boundary.

## 0.9.0 - 2026-08-26

- Resume validator now accepts an explicit `--context-file` absolute/tilde path that resolves inside the canonical `save_dir` of a *registered sibling git worktree*, resolving that handoff and rebinding the drift/refs/continuity report to the sibling worktree's toplevel so `report.continuity.cwd.match` is true without a manual `cd`. The strict same-checkout path, basename/absolute/tilde resolution, candidate discovery, and no-context behavior are unchanged; any path that is neither inside the current checkout's `save_dir` nor a registered sibling worktree's still returns the (widened) contract error. The cross-worktree fallback is fully guarded and degrades to the strict path on any git/probe failure.

## 0.8.1 - 2026-08-25

- Resume validator surfaces strictly-newer handoffs from sibling git worktrees under `checks.cross_worktree_candidates` (each `{name, timestamp, summary, toplevel}`); Step 1 presents them as candidates without auto-switching, so a resume run from the primary checkout on `main` stops silently serving a stale main-slug handoff. The primary same-slug selection is unchanged and the sibling scan is fully guarded so it can never fail the run.

## 0.8.0 - 2026-08-20

- Resolve literal-tilde `--context-file` inputs while preserving basename and absolute-path inputs. Accept only existing `CONTEXT-*.md` files directly inside the current `save_dir`, reject paths outside it, and return the explicit contract error for missing or otherwise invalid inputs.

## 0.7.1 - 2026-08-19

- Add an install-seam CWD-restore smoke test that resolves the skill through the real `${CLAUDE_PLUGIN_ROOT}` boundary and asserts the `cd` side effect, the matching-path no-op, and the missing-path stop. Test-only; no change to the restoration contract. Paired with the plugin manifest bump (0.4.0 -> 0.5.0) that refreshes the version-keyed runtime cache so the 0.7.0 contract actually reaches the invoked copy.

## 0.7.0 - 2026-08-17

- Restore the handoff's recorded working directory before displaying path-relative resume checks or Confirm Direction. Matching paths remain unchanged; an existing mismatch is restored and verified before the validator report is refreshed; a missing, inaccessible, or unverifiable path stops resume rather than allowing later work from the wrong directory. Tools with explicit `cwd` or `workdir` parameters receive the restored path on every subsequent call.

## 0.6.1 - 2026-08-09

- Bind the continuity PR re-query to the recorded repository, not just the recorded number (adversarial-review escalation of the 0.6.0 by-number fix). PR numbers are unique only within a repo, so resuming in a different repo that also has PR #<number> could mis-attribute its state; the lookup now passes `gh pr view <number> --repo owner/repo` derived from the recorded pr_url, and withholds the state with a repo-mismatch note when the resolved url differs from the recorded pr_url. Step 3 PR bullet updated.

## 0.6.0 - 2026-08-08

- Harden the continuity report's PR and remote checks (adversarial review of the 0.5.0 work). The PR check now re-queries the recorded PR by its number, so resuming on a different branch can no longer surface that branch's PR state as the recorded PR's; the report notes an unresolvable recorded PR or a resolved-number mismatch instead of trusting it. The remote check compares stored versus current upstream ref and push state (not just ahead/behind) and resolves a branch pushed without `-u` via the shared capture prober, so it reads as pushed rather than unknown. Step 3 documents both.

## 0.5.0 - 2026-08-08

- Verify the expanded handoff metadata on resume. The validator's single `report` run now carries a `continuity` object beside `drift`/`refs`, checking each recorded field: the working directory still matches (`cwd`), each recorded commit is still resolvable (`commits`), the working tree has not diverged (`working_tree`), the remote ahead/behind was recomputed (`remote`), the PR's current state is re-queried read-only via `gh pr view` (`pr`), and the cobtask/TDD-plan/ADR paths still exist (`paths`). Every probe degrades to a structured note (no crash) when `gh` is missing, offline, or the PR/branch/upstream is gone; resume stays read-only (no git-mutating command, no `gh` write). Step 3 documents surfacing each check before the Confirm Direction menu.

## 0.4.0 - 2026-08-08

- Parse the optional collision-resistant filename suffix (`CONTEXT-{timestamp}-{hex}.md`, see context-window-handoff 0.3.0) when computing `context_candidates`. Two handoffs written in the same second are now both surfaced as distinct candidates instead of one silently overwriting the other on disk before resume ever gets to choose.

## 0.3.0 - 2026-08-08

- Guard against parallel-session handoff collisions: the validator now reports `checks.context_candidates`, every `CONTEXT-*.md` written within a fixed 30-minute window (inclusive) of the newest handoff, newest-first, each with its filename-derived timestamp and `Session Summary` first line. A new `--context-file NAME` flag recomputes the drift/refs report for a chosen candidate. Step 1 presents an `AskUserQuestion` picker only when more than one candidate is in-window; the old "Multiple files" note is folded into that branch.

## 0.2.0 - 2026-07-19

- Read the most recent `CONTEXT-*.md` from the home-scoped, per-repo path `~/.claude/.sct/context-window-manager/<repo-slug>/` instead of the retired project-local path. Packaged into the context-window-manager plugin; invokes its validator via `${CLAUDE_PLUGIN_ROOT}`.
- Genericized skill copy (second-person `you`, no personal names) for public distribution (pre-release).

## 0.1.0 - 2026-07-02

- Promote the context-window-resume command to a full skill. Before the Confirm Direction prompt, run a drift check (stored SHA vs current HEAD ahead/behind, branch-exists) and a stale-reference verification (cited paths + line numbers) via the shared context-window-common library. Degrades gracefully outside a git repo.
