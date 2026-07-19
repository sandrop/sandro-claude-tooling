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
