# retention.py - archival of stale per-repo-slug handoff dirs into the terminal
# zArchives/<YYYY-MM>/<slug>/ tree under SAVE_ROOT. A slug dir whose newest
# CONTEXT-*.md is at least N days old moves whole, on every handoff. N comes
# from env or [retention] in SAVE_ROOT/config.toml. Move-only; never deletes.
import errno
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

ARCHIVE_DIRNAME = "zArchives"  # sibling under SAVE_ROOT; never a repo slug
CONFIG_NAME = "config.toml"  # file at SAVE_ROOT root; never a slug dir
DEFAULT_RETENTION_DAYS = 7
ENV_DAYS = "SCT_CWM_RETENTION_DAYS"

try:
    import tomllib
except ImportError:  # Python < 3.11: config.toml is skipped, env/defaults apply
    tomllib = None

# Authoritative age clock: the CONTEXT-<YYYY-MM-DD-HH-MM-SS> filename core. The
# hex suffix (context_schema.context_filename) is optional so a suffix-less
# handoff still parses by name rather than falling back to mtime. Mirrors
# resume/validator.py's _NAME_RE.
_TS_RE = re.compile(
    r"^CONTEXT-(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})(?:-[0-9a-f]+)?\.md$"
)


class RetentionConfigError(ValueError):
    """Raised for a bad retention window: days that is not an int >= 1,
    malformed config.toml, or a non-table [retention]."""


def _check_days(val, source: str) -> int:
    if type(val) is not int:  # bool is an int subclass; reject it too
        raise RetentionConfigError(f"{source}={val!r} is not an integer")
    if val < 1:
        raise RetentionConfigError(f"{source}={val} must be >= 1")
    return val


def _config_table(config_path: "Path | None") -> "dict | None":
    if config_path is None or tomllib is None or not config_path.is_file():
        return None
    try:
        data = tomllib.loads(config_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise RetentionConfigError(f"{config_path}: {exc}") from None
    table = data.get("retention")
    if table is not None and not isinstance(table, dict):
        raise RetentionConfigError(f"{config_path}: [retention] must be a table")
    return table


def retention_days(env=None, config_path: "Path | None" = None) -> int:
    """Resolve N: SCT_CWM_RETENTION_DAYS first, then [retention] days in
    config_path, else DEFAULT_RETENTION_DAYS. env defaults to os.environ.
    Without tomllib the config file is skipped. Raises RetentionConfigError
    before any walk (fail loud, never silently disable pruning)."""
    env = os.environ if env is None else env
    raw = env.get(ENV_DAYS)
    if raw:
        try:
            val = int(raw)
        except ValueError:
            raise RetentionConfigError(
                f"{ENV_DAYS}={raw!r} is not an integer"
            ) from None
        return _check_days(val, ENV_DAYS)
    table = _config_table(config_path) or {}
    if table.get("days") is None:
        return DEFAULT_RETENTION_DAYS
    return _check_days(table["days"], "[retention] days")


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


def file_timestamp(path: Path) -> datetime:
    """The filename timestamp, or the naive-local mtime when the name is
    unparseable."""
    ts = parse_context_timestamp(path.name)
    if ts is None:
        # Naive local mtime to match parse_context_timestamp's naive clock and
        # the caller-supplied naive `now`; a tz-aware value would not subtract.
        ts = datetime.fromtimestamp(path.stat().st_mtime)  # noqa: DTZ006
    return ts


def file_age_days(path: Path, now: datetime) -> float:
    """(now - file_timestamp(path)) in days."""
    return (now - file_timestamp(path)).total_seconds() / 86400.0


def archive_dir_for(archive_root: Path, slug: str, path: Path) -> Path:
    """zArchives/<YYYY-MM>/<slug>/, the month taken from path's own timestamp."""
    return archive_root / f"{file_timestamp(path):%Y-%m}" / slug


def _iter_slug_dirs(save_root: Path):
    """Yield immediate child dirs of save_root other than the reserved
    zArchives archive. Dot-prefixed dirs are NOT skipped: `repo_slug` is
    `<toplevel-basename>-<hash>`, so a repo whose toplevel name starts with a
    dot (e.g. `~/.claude` -> `.claude-<hash>`) has a legitimate dot-prefixed
    slug that must still be pruned. Root files (config.toml, a legacy
    `.last_prune` marker) are excluded by `is_dir()`."""
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


def _dup_name(name: str, i: int) -> str:
    p = Path(name)
    return f"{p.stem}.dup{i}{p.suffix}"


def _collision_free_dest(
    dest_dir: Path, name: str, reserved: "set[Path] | frozenset" = frozenset()
) -> Path:
    """Return dest_dir/name, or a distinct sibling when that path is already
    taken (on disk or in `reserved`, destinations a dry run has already
    planned), so archiving never overwrites (and so never deletes) a retained
    handoff. A collision means the same handoff filename was archived before,
    e.g. after a manual restore; move-only forbids clobbering it. zArchives/ is
    terminal and never re-scanned, so a suffixed name is inert. This is a
    read-only probe for planning; a real move claims its name with
    _reserve_dest."""
    target = dest_dir / name
    if not target.exists() and target not in reserved:
        return target
    i = 1
    while True:
        candidate = dest_dir / _dup_name(name, i)
        if not candidate.exists() and candidate not in reserved:
            return candidate
        i += 1


def _reserve_dest(dest_dir: Path, name: str) -> Path:
    """Atomically claim dest_dir/name (or the first free .dupN sibling) by
    creating it exclusively as an empty placeholder. Choosing a name and then
    moving onto it are separate steps, so two concurrent archivers (a prune and
    a migration, or two prunes) could pick the same absent name and the later
    rename would replace the earlier file. An exclusive create makes the claim
    atomic: the loser sees the name taken and moves on to the next suffix. The
    caller moves its file over the placeholder it owns."""
    candidate = dest_dir / name
    i = 0
    while True:
        try:
            candidate.touch(mode=0o600, exist_ok=False)
        except FileExistsError:
            i += 1
            candidate = dest_dir / _dup_name(name, i)
            continue
        return candidate


def _release_dest(target: Path) -> None:
    """Remove a placeholder claimed by _reserve_dest after a failed move, but
    only while it is still empty: shutil.move can fall back to copy-then-delete,
    so a failure after the copy must keep the transferred handoff."""
    try:
        if target.stat().st_size == 0:
            target.unlink()  # our own empty placeholder, no handoff
    except FileNotFoundError:
        pass


def _archive(f: Path, archive_root: Path, slug: str) -> "Path | None":
    """Move f to its dated archive dir without overwriting. Returns the target,
    or None when a concurrent prune already moved f (move-only: nothing lost).
    Any other move failure releases the empty placeholder and re-raises."""
    dest_dir = archive_dir_for(archive_root, slug, f)
    for d in (archive_root, dest_dir.parent, dest_dir):  # 0o700 at every level
        _ensure_private_dir(d)
    target = _reserve_dest(dest_dir, f.name)
    try:
        shutil.move(str(f), str(target))
    except FileNotFoundError:
        _release_dest(target)
        return None
    except BaseException:
        _release_dest(target)  # never leave an empty CONTEXT-*.md behind
        raise
    return target


def _move_dir(slug_dir: Path, dest: Path) -> "Path | None":
    """Rename slug_dir to dest, or to the first free dest.dupN sibling when a
    prior archive of the same slug already holds that month. rename never
    replaces a non-empty dir, so two prunes racing for one name cannot clobber
    each other: the loser gets EEXIST/ENOTEMPTY and tries the next suffix. The
    moved dir is made 0o700, since rename keeps the live dir's mode.
    Returns the target, or None when a concurrent prune already moved
    slug_dir (move-only: nothing lost)."""
    for d in (dest.parent.parent, dest.parent):  # zArchives/, month: 0o700
        _ensure_private_dir(d)
    target, i = dest, 0
    while True:
        if not target.exists():
            try:
                os.rename(slug_dir, target)
            except FileNotFoundError:
                return None
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
            else:
                target.chmod(0o700)  # archived slug dir: 0o700 like every level
                return target
        i += 1
        target = dest.with_name(f"{dest.name}.dup{i}")


def prune(
    save_root: Path, now: datetime, days: int, keep_dir: "Path | None" = None
) -> dict:
    """Move each slug dir whose newest CONTEXT-*.md is at least `days` old,
    whole, into zArchives/<YYYY-MM>/<slug>/, the month taken from that newest
    file. One fresh handoff keeps its dir live. A dir with no CONTEXT-*.md is
    left alone. Never scans zArchives/. keep_dir, the caller's own save dir
    and the target of the write that follows, is never moved. Returns
    {"days": days, "dirs_moved": ["<YYYY-MM>/<slug>[.dupN]", ...]}."""
    archive_root = save_root / ARCHIVE_DIRNAME
    keep = keep_dir.resolve() if keep_dir is not None else None
    moved = []
    for slug_dir in sorted(_iter_slug_dirs(save_root)):
        if keep is not None and slug_dir.resolve() == keep:
            continue
        try:
            newest = max(slug_dir.glob("CONTEXT-*.md"), key=file_timestamp)
            if file_age_days(newest, now) < days:
                continue
        except (ValueError, FileNotFoundError):
            continue  # no handoffs, or a concurrent prune moved the dir
        target = _move_dir(
            slug_dir, archive_dir_for(archive_root, slug_dir.name, newest)
        )
        if target is not None:
            moved.append(str(target.relative_to(archive_root)))
    return {"days": days, "dirs_moved": moved}


def maybe_prune(save_root: Path, env=None, keep_dir: "Path | None" = None) -> dict:
    """Resolve N from env and save_root/config.toml, then prune. Runs on every
    call. Config errors propagate before any walk. A config.toml skipped for
    lack of tomllib adds "config_note". keep_dir is forwarded to prune()."""
    config_path = save_root / CONFIG_NAME
    days = retention_days(env, config_path)  # raises before any walk
    now = datetime.now()  # noqa: DTZ005 - naive local clock, matches filename timestamps
    summary = prune(save_root, now, days, keep_dir=keep_dir)
    if tomllib is None and config_path.is_file():
        summary["config_note"] = (
            f"{config_path} skipped: reading it needs Python 3.11+ (tomllib)"
        )
    return summary


_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def migrate_archive(save_root: Path, apply: bool = False) -> dict:
    """One-time move of the legacy zArchives/<slug>/ tree into
    zArchives/<YYYY-MM>/<slug>/. Dry-run unless apply. Any zArchives/ child
    named YYYY-MM is a month dir and is never treated as a legacy slug. Only
    CONTEXT-*.md files move; anything else is listed in "left" and keeps its
    dir. A legacy dir left empty is rmdir'ed; a dir that is not empty (or is
    already gone) is kept quietly, and any other rmdir failure propagates.
    Collisions get a .dupN name, and a dry run reserves each planned
    destination so the plan matches what apply does. Paths are relative to
    zArchives/. Returns {"apply", "planned": [[src, dest], ...], "moved",
    "left", "dirs_emptied"}."""
    archive_root = save_root / ARCHIVE_DIRNAME
    out = {"apply": apply, "planned": [], "moved": [], "left": [], "dirs_emptied": []}
    if not archive_root.is_dir():
        return out
    reserved: set = set()  # dry-run destinations already planned
    legacy = sorted(
        c for c in archive_root.iterdir() if c.is_dir() and not _MONTH_RE.match(c.name)
    )
    for slug_dir in legacy:
        for f in sorted(slug_dir.iterdir()):
            if not (f.is_file() and f.match("CONTEXT-*.md")):
                out["left"].append(str(f.relative_to(archive_root)))
                continue
            src = str(f.relative_to(archive_root))
            if not apply:
                dest = _collision_free_dest(
                    archive_dir_for(archive_root, slug_dir.name, f), f.name, reserved
                )
                reserved.add(dest)
                out["planned"].append([src, str(dest.relative_to(archive_root))])
                continue
            target = _archive(f, archive_root, slug_dir.name)
            if target is not None:
                pair = [src, str(target.relative_to(archive_root))]
                out["planned"].append(pair)
                out["moved"].append(pair)
        if apply:
            try:
                slug_dir.rmdir()  # empty-dir removal only
            except OSError as exc:
                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                    raise  # unexpected: surface it rather than report success
                continue
            out["dirs_emptied"].append(slug_dir.name)
    return out


def main(argv=None) -> int:
    """CLI: `retention.py migrate [--apply]` prints the migrate_archive JSON for
    context_schema.SAVE_ROOT. Dry-run by default."""
    import argparse
    import json
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops it
    import context_schema

    parser = argparse.ArgumentParser(prog="retention.py")
    sub = parser.add_subparsers(dest="command", required=True)
    mig = sub.add_parser("migrate", help="move zArchives/<slug>/ into YYYY-MM dirs")
    mig.add_argument(
        "--apply", action="store_true", help="move files (default: dry-run)"
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            migrate_archive(context_schema.SAVE_ROOT, apply=args.apply), indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
