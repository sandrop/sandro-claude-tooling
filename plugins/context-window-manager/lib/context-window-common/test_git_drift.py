import subprocess
import git_drift


def _run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path):
    _run(tmp_path, "git", "init", "-q", "-b", "main")
    _run(tmp_path, "git", "config", "user.email", "t@t")
    _run(tmp_path, "git", "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("1")
    _run(tmp_path, "git", "add", "-A")
    _run(tmp_path, "git", "commit", "-qm", "c1")


def test_non_git_dir_reports_is_git_false(tmp_path):
    d = git_drift.compute_drift("deadbeef", "main", str(tmp_path))
    assert d["is_git"] is False


def test_ahead_count_after_new_commit(tmp_path):
    _init_repo(tmp_path)
    stored = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    (tmp_path / "b.txt").write_text("2")
    _run(tmp_path, "git", "add", "-A")
    _run(tmp_path, "git", "commit", "-qm", "c2")
    d = git_drift.compute_drift(stored, "main", str(tmp_path))
    assert d["is_git"] is True
    assert d["stored_resolvable"] is True
    assert d["ahead"] == 1


def test_missing_branch_flagged(tmp_path):
    _init_repo(tmp_path)
    stored = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    d = git_drift.compute_drift(stored, "feat/gone", str(tmp_path))
    assert d["branch_exists"] is False


def test_diverged_repo_reports_both_ahead_and_behind(tmp_path):
    # Base commit c1, then a branch commit (stored) and a main commit (HEAD)
    # that share the base. rev-list stored...HEAD must report behind==1 (the
    # branch commit) and ahead==1 (the main commit); this catches a
    # behind/ahead transposition.
    _init_repo(tmp_path)
    _run(tmp_path, "git", "checkout", "-q", "-b", "side")
    (tmp_path / "b.txt").write_text("2")
    _run(tmp_path, "git", "add", "-A")
    _run(tmp_path, "git", "commit", "-qm", "c2-side")
    stored = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    _run(tmp_path, "git", "checkout", "-q", "main")
    (tmp_path / "c.txt").write_text("3")
    _run(tmp_path, "git", "add", "-A")
    _run(tmp_path, "git", "commit", "-qm", "c2-main")
    d = git_drift.compute_drift(stored, "main", str(tmp_path))
    assert d["behind"] == 1
    assert d["ahead"] == 1


def test_none_stored_sha_reports_no_sha_note(tmp_path):
    _init_repo(tmp_path)
    d = git_drift.compute_drift(None, None, str(tmp_path))
    assert d["stored_resolvable"] is False
    assert d["note"] == "no SHA recorded in handoff metadata"


def test_non_hex_stored_sha_is_unresolvable(tmp_path):
    _init_repo(tmp_path)
    d = git_drift.compute_drift("--help", "main", str(tmp_path))
    assert d["stored_resolvable"] is False


def test_git_failure_on_a_later_call_is_contained(tmp_path, monkeypatch):
    # A timeout or missing-git on any call after the first must degrade to a
    # structured result, not propagate out of compute_drift.
    _init_repo(tmp_path)
    real_git = git_drift._git
    calls = {"n": 0}

    def flaky(cwd, *args):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise subprocess.TimeoutExpired(cmd="git", timeout=10)
        return real_git(cwd, *args)

    monkeypatch.setattr(git_drift, "_git", flaky)
    d = git_drift.compute_drift("deadbeef", "main", str(tmp_path))
    assert d["is_git"] is True
    assert d["note"] == "git unavailable"


def _short_head(tmp_path):
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_verify_commits_resolvable_and_missing(tmp_path):
    # Stored entries ride as `<shortsha> <subject>`; verify_commits must split
    # the leading sha token before probing, and flag a vanished commit.
    _init_repo(tmp_path)
    short = _short_head(tmp_path)
    res = git_drift.verify_commits(
        [f"{short} c1", "deadbee gone after rebase"], str(tmp_path)
    )
    assert res[0] == {"sha": short, "subject": "c1", "resolvable": True}
    assert res[1] == {
        "sha": "deadbee",
        "subject": "gone after rebase",
        "resolvable": False,
    }


def test_verify_commits_empty_list(tmp_path):
    _init_repo(tmp_path)
    assert git_drift.verify_commits([], str(tmp_path)) == []


def test_verify_commits_non_git_dir_all_unresolvable(tmp_path):
    res = git_drift.verify_commits(["abc1234 subject"], str(tmp_path))
    assert res[0]["sha"] == "abc1234"
    assert res[0]["resolvable"] is False


def test_recompute_remote_no_upstream_degrades(tmp_path):
    _init_repo(tmp_path)
    r = git_drift.recompute_remote(str(tmp_path))
    assert r["ahead"] is None
    assert r["behind"] is None
    assert r["note"]  # explains there is no upstream


def test_recompute_remote_non_git_dir_degrades(tmp_path):
    r = git_drift.recompute_remote(str(tmp_path))
    assert r["ahead"] is None
    assert r["note"] == "not a git repo"


def _init_repo_with_remote(tmp_path, *, set_upstream: bool):
    """Repo with a bare remote whose `main` holds our commit. When
    set_upstream is False the push omits `-u`, reproducing the pushed-without-
    tracking case the reviewer flagged (a branch on the remote with no local
    `@{upstream}`)."""
    _init_repo(tmp_path)
    bare = tmp_path.parent / f"{tmp_path.name}-remote.git"
    _run(tmp_path.parent, "git", "init", "-q", "--bare", str(bare))
    _run(tmp_path, "git", "remote", "add", "origin", str(bare))
    if set_upstream:
        _run(tmp_path, "git", "push", "-q", "-u", "origin", "main")
    else:
        _run(tmp_path, "git", "push", "-q", "origin", "main")


def test_recompute_remote_reports_upstream_and_pushed(tmp_path):
    # Tracking upstream configured and HEAD fully pushed: ahead 0, pushed yes,
    # and the resolved upstream ref is surfaced for the caller to compare.
    _init_repo_with_remote(tmp_path, set_upstream=True)
    r = git_drift.recompute_remote(str(tmp_path))
    assert r["ahead"] == 0
    assert r["behind"] == 0
    assert r["upstream"] == "origin/main"
    assert r["pushed"] == "yes"


def test_recompute_remote_resolves_pushed_branch_without_tracking(tmp_path):
    # Pushed without -u: no local @{upstream}. The old recompute only asked for
    # @{upstream} and reported unknown. Reusing capture's prober, resume now
    # falls back to the same-named ref on a configured remote and resolves the
    # branch as pushed, matching what capture recorded.
    _init_repo_with_remote(tmp_path, set_upstream=False)
    r = git_drift.recompute_remote(str(tmp_path))
    assert r["ahead"] == 0
    assert r["behind"] == 0
    assert r["upstream"] == "origin/main"
    assert r["pushed"] == "yes"


def test_recompute_remote_stored_upstream_is_last_resort(tmp_path):
    # The remote branch was pushed under a different name, so the same-named
    # fallback finds nothing. The upstream capture recorded (origin/oldname)
    # rides in as the last resort so the count is still recomputed, not unknown.
    _init_repo(tmp_path)
    bare = tmp_path.parent / f"{tmp_path.name}-remote.git"
    _run(tmp_path.parent, "git", "init", "-q", "--bare", str(bare))
    _run(tmp_path, "git", "remote", "add", "origin", str(bare))
    _run(tmp_path, "git", "push", "-q", "origin", "main:oldname")

    without = git_drift.recompute_remote(str(tmp_path))
    assert without["ahead"] is None  # no same-named origin/main ref exists

    carried = git_drift.recompute_remote(
        str(tmp_path), stored_upstream="origin/oldname"
    )
    assert carried["ahead"] == 0
    assert carried["upstream"] == "origin/oldname"
    assert carried["pushed"] == "yes"


def test_recompute_remote_stored_upstream_that_no_longer_exists_is_unknown(tmp_path):
    # A stored upstream ref that is gone from the repo must not silently read
    # as "even": it degrades to unknown counts with a note.
    _init_repo(tmp_path)
    r = git_drift.recompute_remote(str(tmp_path), stored_upstream="origin/gone")
    assert r["ahead"] is None
    assert r["behind"] is None
    assert r["note"]
