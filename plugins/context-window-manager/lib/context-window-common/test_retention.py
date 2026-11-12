import os
import stat
from datetime import datetime
from pathlib import Path

import pytest
import retention


def _cfg(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_retention_days_default_is_7(tmp_path):
    assert retention.retention_days(env={}, config_path=tmp_path / "x.toml") == 7


def test_retention_days_reads_config(tmp_path):
    cfg = _cfg(tmp_path, "[retention]\ndays = 10\n")
    assert retention.retention_days(env={}, config_path=cfg) == 10


def test_env_overrides_config(tmp_path):
    cfg = _cfg(tmp_path, "[retention]\ndays = 10\n")
    env = {"SCT_CWM_RETENTION_DAYS": "3"}
    assert retention.retention_days(env=env, config_path=cfg) == 3


@pytest.mark.parametrize("raw", ["0", "-3", "x", "soon"])
def test_retention_days_rejects_bad_env(tmp_path, raw):
    with pytest.raises(retention.RetentionConfigError):
        retention.retention_days(
            env={"SCT_CWM_RETENTION_DAYS": raw}, config_path=tmp_path / "x.toml"
        )


@pytest.mark.parametrize(
    "text",
    [
        "[retention]\ndays = 0\n",
        '[retention]\ndays = "3"\n',
        "[retention]\ndays = true\n",
        'retention = "3d"\n',
        "[retention\ndays = \n",
    ],
)
def test_retention_days_rejects_bad_config(tmp_path, text):
    with pytest.raises(retention.RetentionConfigError):
        retention.retention_days(env={}, config_path=_cfg(tmp_path, text))


def test_spawn_only_config_uses_default(tmp_path):
    cfg = _cfg(tmp_path, '[[spawn.stages]]\nsuffix = "A-plan"\n')
    assert retention.retention_days(env={}, config_path=cfg) == 7


def test_no_tomllib_ignores_config(tmp_path, monkeypatch):
    monkeypatch.setattr(retention, "tomllib", None)
    cfg = _cfg(tmp_path, "[retention]\ndays = 10\n")
    assert retention.retention_days(env={}, config_path=cfg) == 7


def test_parse_context_timestamp_from_name():
    ts = retention.parse_context_timestamp("CONTEXT-2026-09-01-08-05-00-ab12cd.md")
    assert ts == datetime(2026, 9, 1, 8, 5, 0)  # noqa: DTZ001 - naive local clock under test


def test_parse_context_timestamp_unparseable_returns_none():
    assert retention.parse_context_timestamp("notes.md") is None


def test_file_age_uses_filename_not_mtime(tmp_path):
    # name says 40 days ago; mtime is now. Filename must win.
    f = tmp_path / "CONTEXT-2026-07-29-00-00-00-aaaaaa.md"
    f.write_text("x")
    age = retention.file_age_days(f, now=datetime(2026, 9, 7, 0, 0, 0))  # noqa: DTZ001
    assert age == pytest.approx(40, abs=1)


def test_file_age_falls_back_to_mtime_when_name_unparseable(tmp_path):
    import os
    import time

    f = tmp_path / "CONTEXT-garbage.md"
    f.write_text("x")
    old = time.time() - 40 * 86400
    os.utime(f, (old, old))
    age = retention.file_age_days(f, now=datetime.now())  # noqa: DTZ005
    assert age == pytest.approx(40, abs=1)


NOW = datetime(2026, 9, 7, 12, 0, 0)  # noqa: DTZ001 - naive local clock under test


def _mk(
    root, slug, ts, suffix="aaaaaa"
):  # helper: create a CONTEXT file under a slug dir
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"CONTEXT-{ts}-{suffix}.md"
    f.write_text("x")
    return f


def _arch(root, month, slug):
    return root / "zArchives" / month / slug


def test_stale_slug_dir_moves_whole_into_newest_month(tmp_path):
    _mk(tmp_path, "r-1", "2026-07-30-00-00-00")
    _mk(tmp_path, "r-1", "2026-08-02-00-00-00", suffix="bbbbbb")
    (tmp_path / "r-1" / "notes.txt").write_text("kept with its dir")
    out = retention.prune(tmp_path, NOW, 7)
    assert out == {"days": 7, "dirs_moved": ["2026-08/r-1"]}
    dest = _arch(tmp_path, "2026-08", "r-1")
    assert sorted(p.name for p in dest.iterdir()) == [
        "CONTEXT-2026-07-30-00-00-00-aaaaaa.md",
        "CONTEXT-2026-08-02-00-00-00-bbbbbb.md",
        "notes.txt",
    ]
    assert not (tmp_path / "r-1").exists()


def test_one_fresh_handoff_keeps_the_whole_dir(tmp_path):
    old = _mk(tmp_path, "r-1", "2026-06-01-00-00-00")
    _mk(tmp_path, "r-1", "2026-09-06-00-00-00", suffix="bbbbbb")
    out = retention.prune(tmp_path, NOW, 7)
    assert out["dirs_moved"] == []
    assert old.exists()
    assert not (tmp_path / "zArchives").exists()


def test_handoff_exactly_n_days_old_is_archived(tmp_path):
    _mk(tmp_path, "r-1", "2026-08-31-12-00-00")  # NOW - 7 days exactly
    assert retention.prune(tmp_path, NOW, 7)["dirs_moved"] == ["2026-08/r-1"]


def test_unparseable_name_uses_mtime(tmp_path):
    d = tmp_path / "r-1"
    d.mkdir()
    f = d / "CONTEXT-handwritten.md"
    f.write_text("x")
    old = datetime(2026, 5, 3, 0, 0, 0).timestamp()  # noqa: DTZ001
    os.utime(f, (old, old))
    assert retention.prune(tmp_path, NOW, 7)["dirs_moved"] == ["2026-05/r-1"]


def test_dir_without_handoffs_is_left_alone(tmp_path):
    (tmp_path / "empty-1").mkdir()
    (tmp_path / "other-1").mkdir()
    (tmp_path / "other-1" / "notes.txt").write_text("x")
    assert retention.prune(tmp_path, NOW, 7)["dirs_moved"] == []
    assert (tmp_path / "empty-1").is_dir() and (tmp_path / "other-1").is_dir()


def test_zarchives_is_never_rescanned(tmp_path):
    arch = _arch(tmp_path, "2026-01", "r-1")
    arch.mkdir(parents=True)
    (arch / "CONTEXT-2026-01-01-00-00-00-aaaaaa.md").write_text("x")
    assert retention.prune(tmp_path, NOW, 7)["dirs_moved"] == []
    assert arch.is_dir()


def test_dot_prefixed_slug_is_pruned(tmp_path):
    _mk(tmp_path, ".claude-1234", "2026-06-01-00-00-00")
    assert retention.prune(tmp_path, NOW, 7)["dirs_moved"] == ["2026-06/.claude-1234"]


def test_archive_dirs_are_owner_only(tmp_path):
    _mk(tmp_path, "r-1", "2026-06-01-00-00-00")
    (tmp_path / "r-1").chmod(0o755)  # e.g. a restored dir with a looser mode
    old_umask = os.umask(0o022)
    try:
        retention.prune(tmp_path, NOW, 7)
    finally:
        os.umask(old_umask)
    for d in (
        tmp_path / "zArchives",
        tmp_path / "zArchives" / "2026-06",
        _arch(tmp_path, "2026-06", "r-1"),
    ):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_existing_month_archive_gets_dup_suffix(tmp_path):
    prior = _arch(tmp_path, "2026-06", "r-1")
    prior.mkdir(parents=True)
    (prior / "CONTEXT-2026-06-01-00-00-00-aaaaaa.md").write_text("first")
    f = _mk(tmp_path, "r-1", "2026-06-01-00-00-00")  # same name, other payload
    f.write_text("second")
    assert retention.prune(tmp_path, NOW, 7)["dirs_moved"] == ["2026-06/r-1.dup1"]
    assert (prior / f.name).read_text() == "first"
    assert (_arch(tmp_path, "2026-06", "r-1.dup1") / f.name).read_text() == "second"


def test_racing_prune_takes_next_suffix_without_clobbering(tmp_path, monkeypatch):
    # Another prune claims r-1's month name between our exists() check and our
    # rename. rename refuses a non-empty target, so we take .dup1 and both
    # payloads survive.
    f = _mk(tmp_path, "r-1", "2026-06-01-00-00-00")
    f.write_text("ours")
    real_rename = os.rename

    def rename(src, dst):
        dst = Path(dst)
        if dst.name == "r-1" and not dst.exists():
            dst.mkdir()
            (dst / "theirs.md").write_text("theirs")
        return real_rename(src, dst)

    monkeypatch.setattr(retention.os, "rename", rename)
    assert retention.prune(tmp_path, NOW, 7)["dirs_moved"] == ["2026-06/r-1.dup1"]
    assert (_arch(tmp_path, "2026-06", "r-1") / "theirs.md").read_text() == "theirs"
    assert (_arch(tmp_path, "2026-06", "r-1.dup1") / f.name).read_text() == "ours"


def test_concurrent_prune_skips_already_moved_dir(tmp_path, monkeypatch):
    _mk(tmp_path, "r-1", "2026-06-01-00-00-00")
    real_rename = os.rename

    def rename(src, dst):
        real_rename(src, tmp_path / "zArchives" / "2026-06" / "r-1")  # the other prune
        return real_rename(src, dst)

    monkeypatch.setattr(retention.os, "rename", rename)
    assert retention.prune(tmp_path, NOW, 7)["dirs_moved"] == []
    assert _arch(tmp_path, "2026-06", "r-1").is_dir()


def test_keep_dir_is_never_moved(tmp_path):
    f = _mk(tmp_path, "r-1", "2026-06-01-00-00-00")
    out = retention.prune(tmp_path, NOW, 7, keep_dir=tmp_path / "r-1")
    assert out["dirs_moved"] == []
    assert f.exists()


def test_maybe_prune_runs_on_every_call(tmp_path):
    (tmp_path / ".last_prune").write_text(f"{datetime.now():%Y-%m-%d}")  # noqa: DTZ005 - a legacy marker is ignored
    _mk(tmp_path, "r-1", "2026-01-01-00-00-00")
    assert retention.maybe_prune(tmp_path, env={})["dirs_moved"] == ["2026-01/r-1"]
    _mk(tmp_path, "r-2", "2026-01-02-00-00-00")
    assert retention.maybe_prune(tmp_path, env={})["dirs_moved"] == ["2026-01/r-2"]


def test_maybe_prune_reads_days_from_config(tmp_path):
    from datetime import timedelta

    (tmp_path / "config.toml").write_text("[retention]\ndays = 30\n")
    now = datetime.now()  # noqa: DTZ005 - maybe_prune reads the naive local clock
    _mk(tmp_path, "r-1", f"{now - timedelta(days=20):%Y-%m-%d-%H-%M-%S}")
    out = retention.maybe_prune(tmp_path, env={})
    assert out == {"days": 30, "dirs_moved": []}


def test_maybe_prune_notes_skipped_config_without_tomllib(tmp_path, monkeypatch):
    monkeypatch.setattr(retention, "tomllib", None)
    (tmp_path / "config.toml").write_text("[retention]\ndays = 1\n")
    out = retention.maybe_prune(tmp_path, env={})
    assert out["days"] == 7
    assert "config_note" in out


def test_maybe_prune_propagates_config_error(tmp_path):
    with pytest.raises(retention.RetentionConfigError):
        retention.maybe_prune(tmp_path, env={"SCT_CWM_RETENTION_DAYS": "0"})


def _legacy(root, slug, *stamps):
    d = root / "zArchives" / slug
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for ts in stamps:
        f = d / f"CONTEXT-{ts}-aaaaaa.md"
        f.write_text(ts)
        files.append(f)
    return files


def test_migrate_dry_run_moves_nothing(tmp_path):
    files = _legacy(tmp_path, "slugA", "2026-07-01-00-00-00", "2026-08-01-00-00-00")
    out = retention.migrate_archive(tmp_path)
    assert out["apply"] is False
    assert len(out["planned"]) == 2
    assert out["moved"] == []
    assert all(f.exists() for f in files)
    assert not (tmp_path / "zArchives" / "2026-07").exists()


def test_migrate_dry_run_reserves_planned_destinations(tmp_path):
    # A legacy original and its .dup1 variant both map to July, and the dated
    # archive already holds the original. The preview must plan distinct
    # destinations, move nothing, and match what apply then does.
    legacy = tmp_path / "zArchives" / "slugA"
    legacy.mkdir(parents=True)
    name = "CONTEXT-2026-07-01-00-00-00-aaaaaa.md"
    dup1 = "CONTEXT-2026-07-01-00-00-00-aaaaaa.dup1.md"
    dup2 = "CONTEXT-2026-07-01-00-00-00-aaaaaa.dup2.md"
    (legacy / name).write_text("legacy")
    (legacy / dup1).write_text("legacy dup")
    july = datetime(2026, 7, 1, 0, 0, 0).timestamp()  # noqa: DTZ001
    os.utime(legacy / dup1, (july, july))  # unparseable name: month from mtime
    dated = _arch(tmp_path, "2026-07", "slugA")
    dated.mkdir(parents=True)
    (dated / name).write_text("ORIGINAL")
    preview = retention.migrate_archive(tmp_path)
    assert preview["planned"] == [
        [f"slugA/{dup1}", f"2026-07/slugA/{dup1}"],
        [f"slugA/{name}", f"2026-07/slugA/{dup2}"],
    ]
    assert sorted(p.name for p in dated.iterdir()) == [name]
    assert (legacy / name).exists() and (legacy / dup1).exists()
    applied = retention.migrate_archive(tmp_path, apply=True)
    assert applied["moved"] == preview["planned"]
    assert (dated / name).read_text() == "ORIGINAL"
    assert (dated / dup1).read_text() == "legacy dup"
    assert (dated / dup2).read_text() == "legacy"


def test_competing_archivers_claim_distinct_destinations(tmp_path, monkeypatch):
    # A rival archiver (a live prune of a restored copy) races the migration
    # for the same dated name after the migration picked it but before its
    # move. Each claims its name atomically, so the rival takes .dup1 and both
    # payloads survive; a check-then-move would let the later move clobber.
    (src,) = _legacy(tmp_path, "slugA", "2026-07-01-00-00-00")
    rival_dir = tmp_path / "rival"
    rival_dir.mkdir()
    rival = rival_dir / src.name
    rival.write_text("RIVAL")
    real_move = retention.shutil.move
    state = {"busy": False, "rival": None}

    def move(s, d):
        if not state["busy"]:
            state["busy"] = True
            state["rival"] = retention._archive(rival, tmp_path / "zArchives", "slugA")
        return real_move(s, d)

    monkeypatch.setattr(retention.shutil, "move", move)
    out = retention.migrate_archive(tmp_path, apply=True)
    dest = _arch(tmp_path, "2026-07", "slugA")
    assert out["moved"] == [[f"slugA/{src.name}", f"2026-07/slugA/{src.name}"]]
    assert state["rival"] == dest / "CONTEXT-2026-07-01-00-00-00-aaaaaa.dup1.md"
    assert (dest / src.name).read_text() == "2026-07-01-00-00-00"
    assert state["rival"].read_text() == "RIVAL"


def test_migrate_apply_moves_into_month_dirs(tmp_path):
    jul, aug = _legacy(tmp_path, "slugA", "2026-07-01-00-00-00", "2026-08-01-00-00-00")
    out = retention.migrate_archive(tmp_path, apply=True)
    assert out["apply"] is True
    assert (_arch(tmp_path, "2026-07", "slugA") / jul.name).read_text() == (
        "2026-07-01-00-00-00"
    )
    assert (_arch(tmp_path, "2026-08", "slugA") / aug.name).exists()
    assert not (tmp_path / "zArchives" / "slugA").exists()
    assert out["dirs_emptied"] == ["slugA"]
    for d in (tmp_path / "zArchives" / "2026-07", _arch(tmp_path, "2026-07", "slugA")):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_migrate_skips_existing_month_dirs(tmp_path):
    dated = _arch(tmp_path, "2026-09", "slugB")
    dated.mkdir(parents=True)
    f = dated / "CONTEXT-2026-09-01-00-00-00-aaaaaa.md"
    f.write_text("x")
    out = retention.migrate_archive(tmp_path, apply=True)
    assert out["planned"] == []
    assert f.exists()


def test_migrate_collision_gets_dup_suffix(tmp_path):
    (src,) = _legacy(tmp_path, "slugA", "2026-07-01-00-00-00")
    dest = _arch(tmp_path, "2026-07", "slugA")
    dest.mkdir(parents=True)
    (dest / src.name).write_text("ORIGINAL")
    retention.migrate_archive(tmp_path, apply=True)
    assert (dest / src.name).read_text() == "ORIGINAL"
    assert (dest / "CONTEXT-2026-07-01-00-00-00-aaaaaa.dup1.md").read_text() == (
        "2026-07-01-00-00-00"
    )


def test_migrate_failed_move_releases_empty_reservation(tmp_path, monkeypatch):
    # The move fails before any content lands (e.g. PermissionError on both the
    # rename and the copy fallback). The error propagates, the source stays,
    # and the empty placeholder is removed so no hollow CONTEXT-*.md remains.
    (src,) = _legacy(tmp_path, "slugA", "2026-07-01-00-00-00")

    def move(s, d):
        raise PermissionError(s)

    monkeypatch.setattr(retention.shutil, "move", move)
    with pytest.raises(PermissionError):
        retention.migrate_archive(tmp_path, apply=True)
    assert src.read_text() == "2026-07-01-00-00-00"
    assert list(_arch(tmp_path, "2026-07", "slugA").iterdir()) == []


def test_migrate_failed_move_keeps_transferred_content(tmp_path, monkeypatch):
    # shutil.move can fall back to copy-then-delete. If the copy lands and the
    # source delete then fails, the transferred handoff must survive cleanup.
    (src,) = _legacy(tmp_path, "slugA", "2026-07-01-00-00-00")

    def move(s, d):
        Path(d).write_text(Path(s).read_text())  # the copy landed
        raise PermissionError(s)  # but removing the source failed

    monkeypatch.setattr(retention.shutil, "move", move)
    with pytest.raises(PermissionError):
        retention.migrate_archive(tmp_path, apply=True)
    dest = _arch(tmp_path, "2026-07", "slugA") / src.name
    assert dest.read_text() == "2026-07-01-00-00-00"
    assert src.exists()


def test_migrate_skips_source_moved_by_another_run(tmp_path, monkeypatch):
    # The source vanishes between selection and move (another run took it).
    # Migration skips it, reports no move, and releases its placeholder.
    _legacy(tmp_path, "slugA", "2026-07-01-00-00-00")
    elsewhere = tmp_path / "elsewhere.md"
    real_move = retention.shutil.move

    def move(s, d):
        real_move(s, str(elsewhere))  # the other run
        raise FileNotFoundError(s)

    monkeypatch.setattr(retention.shutil, "move", move)
    out = retention.migrate_archive(tmp_path, apply=True)
    assert out["moved"] == []
    assert elsewhere.read_text() == "2026-07-01-00-00-00"
    assert list(_arch(tmp_path, "2026-07", "slugA").iterdir()) == []


def test_migrate_leaves_non_context_files(tmp_path):
    _legacy(tmp_path, "slugA", "2026-07-01-00-00-00")
    legacy = tmp_path / "zArchives" / "slugA"
    (legacy / "notes.txt").write_text("keep")
    (legacy / "nested").mkdir()
    out = retention.migrate_archive(tmp_path, apply=True)
    assert sorted(out["left"]) == ["slugA/nested", "slugA/notes.txt"]
    assert (legacy / "notes.txt").exists()
    assert out["dirs_emptied"] == []


def test_migrate_is_idempotent(tmp_path):
    _legacy(tmp_path, "slugA", "2026-07-01-00-00-00", "2026-08-01-00-00-00")
    retention.migrate_archive(tmp_path, apply=True)
    again = retention.migrate_archive(tmp_path, apply=True)
    assert again["planned"] == [] and again["moved"] == []


def test_migrate_without_archive_is_empty(tmp_path):
    out = retention.migrate_archive(tmp_path)
    assert out["planned"] == [] and out["left"] == []


def test_migrate_cli_dry_run_by_default(tmp_path):
    import json
    import os
    import subprocess
    import sys

    save_root = tmp_path / ".config" / "sct" / "context-window-manager"
    (f,) = _legacy(save_root, "slugA", "2026-07-01-00-00-00")
    env = {k: v for k, v in os.environ.items() if k != "CWM_SAVE_ROOT"}
    env["HOME"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-I", retention.__file__, "migrate"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["apply"] is False
    assert len(out["planned"]) == 1
    assert f.exists()


def test_maybe_prune_default_keeps_last_7_days(tmp_path):
    from datetime import timedelta

    now = datetime.now()  # noqa: DTZ005 - maybe_prune reads the naive local clock
    _mk(tmp_path, "old-1", f"{now - timedelta(days=8):%Y-%m-%d-%H-%M-%S}")
    recent = _mk(tmp_path, "new-1", f"{now - timedelta(days=6):%Y-%m-%d-%H-%M-%S}")
    out = retention.maybe_prune(tmp_path, env={})
    assert [m.split("/")[1] for m in out["dirs_moved"]] == ["old-1"]
    assert recent.exists()
