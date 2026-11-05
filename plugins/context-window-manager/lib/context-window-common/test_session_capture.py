import subprocess

import session_capture
import verify_refs
from context_schema import SessionMetadata, render_metadata_block

PLACEHOLDER_TASK = "## TDD Plan\n\nPopulated by `cobtask-workflow:cobtask-tdd-plan` at start-work time.\n\n## Log\n"
POPULATED_TASK = "## TDD Plan\n\n### 2026-08-08\n\n1. Do the thing.\n\n## Log\n"

# publish.sh rejects a literal `<3 uppercase letters>-<4 digits>` anywhere in a
# first-party plugin tree, so sample task and ADR ids are assembled from parts.
# The assembled values still exercise the real id shapes.
_ID_PREFIX = "ABC"
_ADR = "ADR"
TASK_ID = f"{_ID_PREFIX}-0033"
BRANCH_WITH_ID = f"feat/{TASK_ID.lower()}-expand-handoff-metadata"
SAMPLE_ADR = f"/d/{_ADR}-0014-foo.md"
HEAD_OID = "a" * 40


def _fake_gh(stdout, returncode=0):
    return lambda cwd: subprocess.CompletedProcess((), returncode, stdout, "")


def _vault_with_task(tmp_path, body):
    """A minimal cobtask vault holding one task file."""
    items = tmp_path / "vault" / "items"
    items.mkdir(parents=True, exist_ok=True)
    task = items / f"{TASK_ID}-expand-handoff-metadata.md"
    task.write_text(body)
    return tmp_path / "vault", task


def _run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "-q", "-b", "main")
    _run(path, "git", "config", "user.email", "t@t")
    _run(path, "git", "config", "user.name", "t")
    (path / "a.txt").write_text("1")
    _run(path, "git", "add", "-A")
    _run(path, "git", "commit", "-qm", "c1: first")


def _pushed_repo(tmp_path):
    """A work tree whose `main` tracks a bare origin, so upstream probes have
    something real to resolve."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True
    )
    _init_repo(work)
    _run(work, "git", "remote", "add", "origin", str(origin))
    _run(work, "git", "push", "-q", "-u", "origin", "main")
    return work


def test_collect_git_reports_dirty_tree_with_counts(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "staged.txt").write_text("s")
    _run(tmp_path, "git", "add", "staged.txt")
    (tmp_path / "untracked.txt").write_text("u")
    meta = session_capture.collect_git(str(tmp_path))
    assert meta.working_tree == "dirty"
    assert meta.staged == 1
    assert meta.untracked == 1
    # No remote configured, so the branch cannot be considered pushed.
    assert meta.remote_pushed == "no"
    assert meta.remote_upstream == ""
    # Subject text travels with the hash so a resume can read what was done
    # without re-running git.
    assert any("c1: first" in c for c in meta.commits)


def test_collect_git_outside_a_repo_leaves_git_fields_unset(tmp_path):
    # Non-git is a supported case, not an error: the fields stay at their
    # defaults so the rendered block says 'n/a' instead of inventing a value.
    meta = session_capture.collect_git(str(tmp_path))
    assert meta.git_sha == ""
    assert meta.git_branch == ""
    assert meta.working_tree == ""
    assert meta.commits == []
    assert meta.cwd == str(tmp_path.resolve())


def test_collect_git_reports_clean_tree(tmp_path):
    _init_repo(tmp_path)
    meta = session_capture.collect_git(str(tmp_path))
    assert meta.working_tree == "clean"
    assert (meta.staged, meta.unstaged, meta.untracked) == (0, 0, 0)


def test_collect_git_counts_an_unstaged_modification(tmp_path):
    # The gap this whole field closes: HEAD alone would report this tree clean.
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("changed")
    meta = session_capture.collect_git(str(tmp_path))
    assert meta.working_tree == "dirty"
    assert meta.unstaged == 1
    assert meta.staged == 0


def test_collect_git_reports_stash_depth(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("changed")
    _run(tmp_path, "git", "stash", "push", "-q", "-m", "t")
    assert session_capture.collect_git(str(tmp_path)).stash_count == 1


def test_collect_git_reports_pushed_branch(tmp_path):
    work = _pushed_repo(tmp_path)
    meta = session_capture.collect_git(str(work))
    assert meta.remote_upstream == "origin/main"
    assert meta.remote_ahead == 0
    assert meta.remote_behind == 0
    assert meta.remote_pushed == "yes"


def test_collect_git_reports_unpushed_commits_as_not_pushed(tmp_path):
    work = _pushed_repo(tmp_path)
    (work / "b.txt").write_text("2")
    _run(work, "git", "add", "-A")
    _run(work, "git", "commit", "-qm", "c2: local only")
    meta = session_capture.collect_git(str(work))
    assert meta.remote_ahead == 1
    assert meta.remote_pushed == "no"


def test_collect_git_reports_pushed_without_tracking_as_pushed(tmp_path):
    # `git push origin main` with no `-u` leaves no `@{upstream}` config,
    # but the remote genuinely has every commit: this must not be reported
    # as unpushed just because tracking was never configured.
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True
    )
    _init_repo(work)
    _run(work, "git", "remote", "add", "origin", str(origin))
    _run(work, "git", "push", "-q", "origin", "main")
    meta = session_capture.collect_git(str(work))
    assert meta.remote_upstream == "origin/main"
    assert meta.remote_ahead == 0
    assert meta.remote_behind == 0
    assert meta.remote_pushed == "yes"


def test_collect_git_reports_pushed_after_tracking_unset(tmp_path):
    # Unsetting the upstream tracking config must not make a genuinely
    # pushed branch look unpushed: the origin/<branch> ref still exists.
    work = _pushed_repo(tmp_path)
    _run(work, "git", "branch", "--unset-upstream")
    meta = session_capture.collect_git(str(work))
    assert meta.remote_upstream == "origin/main"
    assert meta.remote_pushed == "yes"


def test_collect_git_reports_pushed_to_a_non_origin_remote_without_tracking(tmp_path):
    # `git push upstream main` with no `-u`, to a remote that isn't named
    # "origin", leaves no `@{upstream}` config and no origin/<branch> ref to
    # fall back to -- but the remote genuinely has every commit. Checking
    # only origin/<branch> would misreport this as unpushed.
    remote, work = tmp_path / "upstream.git", tmp_path / "work"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True
    )
    _init_repo(work)
    _run(work, "git", "remote", "add", "upstream", str(remote))
    _run(work, "git", "push", "-q", "upstream", "main")
    meta = session_capture.collect_git(str(work))
    assert meta.remote_upstream == "upstream/main"
    assert meta.remote_ahead == 0
    assert meta.remote_behind == 0
    assert meta.remote_pushed == "yes"


def test_collect_git_reports_not_pushed_when_remote_lacks_the_branch(tmp_path):
    # A remote is configured but this branch was never pushed under any
    # name: the origin/<branch> fallback must not invent a match.
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(
        ["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True
    )
    _init_repo(work)
    _run(work, "git", "remote", "add", "origin", str(origin))
    meta = session_capture.collect_git(str(work))
    assert meta.remote_upstream == ""
    assert meta.remote_pushed == "no"


def test_commits_are_scoped_to_the_branch_divergence(tmp_path):
    # Only the work this branch added should be listed; the base branch's own
    # history is not this session's work.
    _init_repo(tmp_path)
    _run(tmp_path, "git", "checkout", "-qb", "feat/x")
    (tmp_path / "b.txt").write_text("2")
    _run(tmp_path, "git", "add", "-A")
    _run(tmp_path, "git", "commit", "-qm", "c2: branch work")
    meta = session_capture.collect_git(str(tmp_path))
    assert [c.split(" ", 1)[1] for c in meta.commits] == ["c2: branch work"]


def test_commits_fall_back_to_recent_history_on_the_base_branch(tmp_path):
    # Sitting on the base branch there is no divergence, but a session spent
    # working there still needs its commits listed.
    _init_repo(tmp_path)
    meta = session_capture.collect_git(str(tmp_path))
    assert [c.split(" ", 1)[1] for c in meta.commits] == ["c1: first"]


def test_commit_list_is_capped(tmp_path):
    # The commit list rides inside a context file, so an old branch must not
    # dump its whole history into it.
    _init_repo(tmp_path)
    _run(tmp_path, "git", "checkout", "-qb", "feat/many")
    for i in range(session_capture._MAX_COMMITS + 3):
        (tmp_path / f"f{i}").write_text("x")
        _run(tmp_path, "git", "add", "-A")
        _run(tmp_path, "git", "commit", "-qm", f"c{i}")
    meta = session_capture.collect_git(str(tmp_path))
    assert len(meta.commits) == session_capture._MAX_COMMITS


def test_commit_subjects_with_backticks_are_neutralized(tmp_path):
    # A commit subject is attacker-influenceable (any contributor can write
    # one). Left verbatim, a backtick-wrapped home path in a subject would be
    # picked up by resume's reference scanner (verify_refs.extract_refs) and
    # probed against the filesystem. Backticks must not survive capture.
    _init_repo(tmp_path)
    _run(
        tmp_path,
        "git",
        "commit",
        "--allow-empty",
        "-qm",
        "test: `~/.ssh/config:1`",
    )
    meta = session_capture.collect_git(str(tmp_path))
    assert not any("`" in c for c in meta.commits)


def test_backticked_commit_subject_yields_no_extractable_ref(tmp_path):
    # End-to-end proof of the fix: capture -> render -> resume's ref scanner
    # must find nothing to verify from a malicious commit subject.
    _init_repo(tmp_path)
    _run(
        tmp_path,
        "git",
        "commit",
        "--allow-empty",
        "-qm",
        "test: `~/.ssh/config:1`",
    )
    meta = session_capture.collect_git(str(tmp_path))
    block = render_metadata_block(meta)
    refs = verify_refs.extract_refs(block)
    assert not any("ssh" in r["path"] for r in refs)


def test_collect_context_resolves_cobtask_pr_and_external_tdd_plan(tmp_path):
    vault, task = _vault_with_task(tmp_path, PLACEHOLDER_TASK)
    meta = SessionMetadata(cwd=str(tmp_path), git_branch=BRANCH_WITH_ID)
    out = session_capture.collect_context(
        meta,
        vault_root=str(vault),
        tdd_plan_path="/plans/p.md",
        adrs=[SAMPLE_ADR],
        gh_runner=_fake_gh('{"number": 12, "url": "https://gh/pr/12"}'),
    )
    # The id rides in the branch name in lowercase; the vault stores it upper.
    assert out.cobtask_id == TASK_ID
    assert out.cobtask_path == str(task)
    assert out.pr_number == "12"
    assert out.pr_url == "https://gh/pr/12"
    assert out.adrs == [SAMPLE_ADR]
    # The task carries only the placeholder, so an external plan is worth a link.
    assert out.tdd_plan_path == "/plans/p.md"


def test_tdd_plan_path_is_dropped_when_the_cobtask_already_holds_the_plan(tmp_path):
    # AC: capture tdd_plan_path only when the cobtask file lacks a real plan.
    vault, _ = _vault_with_task(tmp_path, POPULATED_TASK)
    meta = SessionMetadata(cwd=str(tmp_path), git_branch=BRANCH_WITH_ID)
    out = session_capture.collect_context(
        meta, vault_root=str(vault), tdd_plan_path="/plans/p.md"
    )
    assert out.tdd_plan_path == ""


def test_explicit_cobtask_id_beats_the_branch_name(tmp_path):
    vault, task = _vault_with_task(tmp_path, PLACEHOLDER_TASK)
    meta = SessionMetadata(cwd=str(tmp_path), git_branch="feat/no-id-here")
    out = session_capture.collect_context(
        meta, cobtask_id=TASK_ID, vault_root=str(vault)
    )
    assert out.cobtask_id == TASK_ID
    assert out.cobtask_path == str(task)


def test_branch_without_a_task_id_leaves_cobtask_fields_empty(tmp_path):
    vault, _ = _vault_with_task(tmp_path, PLACEHOLDER_TASK)
    meta = SessionMetadata(cwd=str(tmp_path), git_branch="chore/tidy-up")
    out = session_capture.collect_context(meta, vault_root=str(vault))
    assert out.cobtask_id == ""
    assert out.cobtask_path == ""


def test_no_vault_root_still_records_the_id_from_the_branch(tmp_path):
    meta = SessionMetadata(cwd=str(tmp_path), git_branch=BRANCH_WITH_ID)
    out = session_capture.collect_context(meta)
    assert out.cobtask_id == TASK_ID
    assert out.cobtask_path == ""


def test_missing_gh_degrades_to_no_pr(tmp_path):
    def boom(cwd):
        raise FileNotFoundError("gh is not installed")

    meta = SessionMetadata(cwd=str(tmp_path))
    out = session_capture.collect_context(meta, gh_runner=boom)
    assert out.pr_number == ""
    assert out.pr_url == ""


def test_no_open_pr_degrades_to_no_pr(tmp_path):
    # `gh pr view` exits non-zero when the branch has no PR. That is the normal
    # pre-PR case, not an error to surface.
    meta = SessionMetadata(cwd=str(tmp_path))
    out = session_capture.collect_context(meta, gh_runner=_fake_gh("", returncode=1))
    assert out.pr_number == ""
    assert out.pr_url == ""


def test_unparseable_gh_output_degrades_to_no_pr(tmp_path):
    meta = SessionMetadata(cwd=str(tmp_path))
    out = session_capture.collect_context(meta, gh_runner=_fake_gh("not json"))
    assert out.pr_number == ""
    assert out.pr_url == ""


def test_working_tree_probe_failure_reports_unknown_not_zero(tmp_path, monkeypatch):
    # A `git status` that fails (timeout, broken submodule, etc.) must not be
    # confused with "we checked and there were no changes": the counts stay
    # unknown (None), not silently zeroed.
    _init_repo(tmp_path)
    real_git = session_capture._git

    def flaky(cwd, *args, **kwargs):
        if args[:1] == ("status",):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setattr(session_capture, "_git", flaky)
    meta = session_capture.collect_git(str(tmp_path))
    assert meta.working_tree == ""
    assert meta.staged is None
    assert meta.unstaged is None
    assert meta.untracked is None


def test_stash_probe_failure_reports_unknown_not_zero(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    real_git = session_capture._git

    def flaky(cwd, *args, **kwargs):
        if args[:1] == ("stash",):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setattr(session_capture, "_git", flaky)
    meta = session_capture.collect_git(str(tmp_path))
    assert meta.stash_count is None


def test_remote_probe_failure_reports_unknown_not_no(tmp_path, monkeypatch):
    # A `git rev-list` that fails after the upstream resolves must not be
    # confused with "we checked and it is unpushed": pushed state stays
    # unknown (renders 'n/a'), not silently "no".
    work = _pushed_repo(tmp_path)
    real_git = session_capture._git

    def flaky(cwd, *args, **kwargs):
        if args[:1] == ("rev-list",):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setattr(session_capture, "_git", flaky)
    meta = session_capture.collect_git(str(work))
    assert meta.remote_upstream == "origin/main"
    assert meta.remote_ahead is None
    assert meta.remote_behind is None
    assert meta.remote_pushed == ""


def test_git_failure_is_contained(tmp_path, monkeypatch):
    # A missing git binary must degrade to "nothing known", not raise into the
    # handoff.
    def boom(*a, **k):
        raise OSError("git is gone")

    monkeypatch.setattr(session_capture.subprocess, "run", boom)
    meta = session_capture.collect_git(str(tmp_path))
    assert meta.git_sha == ""
    assert meta.working_tree == ""
    assert meta.commits == []


def test_git_call_is_skipped_once_the_deadline_has_passed(tmp_path):
    # `_git` must not even attempt the subprocess call once the shared
    # deadline has already passed: this is the primitive every probe in
    # collect_git/collect_context relies on to stop issuing dependent probes.
    _init_repo(tmp_path)
    deadline = session_capture._Deadline(budget_seconds=-1)
    r = session_capture._git(str(tmp_path), "status", deadline=deadline)
    assert r.returncode == 1


def test_collect_git_stops_issuing_probes_once_the_deadline_passes(
    tmp_path, monkeypatch
):
    # Without a shared deadline, a degraded git installation makes each of
    # collect_git's dozen-plus independent probes wait out its own
    # _GIT_TIMEOUT in sequence, turning a ~10s problem into minutes. A fake
    # clock simulates the first probe alone consuming the entire budget;
    # every probe after it must be skipped outright rather than attempted.
    _init_repo(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(session_capture.time, "monotonic", lambda: clock[0])
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        clock[0] += session_capture._GIT_TIMEOUT
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout="true\n", stderr=""
        )

    monkeypatch.setattr(session_capture.subprocess, "run", fake_run)
    deadline = session_capture._Deadline()
    meta = session_capture.collect_git(str(tmp_path), deadline=deadline)
    # Only the very first probe (the one that crossed the deadline) is ever
    # attempted; every dependent probe after it is skipped, not issued.
    assert len(calls) == 1
    assert meta.commits == []
    assert meta.working_tree == ""


def test_collect_context_skips_the_default_gh_probe_once_the_deadline_has_passed(
    tmp_path, monkeypatch
):
    # Once the capture deadline has passed, the default `gh` probe must not
    # be issued at all. Uses a pushed repo (a configured remote) so the
    # existing "no remote" short-circuit doesn't already explain the skip,
    # isolating the deadline behavior specifically.
    work = _pushed_repo(tmp_path)
    meta = SessionMetadata(cwd=str(work), git_branch="main")
    calls = []
    monkeypatch.setattr(
        session_capture,
        "_default_gh_runner",
        lambda cwd: (
            calls.append(cwd)
            or subprocess.CompletedProcess(
                (), 0, '{"number": "1", "url": "https://x"}', ""
            )
        ),
    )
    deadline = session_capture._Deadline(budget_seconds=-1)
    out = session_capture.collect_context(meta, deadline=deadline)
    assert calls == []
    assert out.pr_number == ""
    assert out.pr_url == ""


def test_pull_request_fallback_includes_all_states_and_matches_oid(tmp_path):
    calls = []

    def runner(cwd, *args):
        calls.append(args)
        if not args:
            return subprocess.CompletedProcess((), 1, "", "no PR")
        return subprocess.CompletedProcess(
            (),
            0,
            f"["
            f'{{"number": 12, "url": "https://gh/pr/12", '
            f'"headRefOid": "{HEAD_OID}"}}'
            f"]",
            "",
        )

    pr_number, pr_url = session_capture._pull_request(
        str(tmp_path), runner, head="feat/my-branch", head_oid=HEAD_OID
    )
    assert pr_number == "12"
    assert pr_url == "https://gh/pr/12"
    assert len(calls) == 2
    assert calls[0] == ()
    assert calls[1] == (
        "list",
        "--head",
        "feat/my-branch",
        "--state",
        "all",
        "--json",
        "number,url,headRefOid",
    )


def test_collect_context_falls_back_to_remote_head_in_worktree(tmp_path, monkeypatch):
    calls = []

    def runner(cwd, *args):
        calls.append(args)
        if not args:
            return subprocess.CompletedProcess((), 1, "", "no PR")
        return subprocess.CompletedProcess(
            (),
            0,
            f"["
            f'{{"number": 12, "url": "https://gh/pr/12", '
            f'"headRefOid": "{HEAD_OID}"}}'
            f"]",
            "",
        )

    meta = SessionMetadata(
        cwd=str(tmp_path),
        git_branch="worktree-feat+abc-0033-foo",
        remote_upstream="origin/feat/abc-0033-foo",
    )
    monkeypatch.setattr(session_capture, "_out", lambda *args, **kwargs: HEAD_OID)
    out = session_capture.collect_context(meta, gh_runner=runner)
    assert out.pr_number == "12"
    assert out.pr_url == "https://gh/pr/12"
    assert len(calls) == 2
    assert calls[0] == ()
    assert calls[1] == (
        "list",
        "--head",
        "feat/abc-0033-foo",
        "--state",
        "all",
        "--json",
        "number,url,headRefOid",
    )


def test_pull_request_fallback_ignores_same_named_fork_with_wrong_oid(tmp_path):
    wrong_oid = "b" * 40

    def runner(cwd, *args):
        if not args:
            return subprocess.CompletedProcess((), 1, "", "no PR")
        return subprocess.CompletedProcess(
            (),
            0,
            f"["
            f'{{"number": 10, "url": "https://gh/pr/10", '
            f'"headRefOid": "{wrong_oid}"}},'
            f'{{"number": 12, "url": "https://gh/pr/12", '
            f'"headRefOid": "{HEAD_OID}"}}'
            f"]",
            "",
        )

    pr_number, pr_url = session_capture._pull_request(
        str(tmp_path), runner, head="feat/x", head_oid=HEAD_OID
    )
    assert pr_number == "12"
    assert pr_url == "https://gh/pr/12"


def test_pull_request_fallback_rejects_ambiguous_matching_oids(tmp_path):
    def runner(cwd, *args):
        if not args:
            return subprocess.CompletedProcess((), 1, "", "no PR")
        return subprocess.CompletedProcess(
            (),
            0,
            f"["
            f'{{"number": 10, "url": "https://gh/pr/10", '
            f'"headRefOid": "{HEAD_OID}"}},'
            f'{{"number": 12, "url": "https://gh/pr/12", '
            f'"headRefOid": "{HEAD_OID}"}}'
            f"]",
            "",
        )

    pr_number, pr_url = session_capture._pull_request(
        str(tmp_path), runner, head="feat/x", head_oid=HEAD_OID
    )
    assert pr_number == ""
    assert pr_url == ""


def test_pull_request_both_probes_fail_returns_empty(tmp_path):
    def runner(cwd, *args):
        return subprocess.CompletedProcess((), 1, "", "no PR")

    pr_number, pr_url = session_capture._pull_request(
        str(tmp_path), runner, head="feat/x"
    )
    assert pr_number == ""
    assert pr_url == ""


def test_pull_request_preserves_one_argument_runner_contract(tmp_path):
    def runner(cwd):
        return subprocess.CompletedProcess((), 1, "", "no PR")

    pr_number, pr_url = session_capture._pull_request(
        str(tmp_path), runner, head="feat/x"
    )
    assert pr_number == ""
    assert pr_url == ""


def test_pull_request_fallback_malformed_list_returns_empty(tmp_path):
    def runner(cwd, *args):
        if not args:
            return subprocess.CompletedProcess((), 1, "", "no PR")
        return subprocess.CompletedProcess((), 0, "not json", "")

    pr_number, pr_url = session_capture._pull_request(
        str(tmp_path), runner, head="feat/x"
    )
    assert pr_number == ""
    assert pr_url == ""


def test_pull_request_fallback_empty_list_returns_empty(tmp_path):
    def runner(cwd, *args):
        if not args:
            return subprocess.CompletedProcess((), 1, "", "no PR")
        return subprocess.CompletedProcess((), 0, "[]", "")

    pr_number, pr_url = session_capture._pull_request(
        str(tmp_path), runner, head="feat/x"
    )
    assert pr_number == ""
    assert pr_url == ""


def test_pull_request_fallback_skipped_when_deadline_expired(tmp_path, monkeypatch):
    calls = []

    def runner(cwd, *args):
        calls.append(args)
        return subprocess.CompletedProcess((), 1, "", "no PR")

    deadline = session_capture._Deadline(budget_seconds=-1)
    pr_number, pr_url = session_capture._pull_request(
        str(tmp_path), runner, head="feat/x", deadline=deadline
    )
    assert pr_number == ""
    assert pr_url == ""
    assert len(calls) == 1
    assert calls[0] == ()
