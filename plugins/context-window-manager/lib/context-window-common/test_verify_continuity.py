# test_verify_continuity.py - resume-side verification of the expanded handoff
# metadata fields. Consumes the parse_metadata_block dict shape.
import subprocess

from verify_continuity import verify_continuity


def _run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path):
    _run(tmp_path, "git", "init", "-q", "-b", "main")
    _run(tmp_path, "git", "config", "user.email", "t@t")
    _run(tmp_path, "git", "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("1")
    _run(tmp_path, "git", "add", "-A")
    _run(tmp_path, "git", "commit", "-qm", "c1")


def _short_head(tmp_path):
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_cwd_assertion_flags_mismatch():
    meta = {
        "cwd": "/repo/a",
        "cobtask_path": None,
        "tdd_plan_path": None,
        "pr_url": None,
        "pr_number": None,
        "adrs": [],
        "commits": [],
    }
    res = verify_continuity(meta, cwd="/repo/b", gh_runner=lambda *a: None)
    assert res["cwd"] == {"stored": "/repo/a", "current": "/repo/b", "match": False}


def test_path_existence_marks_each_kind(tmp_path):
    real = tmp_path / "task.md"
    real.write_text("x")
    missing = tmp_path / "gone.md"
    real_adr = tmp_path / "adr.md"
    real_adr.write_text("y")
    missing_adr = tmp_path / "gone-adr.md"
    meta = {
        "cwd": str(tmp_path),
        "cobtask_path": str(real),
        "tdd_plan_path": str(missing),
        "pr_url": None,
        "pr_number": None,
        "adrs": [str(real_adr), str(missing_adr)],
        "commits": [],
    }
    res = verify_continuity(meta, cwd=str(tmp_path), gh_runner=lambda *a: None)
    assert res["paths"] == [
        {"kind": "cobtask", "path": str(real), "exists": True},
        {"kind": "tdd_plan", "path": str(missing), "exists": False},
        {"kind": "adr", "path": str(real_adr), "exists": True},
        {"kind": "adr", "path": str(missing_adr), "exists": False},
    ]


def test_path_existence_skips_absent_scalars(tmp_path):
    meta = {
        "cwd": str(tmp_path),
        "cobtask_path": None,
        "tdd_plan_path": None,
        "pr_url": None,
        "pr_number": None,
        "adrs": [],
        "commits": [],
    }
    res = verify_continuity(meta, cwd=str(tmp_path), gh_runner=lambda *a: None)
    assert res["paths"] == []


def _pr_meta(**over):
    base = {
        "cwd": "/repo",
        "cobtask_path": None,
        "tdd_plan_path": None,
        "pr_number": "12",
        "pr_url": "https://github.com/o/r/pull/12",
        "adrs": [],
        "commits": [],
    }
    base.update(over)
    return base


def test_pr_state_requeried_surfaces_current_state():
    res = verify_continuity(
        _pr_meta(), cwd="/repo", gh_runner=lambda *a: {"state": "MERGED"}
    )
    assert res["pr"]["number"] == "12"
    assert res["pr"]["url"] == "https://github.com/o/r/pull/12"
    assert res["pr"]["current_state"] == "MERGED"
    assert res["pr"]["note"] == ""


def test_pr_check_degrades_when_runner_returns_none():
    res = verify_continuity(_pr_meta(), cwd="/repo", gh_runner=lambda *a: None)
    assert res["pr"]["current_state"] is None
    assert res["pr"]["note"]  # non-empty structured note, no exception


def test_pr_check_degrades_when_runner_raises():
    def boom(*a):
        raise FileNotFoundError("gh not installed")

    res = verify_continuity(_pr_meta(), cwd="/repo", gh_runner=boom)
    assert res["pr"]["current_state"] is None
    assert res["pr"]["note"]


def test_pr_state_queried_by_recorded_number_not_current_branch():
    # The recorded PR is #12. The runner must be asked for #12 explicitly, with
    # the recorded pr_url so it can bind to the recorded repository, so resuming
    # on a different branch (or a different repo) cannot substitute that branch's
    # PR state.
    calls = []

    def runner(cwd, number, url):
        calls.append((cwd, number, url))
        return {"state": "OPEN", "number": 12, "url": "https://github.com/o/r/pull/12"}

    res = verify_continuity(_pr_meta(), cwd="/repo", gh_runner=runner)
    assert calls == [("/repo", "12", "https://github.com/o/r/pull/12")]
    assert res["pr"]["current_state"] == "OPEN"
    assert res["pr"]["note"] == ""


def test_pr_mismatch_when_resolved_pr_differs_from_recorded():
    # If the resolved PR number is not the recorded one, the state is not
    # trustworthy evidence for the recorded PR: current_state is withheld and
    # the note explains the mismatch.
    def runner(cwd, number, url):
        return {
            "state": "MERGED",
            "number": 99,
            "url": "https://github.com/o/r/pull/99",
        }

    res = verify_continuity(_pr_meta(), cwd="/repo", gh_runner=runner)
    assert res["pr"]["current_state"] is None
    assert "99" in res["pr"]["note"] and "12" in res["pr"]["note"]


def test_pr_mismatch_when_resolved_url_differs_from_recorded():
    # PR numbers are unique only within a repo. A different repository that also
    # has a PR #12 must NOT be reported as the recorded PR's state: when the
    # resolved url does not match the recorded pr_url, the state is withheld and
    # the repo mismatch is noted, even though the numbers agree.
    def runner(cwd, number, url):
        return {
            "state": "MERGED",
            "number": 12,
            "url": "https://github.com/other/repo/pull/12",
        }

    res = verify_continuity(_pr_meta(), cwd="/repo", gh_runner=runner)
    assert res["pr"]["current_state"] is None
    assert "other/repo" in res["pr"]["note"]
    assert "o/r" in res["pr"]["note"]


def test_default_runner_binds_query_to_recorded_repo(monkeypatch):
    # The default runner must derive owner/repo from the recorded pr_url and pass
    # `gh pr view <number> --repo owner/repo`, so the query is bound to the
    # recorded repository rather than whatever repo the resume cwd sits in.
    import verify_continuity as vc

    captured = {}

    class _Result:
        returncode = 0
        stdout = '{"state":"OPEN","number":12,"url":"https://github.com/o/r/pull/12"}'

    def fake_run(args, **kwargs):
        captured["args"] = tuple(args)
        return _Result()

    monkeypatch.setattr(vc.subprocess, "run", fake_run)
    data = vc._default_gh_runner("/repo", "12", "https://github.com/o/r/pull/12")
    assert "--repo" in captured["args"]
    assert "o/r" in captured["args"]
    assert data["number"] == 12


def test_pr_degrades_when_recorded_pr_unresolvable():
    res = verify_continuity(_pr_meta(), cwd="/repo", gh_runner=lambda *a: None)
    assert res["pr"]["current_state"] is None
    assert "12" in res["pr"]["note"]


def test_pr_check_absent_when_no_pr_recorded():
    res = verify_continuity(
        _pr_meta(pr_number=None, pr_url=None),
        cwd="/repo",
        gh_runner=lambda *a: {"state": "OPEN"},
    )
    assert res["pr"] == {
        "number": None,
        "url": None,
        "current_state": None,
        "note": "no PR recorded in handoff metadata",
    }


def _repo_meta(tmp_path, **over):
    base = {
        "cwd": str(tmp_path),
        "cobtask_path": None,
        "tdd_plan_path": None,
        "pr_number": None,
        "pr_url": None,
        "working_tree": None,
        "remote_ahead": None,
        "remote_behind": None,
        "adrs": [],
        "commits": [],
    }
    base.update(over)
    return base


def test_working_tree_divergence_flagged(tmp_path):
    _init_repo(tmp_path)  # clean working tree
    meta = _repo_meta(tmp_path, working_tree="dirty")
    res = verify_continuity(meta, cwd=str(tmp_path), gh_runner=lambda *a: None)
    assert res["working_tree"]["stored"] == "dirty"
    assert res["working_tree"]["current"] == "clean"
    assert res["working_tree"]["diverged"] is True


def test_working_tree_no_stored_value_not_diverged(tmp_path):
    _init_repo(tmp_path)
    meta = _repo_meta(tmp_path, working_tree=None)
    res = verify_continuity(meta, cwd=str(tmp_path), gh_runner=lambda *a: None)
    assert res["working_tree"]["diverged"] is False
    assert res["working_tree"]["note"]  # explains there was no stored value


def test_commits_resolvability_wired(tmp_path):
    _init_repo(tmp_path)
    short = _short_head(tmp_path)
    meta = _repo_meta(tmp_path, commits=[f"{short} c1", "deadbee gone"])
    res = verify_continuity(meta, cwd=str(tmp_path), gh_runner=lambda *a: None)
    assert res["commits"][0]["resolvable"] is True
    assert res["commits"][1]["resolvable"] is False


def test_remote_recompute_wired_no_upstream(tmp_path):
    _init_repo(tmp_path)
    meta = _repo_meta(tmp_path)
    res = verify_continuity(meta, cwd=str(tmp_path), gh_runner=lambda *a: None)
    assert res["remote"]["current_ahead"] is None
    assert res["remote"]["current_behind"] is None
    assert res["remote"]["note"]


def test_remote_surfaces_upstream_and_pushed_and_flags_upstream_change(tmp_path):
    # The stored upstream/push-state must ride into the result so a changed
    # upstream is caught, not silently reported as unchanged. Here the repo has
    # no tracking upstream, but the handoff carried origin/main; the recompute
    # falls back to it, and the different stored upstream string is flagged.
    _run(tmp_path, "git", "init", "-q", "-b", "main")
    _run(tmp_path, "git", "config", "user.email", "t@t")
    _run(tmp_path, "git", "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("1")
    _run(tmp_path, "git", "add", "-A")
    _run(tmp_path, "git", "commit", "-qm", "c1")
    bare = tmp_path.parent / f"{tmp_path.name}-remote.git"
    _run(tmp_path.parent, "git", "init", "-q", "--bare", str(bare))
    _run(tmp_path, "git", "remote", "add", "origin", str(bare))
    _run(tmp_path, "git", "push", "-q", "origin", "main")  # no -u
    meta = _repo_meta(
        tmp_path,
        remote_upstream="fork/main",
        remote_pushed="yes",
        remote_ahead=0,
        remote_behind=0,
    )
    res = verify_continuity(meta, cwd=str(tmp_path), gh_runner=lambda *a: None)
    r = res["remote"]
    assert r["stored_upstream"] == "fork/main"
    assert r["current_upstream"] == "origin/main"
    assert r["current_pushed"] == "yes"
    assert r["changed"] is True  # stored fork/main != resolved origin/main
