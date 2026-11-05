from pathlib import Path

import pytest
import retention


def test_retention_days_default_when_unset():
    assert retention.retention_days(env={}) == 30


def test_retention_days_reads_env():
    assert retention.retention_days(env={"SCT_CWM_RETENTION_DAYS": "7"}) == 7


def test_retention_days_rejects_non_int():
    with pytest.raises(retention.RetentionConfigError):
        retention.retention_days(env={"SCT_CWM_RETENTION_DAYS": "soon"})


def test_retention_days_rejects_zero_and_negative():
    for bad in ("0", "-5"):
        with pytest.raises(retention.RetentionConfigError):
            retention.retention_days(env={"SCT_CWM_RETENTION_DAYS": bad})


from datetime import datetime


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


def _mk(root, slug, ts):  # helper: create a CONTEXT file under a slug dir
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"CONTEXT-{ts}-aaaaaa.md"
    f.write_text("x")
    return f


def test_stale_file_moves_but_live_stays(tmp_path):
    slug = "repo-deadbeef"
    stale = _mk(tmp_path, slug, "2026-07-01-00-00-00")  # >30d
    live = _mk(tmp_path, slug, "2026-09-06-00-00-00")  # <30d
    retention.prune(tmp_path, now=NOW, days=30)
    assert not stale.exists()
    assert (tmp_path / "zArchives" / slug / stale.name).exists()
    assert live.exists()  # untouched
    assert (tmp_path / slug).is_dir()  # dir kept (has live file)


def test_dir_moves_when_all_files_stale(tmp_path):
    slug = "old-cafef00d"
    _mk(tmp_path, slug, "2026-06-01-00-00-00")
    _mk(tmp_path, slug, "2026-06-02-00-00-00")
    retention.prune(tmp_path, now=NOW, days=30)
    assert not (tmp_path / slug).exists()
    assert (tmp_path / "zArchives" / slug).is_dir()
    assert len(list((tmp_path / "zArchives" / slug).glob("CONTEXT-*.md"))) == 2


def test_zarchives_is_never_rescanned(tmp_path):
    # a stale file already in zArchives must not move again / recurse
    arch = tmp_path / "zArchives" / "x-1234"
    arch.mkdir(parents=True)
    (arch / "CONTEXT-2026-01-01-00-00-00-aaaaaa.md").write_text("x")
    summary = retention.prune(tmp_path, now=NOW, days=30)
    assert summary["files_moved"] == []
    assert summary["dirs_moved"] == []


def test_dot_prefixed_slug_is_pruned(tmp_path):
    # repo_slug for a dot-named repo toplevel (e.g. ~/.claude -> .claude-<hash>)
    # is a legitimate dot-prefixed slug and must not be skipped by the walk.
    slug = ".claude-deadbeef"
    stale = _mk(tmp_path, slug, "2026-06-01-00-00-00")
    summary = retention.prune(tmp_path, now=NOW, days=30)
    assert not stale.exists()
    assert (tmp_path / "zArchives" / slug).is_dir()
    assert slug in summary["dirs_moved"]


def test_all_stale_merges_into_existing_archive_dir(tmp_path):
    # A prior day's file-level move already created zArchives/<slug>/. Today
    # every remaining file is stale: a whole-dir move would collide with the
    # existing dest and raise, so the stale files must merge in file-by-file.
    slug = "repo-deadbeef"
    prior = tmp_path / "zArchives" / slug
    prior.mkdir(parents=True)
    (prior / "CONTEXT-2026-05-01-00-00-00-aaaaaa.md").write_text("old")
    now_stale = _mk(tmp_path, slug, "2026-07-01-00-00-00")  # >30d, still live-side
    summary = retention.prune(tmp_path, now=NOW, days=30)
    assert not now_stale.exists()
    assert (prior / now_stale.name).exists()
    assert now_stale.name in summary["files_moved"]
    assert summary["dirs_moved"] == []
    # The now-empty live slug dir is left in place (move-only; no rm).
    assert list((tmp_path / slug).glob("CONTEXT-*.md")) == []


import stat


def test_archive_dirs_are_owner_only(tmp_path):
    # Archived handoffs must be no more exposed than the live 0o700 slug dirs.
    # File-merge path (a live file remains) creates zArchives/ and its slug dir.
    slug = "repo-deadbeef"
    _mk(tmp_path, slug, "2026-07-01-00-00-00")  # stale -> archived
    _mk(tmp_path, slug, "2026-09-06-00-00-00")  # live -> slug dir stays
    retention.prune(tmp_path, now=NOW, days=30)
    archive_root = tmp_path / "zArchives"
    dest = archive_root / slug
    assert dest.is_dir()
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700
    assert stat.S_IMODE(archive_root.stat().st_mode) == 0o700


def test_existing_archive_file_is_never_overwritten(tmp_path):
    # A same-named file already in the archive (e.g. after a manual restore)
    # must not be clobbered: move-only never deletes. The stale live file is
    # archived under a distinct name, and the prior archived content survives.
    slug = "repo-deadbeef"
    prior = tmp_path / "zArchives" / slug
    prior.mkdir(parents=True)
    name = "CONTEXT-2026-07-01-00-00-00-aaaaaa.md"
    (prior / name).write_text("ORIGINAL")
    # A live-side file with the SAME name is now stale and would collide.
    live_dir = tmp_path / slug
    live_dir.mkdir(parents=True)
    (live_dir / name).write_text("NEWER")
    summary = retention.prune(tmp_path, now=NOW, days=30)
    assert (prior / name).read_text() == "ORIGINAL"  # untouched
    archived = sorted(prior.glob("CONTEXT-*"))
    assert len(archived) == 2  # both retained, nothing deleted
    moved = summary["files_moved"]
    assert moved and moved[0] != name  # collision-safe distinct name
    assert (prior / moved[0]).read_text() == "NEWER"
    assert not (live_dir / name).exists()  # source was moved, not left behind


def test_concurrent_prune_skips_already_moved_file(tmp_path, monkeypatch):
    # Simulate a racing prune: the stale source vanishes between selection and
    # move. The prune must skip it rather than raise (move-only => no loss).
    slug = "repo-deadbeef"
    stale = _mk(tmp_path, slug, "2026-07-01-00-00-00")
    _mk(tmp_path, slug, "2026-09-06-00-00-00")  # live keeps the file-merge path

    real_move = retention.shutil.move
    calls = {"n": 0}

    def racing_move(src, dst):
        # First call: pretend a concurrent prune already moved the source.
        if calls["n"] == 0 and Path(src) == stale:
            calls["n"] += 1
            Path(src).unlink()
            raise FileNotFoundError(src)
        return real_move(src, dst)

    monkeypatch.setattr(retention.shutil, "move", racing_move)
    # The lost-race source is removed before we call prune's move, so prune's
    # own f.exists() guard skips it: no exception, empty move list for it.
    summary = retention.prune(tmp_path, now=NOW, days=30)
    assert stale.name not in summary["files_moved"]


def test_keep_dir_is_never_relocated_wholesale(tmp_path):
    # The active handoff dir, even when every handoff under it is stale and no
    # prior archive exists, keeps its directory (the pending write's already
    # reported target) while its stale files are still archived file-by-file.
    slug = "active-deadbeef"
    active = tmp_path / slug
    s1 = _mk(tmp_path, slug, "2026-06-01-00-00-00")
    s2 = _mk(tmp_path, slug, "2026-06-02-00-00-00")
    summary = retention.prune(tmp_path, now=NOW, days=30, keep_dir=active)
    assert active.is_dir()  # dir preserved, not moved wholesale
    assert summary["dirs_moved"] == []
    assert set(summary["files_moved"]) == {s1.name, s2.name}
    assert not s1.exists() and not s2.exists()
    arch = tmp_path / "zArchives" / slug
    assert (arch / s1.name).exists() and (arch / s2.name).exists()


def test_non_keep_dir_still_moves_wholesale(tmp_path):
    # A different all-stale slug (not the active dir) still relocates wholesale.
    active = tmp_path / "active-1111"
    active.mkdir()
    other = "other-2222"
    _mk(tmp_path, other, "2026-06-01-00-00-00")
    summary = retention.prune(tmp_path, now=NOW, days=30, keep_dir=active)
    assert not (tmp_path / other).exists()
    assert other in summary["dirs_moved"]


from datetime import date


def test_maybe_prune_runs_first_time_and_stamps_marker(tmp_path):
    _mk(tmp_path, "r-1111", "2026-06-01-00-00-00")
    out = retention.maybe_prune(tmp_path, today=date(2026, 9, 7), env={})
    assert "skipped" not in out
    marker = tmp_path / ".last_prune"
    assert marker.read_text().strip() == "2026-09-07"


def test_maybe_prune_skips_when_already_pruned_today(tmp_path):
    (tmp_path / ".last_prune").write_text("2026-09-07")
    stale = _mk(tmp_path, "r-2222", "2026-06-01-00-00-00")
    out = retention.maybe_prune(tmp_path, today=date(2026, 9, 7), env={})
    assert out["skipped"] == "already-pruned-today"
    assert stale.exists()  # gate prevented the walk


def test_maybe_prune_runs_again_next_day(tmp_path):
    (tmp_path / ".last_prune").write_text("2026-09-06")
    stale = _mk(tmp_path, "r-3333", "2026-06-01-00-00-00")
    out = retention.maybe_prune(tmp_path, today=date(2026, 9, 7), env={})
    assert "skipped" not in out
    assert not stale.exists()


def test_maybe_prune_propagates_config_error(tmp_path):
    with pytest.raises(retention.RetentionConfigError):
        retention.maybe_prune(
            tmp_path, today=date(2026, 9, 7), env={"SCT_CWM_RETENTION_DAYS": "nope"}
        )
