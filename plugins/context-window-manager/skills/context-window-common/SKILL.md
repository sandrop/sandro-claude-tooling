---
name: context-window-common
version: 0.2.0
version-date: 2026-07-19
description: Shared library for the context-window-handoff and context-window-resume skills. Not user-invocable; carries no slash command. Houses context_schema.py (CONTEXT-*.md Session Metadata block render/parse), git_drift.py (stored-SHA vs current-HEAD ahead/behind + branch-exists, degrades when not a git repo), and verify_refs.py (extract backticked path refs from handoff text and stat them against the working tree). Bumps independently of its consumers.
---

# context-window-common

Shared library for the context-window family. There is no `/context-window-common` slash command; this directory ships helpers that the two user-facing skills depend on. Bump this skill independently of its consumers; each consumer references the live helper, not a snapshot.

## Modules

- `context_schema.py`: render and parse the `### Session Metadata` block that context-window-handoff appends to each `CONTEXT-*.md` file. `render_metadata_block(sha, sha_short, branch)` produces the block text; `parse_metadata_block(text)` reads it back. Also owns the save-path contract: `SAVE_ROOT` (`~/.claude/.sct/context-window-manager`, overridable via the `CWM_SAVE_ROOT` env var), `repo_slug(cwd)` (git-toplevel basename, else cwd basename), and `save_dir(cwd, create=False)` (`SAVE_ROOT/<repo-slug>`, made on demand when `create=True`), plus the shared constants `CONTEXT_GLOB` (`CONTEXT-*.md`) and `META_HEADING` (`### Session Metadata`). When the handoff runs outside a git repo, the helper renders `n/a` values so the block is always well-formed.
- `git_drift.py`: `compute_drift(stored_sha, stored_branch, cwd)` compares the stored HEAD SHA against the current HEAD and reports ahead/behind counts, whether the stored SHA still resolves, whether the stored branch still exists, and the current branch. Degrades gracefully when `cwd` is not a git repo (returns `is_git` false rather than raising), so context-window-resume can still show the rest of its report.
- `verify_refs.py`: `extract_refs(text)` pulls backticked path references out of the handoff prose; `verify_refs(refs, cwd)` stats each one against the working tree and reports whether the path still exists and whether any cited line number is still in range. This is how context-window-resume flags stale references before Sandro chooses a direction.

Each module has a sibling `test_*.py`. The library is imported by the two sibling skills via a `sys.path` insert into this directory, so the consumers always call the live helper rather than a copied snapshot.

## Rules

- Read-only library. Helpers stat and parse; they never mutate files.
- No external dependencies. The metadata block shape is narrow enough that a hand-rolled render and parse pair is faster and safer than dragging in a YAML library.
- Helpers degrade rather than raise on boundary conditions (no git repo, unresolvable SHA, missing path), so the consuming skills can always render a partial report.

## Version history

See [CHANGELOG.md](CHANGELOG.md).
