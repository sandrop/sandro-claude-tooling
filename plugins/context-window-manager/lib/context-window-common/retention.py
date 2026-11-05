# retention.py - once/day archival of stale CONTEXT-*.md handoffs into a
# terminal zArchives/ sibling under SAVE_ROOT. Move-only; never deletes.
import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path

ARCHIVE_DIRNAME = "zArchives"  # sibling under SAVE_ROOT; never a repo slug
PRUNE_MARKER_NAME = (
    ".last_prune"  # dot-file at SAVE_ROOT root; never a CONTEXT glob or slug
)
DEFAULT_RETENTION_DAYS = 30
_ENV_VAR = "SCT_CWM_RETENTION_DAYS"

# Authoritative age clock: the CONTEXT-<YYYY-MM-DD-HH-MM-SS> filename core. The
# hex suffix (context_schema.context_filename) is optional so a suffix-less
# handoff still parses by name rather than falling back to mtime. Mirrors
# resume/validator.py's _NAME_RE.
_TS_RE = re.compile(
    r"^CONTEXT-(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})(?:-[0-9a-f]+)?\.md$"
)


class RetentionConfigError(ValueError):
    """Raised when SCT_CWM_RETENTION_DAYS is set to a non-int or a value < 1."""


def retention_days(env=None) -> int:
    """Resolve N. env defaults to os.environ; unset/empty -> DEFAULT_RETENTION_DAYS;
    non-int or < 1 -> raise RetentionConfigError (fail loud, never silently
    disable pruning)."""
    env = os.environ if env is None else env
    raw = env.get(_ENV_VAR)
    if raw is None or raw == "":
        return DEFAULT_RETENTION_DAYS
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise RetentionConfigError(f"{_ENV_VAR}={raw!r} is not an integer")
    if val < 1:
        raise RetentionConfigError(f"{_ENV_VAR}={val} must be >= 1")
    return val


def parse_context_timestamp(name: str) -> "datetime | None":
    """'CONTEXT-2026-09-07-14-30-05-ab12cd.md' -> datetime(2026,9,7,14,30,5);
    unparseable name -> None."""
    m = _TS_RE.match(name)
    if not m:
        return None
    year, month, day, hour, minute, second = (int(g) for g in m.groups())
    try:
        # Naive local wall-clock: CONTEXT filenames are stamped in OS-local time
        # (context_schema.context_filename), matching the plugin's naive-datetime
        # convention. A tz here would misread every recorded timestamp.
        return datetime(year, month, day, hour, minute, second)  # noqa: DTZ001
    except ValueError:
        return None


def file_age_days(path: Path, now: datetime) -> float:
    """(now - filename_ts) in days; falls back to mtime when the name is
    unparseable."""
    ts = parse_context_timestamp(path.name)
    if ts is None:
        # Naive local mtime to match parse_context_timestamp's naive clock and
        # the caller-supplied naive `now`; a tz-aware value would not subtract.
        ts = datetime.fromtimestamp(path.stat().st_mtime)  # noqa: DTZ006
    return (now - ts).total_seconds() / 86400.0


def _iter_slug_dirs(save_root: Path):
    """Yield immediate child dirs of save_root other than the reserved
    zArchives archive. Dot-prefixed dirs are NOT skipped: `repo_slug` is
    `<toplevel-basename>-<hash>`, so a repo whose toplevel name starts with a
    dot (e.g. `~/.claude` -> `.claude-<hash>`) has a legitimate dot-prefixed
    slug that must still be pruned. The `.last_prune` marker is a file, so
    `is_dir()` already excludes it."""
    if not save_root.is_dir():
        return
    for child in save_root.iterdir():
        if not child.is_dir():
            continue
        if child.name == ARCHIVE_DIRNAME:
            continue
        yield child


def _ensure_private_dir(path: Path) -> None:
    """Create path (and parents) owner-only, mirroring
    context_schema.save_dir. Archived handoffs carry the same session narrative
    and repo metadata as the live 0o700 slug dirs, so their zArchives/ home must
    be no more exposed. chmod after mkdir defeats a permissive umask on a dir
    that already existed."""
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)


def _collision_free_dest(dest_dir: Path, name: str) -> Path:
    """Return dest_dir/name, or a distinct sibling when that path is already
    taken, so archiving never overwrites (and so never deletes) a retained
    handoff. A collision means the same handoff filename was archived before,
    e.g. after a manual restore; move-only forbids clobbering it. zArchives/ is
    terminal and never re-scanned, so a suffixed name is inert."""
    target = dest_dir / name
    if not target.exists():
        return target
    p = Path(name)
    stem, suffix = p.stem, p.suffix
    i = 1
    while True:
        candidate = dest_dir / f"{stem}.dup{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def prune(
    save_root: Path, now: datetime, days: int, keep_dir: "Path | None" = None
) -> dict:
    """Move stale files into zArchives/<slug>/; move a whole slug dir only when
    every CONTEXT-*.md under it is stale. Never scans zArchives/. Returns
    {"files_moved": [...], "dirs_moved": [...]}. No-op for an empty/absent
    root. Idempotent and safe under a concurrent prune: a source already moved
    by another handoff is skipped rather than erroring, and a destination that
    already exists is never overwritten (move-only, never deletes).

    keep_dir names the caller's active handoff directory (the resolved
    save_dir for this session). That dir is never relocated wholesale even when
    all its handoffs are stale: its stale files are still archived one at a
    time, but the (now-empty) dir is left in place so the handoff write that
    follows this prune still has its already-reported, writable target."""
    moved_files, moved_dirs = [], []
    archive_root = save_root / ARCHIVE_DIRNAME
    keep = keep_dir.resolve() if keep_dir is not None else None
    for slug_dir in _iter_slug_dirs(save_root):
        ctx = sorted(slug_dir.glob("CONTEXT-*.md"))
        if not ctx:
            continue
        stale = [f for f in ctx if file_age_days(f, now) >= days]
        dest_dir = archive_root / slug_dir.name
        is_active = keep is not None and slug_dir.resolve() == keep
        if (
            len(stale) == len(ctx)
            and not dest_dir.exists()
            and slug_dir.exists()
            and not is_active
        ):
            # Whole slug dir is stale and no prior archive exists for it: move
            # the directory wholesale (cheap, preserves any non-CONTEXT files).
            _ensure_private_dir(dest_dir.parent)
            try:
                shutil.move(str(slug_dir), str(dest_dir))
            except FileNotFoundError:
                # A concurrent prune moved this slug dir first. Move-only means
                # the handoff is safely archived by that run; nothing is lost.
                continue
            moved_dirs.append(slug_dir.name)
            continue
        # Either some live files remain, or a prior day already created the
        # archive dir (merging a whole-dir move onto it would raise). Move the
        # stale files one at a time into the archive dir.
        if not stale:
            continue
        _ensure_private_dir(archive_root)  # parent, before the 0o700 leaf below
        _ensure_private_dir(dest_dir)
        for f in stale:
            if not f.exists():
                # Already archived by a concurrent prune; skip (race-safe).
                continue
            target = _collision_free_dest(dest_dir, f.name)
            try:
                shutil.move(str(f), str(target))
            except FileNotFoundError:
                # A concurrent prune moved the source between the check above
                # and here; it is safely archived by that run (move-only).
                continue
            moved_files.append(target.name)
    return {"files_moved": moved_files, "dirs_moved": moved_dirs}


def maybe_prune(
    save_root: Path, today: "date", env=None, keep_dir: "Path | None" = None
) -> dict:
    """Once/day gate via PRUNE_MARKER_NAME. If the marker date == today, return
    {"skipped": "already-pruned-today", ...} without walking. Else run prune(),
    stamp the marker to today, return the prune summary. Config errors
    propagate before any walk. keep_dir is forwarded to prune() so the caller's
    active handoff dir is never relocated out from under a pending write."""
    days = retention_days(env)  # raises RetentionConfigError before any walk
    marker = save_root / PRUNE_MARKER_NAME
    try:
        if marker.exists() and marker.read_text().strip() == today.isoformat():
            return {
                "skipped": "already-pruned-today",
                "files_moved": [],
                "dirs_moved": [],
            }
    except OSError:
        pass  # unreadable marker -> treat as not-pruned-today
    summary = prune(save_root, now=datetime.now(), days=days, keep_dir=keep_dir)  # noqa: DTZ005 - naive local clock, matches filename timestamps
    save_root.mkdir(parents=True, exist_ok=True)
    marker.write_text(today.isoformat())
    return summary
