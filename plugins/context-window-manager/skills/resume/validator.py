#!/usr/bin/env python3
# validator.py - resume pre-flight + drift/verify report.
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

COMMON = Path(__file__).resolve().parent.parent.parent / "lib" / "context-window-common"
sys.path.insert(0, str(COMMON))
import context_schema
import git_drift
import verify_continuity
import verify_refs

# Fixed constant, no env override: simplicity-first.
WINDOW_MINUTES = 30
# Distinct diagnostic for a --context-file value that carries instruction prose
# appended after an otherwise-valid path. Surfaced before the generic contract
# error so the clearer "you appended extra text" message wins.
APPENDED_TEXT_ERROR = (
    "context file value looks like extra text was appended after the path; "
    "pass ONLY the bare CONTEXT-*.md filename or path with nothing after it: {arg}"
)
_TS_FORMAT = "%Y-%m-%d-%H-%M-%S"
# The timestamp core, with an optional trailing collision-resistant suffix
# (see context_schema.context_filename): CONTEXT-<ts>.md or CONTEXT-<ts>-<hex>.md.
_NAME_RE = re.compile(
    r"^CONTEXT-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})(?:-[0-9a-f]+)?\.md$"
)


def _parse_context_ts(name: str) -> "datetime | None":
    """Parse the YYYY-MM-DD-HH-MM-SS core of a CONTEXT-*.md filename,
    tolerating an optional trailing collision-resistant suffix. Returns None
    for a non-conforming name so callers can exclude it from window
    comparison instead of raising."""
    m = _NAME_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), _TS_FORMAT)  # noqa: DTZ007 - filename stamps are naive local time
    except ValueError:
        return None


def _ts_str(name: str) -> str:
    """Display timestamp for a candidate: the parsed core when the name
    matches the CONTEXT-*.md shape (suffix stripped), else a best-effort
    prefix/suffix strip of the raw name."""
    m = _NAME_RE.match(name)
    return m.group(1) if m else name.removeprefix("CONTEXT-").removesuffix(".md")


def _safe_context_target(target: Path, save_dir: Path) -> "Path | None":
    try:
        resolved = target.expanduser().resolve()
        resolved_save_dir = save_dir.resolve()
    except (OSError, RuntimeError):
        return None
    if (
        resolved.parent != resolved_save_dir
        or not resolved.is_file()
        or not Path(resolved.name).match(context_schema.CONTEXT_GLOB)
    ):
        return None
    return resolved


def _context_files(cwd: Path) -> list[Path]:
    d = context_schema.save_dir(cwd)
    if not d.is_dir():
        return []
    files = set()
    for candidate in d.glob(context_schema.CONTEXT_GLOB):
        target = _safe_context_target(candidate, d)
        if target is not None:
            files.add(target)
    return sorted(files)


def _within_window(files: list[Path]) -> list[Path]:
    """Every file within WINDOW_MINUTES (inclusive) of the newest file's
    filename-derived timestamp, newest-first. A file with an unparseable
    timestamp is excluded from the window comparison; the newest file
    (files[-1]) is always included regardless of parseability."""
    if not files:
        return []
    newest_ts = _parse_context_ts(files[-1].name)
    if newest_ts is None:
        return [files[-1]]
    window = timedelta(minutes=WINDOW_MINUTES)
    in_window = []
    for f in reversed(files):
        ts = _parse_context_ts(f.name)
        if ts is not None and newest_ts - ts <= window:
            in_window.append(f)
    return in_window


def _session_summary_line(text: str) -> str:
    """First non-empty, non-heading line beneath a 'Session Summary' heading
    (any # level). Returns '' when no such heading exists, or no prose line
    follows it before the next heading."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (
            stripped.startswith("#")
            and stripped.lstrip("#").strip() == "Session Summary"
        ):
            start = i + 1
            break
    if start is None:
        return ""
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if stripped:
            return stripped
    return ""


def _parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument(
        "--context-file",
        dest="context_file",
        default=None,
        help="Select a CONTEXT-*.md by basename, absolute path, or tilde path; "
        "a basename must resolve directly inside the current save_dir, while an "
        "absolute or tilde path may resolve inside a registered sibling worktree's "
        "canonical save directory.",
    )
    return p.parse_args(argv)


def _resolve_cross_worktree_target(target: Path, cwd: Path):
    """If `target` is a CONTEXT-*.md directly inside the canonical save_dir of a
    git worktree linked to `cwd`'s repo (other than cwd's own), return
    (resolved_path, worktree_toplevel, None). Otherwise None. Never raises: any
    git/probe failure degrades to None so the strict same-checkout path is the
    only fallback (mirrors `_cross_worktree_candidates`' except-degrade)."""
    try:
        own_slug = context_schema.repo_slug(cwd)
        for top in context_schema.worktree_toplevels(cwd):
            slug = context_schema.repo_slug(top)
            if slug == own_slug:
                continue
            resolved = _safe_context_target(target, context_schema.SAVE_ROOT / slug)
            if resolved is not None:
                return resolved, top, None
    except Exception:  # noqa: BLE001 - never break the strict path
        return None
    return None


def _looks_like_appended_text(arg: str) -> bool:
    """True when `arg` looks like a valid path with instruction prose appended
    after it: the value splits into >= 2 whitespace-delimited tokens and the
    first token ends in `.md` (the real path), so the remainder is
    stray text. A legitimate --context-file value is a single path token with
    no interior whitespace, so this never fires on a well-formed argument."""
    parts = arg.split()
    if len(parts) < 2:
        return False
    return parts[0].endswith(".md")


def _resolve_context_target(
    arg: str, cwd: Path
) -> tuple[Path | None, Path | None, bool, str | None]:
    save_dir = context_schema.save_dir(cwd)
    if _looks_like_appended_text(arg):
        return None, None, False, APPENDED_TEXT_ERROR.format(arg=arg)
    error = (
        "context file must be an existing CONTEXT-*.md basename or path inside "
        f"the current checkout or a registered sibling worktree: {arg}"
    )
    path = Path(arg)
    explicit_path = arg.startswith("~") or path.is_absolute()
    if explicit_path:
        target = path
    elif path.name == arg:
        target = save_dir / path
    else:
        return None, None, False, error
    resolved = _safe_context_target(target, save_dir)
    if resolved is not None:
        return resolved, cwd, False, None
    if explicit_path:
        sibling = _resolve_cross_worktree_target(target, cwd)
        if sibling is not None:
            resolved, selection_root, _ = sibling
            return resolved, selection_root, True, None
    return None, None, False, error


def _resolve_recorded_sibling_cwd(
    recorded: "str | None", selection_root: Path
) -> tuple[Path | None, str | None]:
    error = (
        "recorded working directory for sibling context file must be an accessible "
        f"directory inside the selected worktree: {recorded or ''}"
    )
    if not recorded:
        return None, error
    try:
        path = Path(recorded).expanduser()
        if not path.is_absolute():
            return None, error
        resolved = path.resolve(strict=True)
        root = selection_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, error
    if (
        not resolved.is_dir()
        or not os.access(resolved, os.X_OK)
        or not resolved.is_relative_to(root)
    ):
        return None, error
    return resolved, None


def _cross_worktree_candidates(cwd: Path, primary_ts):
    """Newest handoff of each sibling worktree slug whose filename timestamp is
    strictly newer than the cwd-slug selection (primary_ts, or None when the
    cwd-slug save_dir is empty -- in which case any sibling handoff qualifies).
    Never raises: any failure degrades to whatever was collected so far, so the
    primary resume path is never affected by the sibling scan."""
    out = []
    try:
        own_slug = context_schema.repo_slug(cwd)
        seen = {own_slug}
        for top in context_schema.worktree_toplevels(cwd):
            slug = context_schema.repo_slug(top)
            if slug in seen:
                continue
            seen.add(slug)
            d = context_schema.SAVE_ROOT / slug
            if not d.is_dir():
                continue
            sib_files = []
            for candidate in d.glob(context_schema.CONTEXT_GLOB):
                target = _safe_context_target(candidate, d)
                if target is not None:
                    sib_files.append(target)
            if not sib_files:
                continue
            newest = max(sib_files)
            sib_ts = _parse_context_ts(newest.name)
            if sib_ts is None:
                continue
            if primary_ts is not None and sib_ts <= primary_ts:
                continue
            out.append(
                {
                    "name": newest.name,
                    "timestamp": _ts_str(newest.name),
                    "summary": _session_summary_line(
                        newest.read_text(encoding="utf-8", errors="ignore")
                    ),
                    "toplevel": str(top),
                }
            )
    except Exception:  # noqa: BLE001 - sibling scan must never break primary path
        return out
    return out


def main(argv=None):
    args = _parse_args(argv)
    cwd = Path.cwd()
    checks = {
        "context_file": None,
        "context_path": None,
        "explicit_sibling": False,
        "context_candidates": [],
        "cross_worktree_candidates": [],
    }
    report, errors = None, []
    try:
        files = _context_files(cwd)
        checks["context_candidates"] = [
            {
                "name": f.name,
                "timestamp": _ts_str(f.name),
                "summary": _session_summary_line(
                    f.read_text(encoding="utf-8", errors="ignore")
                ),
            }
            for f in _within_window(files)
        ]
        primary_ts = _parse_context_ts(files[-1].name) if files else None
        checks["cross_worktree_candidates"] = _cross_worktree_candidates(
            cwd, primary_ts
        )
        report_cwd = cwd
        explicit_sibling = False
        if args.context_file is not None:
            target, selection_root, explicit_sibling, error = _resolve_context_target(
                args.context_file, cwd
            )
            if error:
                errors.append(error)
        else:
            target = files[-1] if files else None
            selection_root = cwd
        if target is not None:
            checks["context_file"] = target.name
            checks["context_path"] = str(target)
            checks["explicit_sibling"] = explicit_sibling
            text = target.read_text(encoding="utf-8", errors="ignore")
            meta = context_schema.parse_metadata_block(text)
            if explicit_sibling:
                assert selection_root is not None
                report_cwd, error = _resolve_recorded_sibling_cwd(
                    meta.get("cwd"), selection_root
                )
                if error:
                    errors.append(error)
            if not errors:
                drift = git_drift.compute_drift(
                    meta["git_sha"], meta["git_branch"], str(report_cwd)
                )
                refs = verify_refs.verify_refs(
                    verify_refs.extract_refs(text), str(report_cwd)
                )
                # Read-only continuity verification of the expanded handoff fields
                # (cwd, paths, PR state, commits, working tree, remote), folded into the
                # same single report run. The default gh runner issues a read-only
                # `gh pr view`; a missing gh or offline network degrades to a note.
                continuity = verify_continuity.verify_continuity(meta, str(report_cwd))
                report = {
                    "meta": meta,
                    "drift": drift,
                    "refs": refs,
                    "continuity": continuity,
                }
    except Exception as e:  # noqa: BLE001 - surface any failure via errors/ok
        errors.append(f"{type(e).__name__}: {e}")
    print(
        json.dumps(
            {"ok": not errors, "checks": checks, "report": report, "errors": errors},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
