import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import context_schema
import pytest
from conftest import write_context, write_context_raw

# Load this skill's validator by explicit path under a unique module name so it
# never collides with the handoff skill's identically-named validator.py when
# both suites are collected in one pytest process.
_VAL_PATH = pathlib.Path(__file__).parent / "validator.py"
sys.path.insert(0, str(_VAL_PATH.parent))
_spec = importlib.util.spec_from_file_location("resume_validator", _VAL_PATH)
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)

VAL = str(_VAL_PATH)


def _run(cwd, save_root):
    # Redirect the save root via env so the subprocess validator reads fixtures
    # from a test-controlled dir instead of the real ~/.claude/.sct tree.
    env = {**os.environ, "CWM_SAVE_ROOT": str(save_root)}
    r = subprocess.run(
        [sys.executable, VAL],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return json.loads(r.stdout)


def _run_cf(cwd, save_root, context_file, home=None):
    env = {**os.environ, "CWM_SAVE_ROOT": str(save_root)}
    if home is not None:
        env["HOME"] = str(home)
    r = subprocess.run(
        [sys.executable, VAL, "--context-file", str(context_file)],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return json.loads(r.stdout)


def _init_repo(p):
    for a in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(a, cwd=p, check=True)
    (p / "src").mkdir()
    (p / "src" / "keep.py").write_text("x=1\n")
    subprocess.run(["git", "add", "-A"], cwd=p, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=p, check=True)


def _save_dir(save_root, repo):
    # Mirror context_schema.save_dir: SAVE_ROOT/<repo-slug>. Use the real slug
    # helper so the fixture dir matches what the validator computes.
    d = save_root / context_schema.repo_slug(repo)
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_no_context_dir_is_ok(tmp_path):
    _init_repo(tmp_path)
    out = _run(str(tmp_path), tmp_path / "sct")  # empty save root
    assert out["ok"] is True
    assert out["checks"]["context_file"] is None


def test_drift_and_missing_ref_reported(tmp_path):
    _init_repo(tmp_path)
    stored = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    save_root = tmp_path / "sct"
    d = _save_dir(save_root, tmp_path)
    (d / "CONTEXT-2026-07-19-14-30-00.md").write_text(
        "### Session Metadata\n\n"
        f"- git_sha: {stored}\n- git_sha_short: {stored[:7]}\n- git_branch: main\n\n"
        "### Key Context\n- `src/keep.py:1` and `src/gone.py`\n"
    )
    (tmp_path / "n.py").write_text("y=2\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "c2"], cwd=tmp_path, check=True)
    out = _run(str(tmp_path), save_root)
    assert out["checks"]["context_file"] == "CONTEXT-2026-07-19-14-30-00.md"
    assert out["report"]["drift"]["ahead"] == 1
    refs = {r["path"]: r["exists"] for r in out["report"]["refs"]}
    assert refs["src/keep.py"] is True and refs["src/gone.py"] is False


def test_newest_context_file_wins(tmp_path):
    # The skill's headline behavior: resume from the MOST RECENT handoff. Names
    # are CONTEXT-<sortable-timestamp>.md, so lexicographically-latest == newest.
    # Written oldest-last so filesystem creation order can't be what makes the
    # assertion pass.
    _init_repo(tmp_path)
    stored = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    save_root = tmp_path / "sct"
    d = _save_dir(save_root, tmp_path)
    meta = (
        "### Session Metadata\n\n"
        f"- git_sha: {stored}\n- git_sha_short: {stored[:7]}\n- git_branch: main\n"
    )
    # Two real dated handoff names: the later timestamp sorts last, so it is the
    # newest and must win regardless of write order.
    (d / "CONTEXT-2026-07-19-14-30-00.md").write_text(meta)
    (d / "CONTEXT-2026-07-19-09-00-00.md").write_text(meta)
    out = _run(str(tmp_path), save_root)
    assert out["checks"]["context_file"] == "CONTEXT-2026-07-19-14-30-00.md"


def test_two_handoffs_within_window_both_reported(tmp_save_dir, run_validator):
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="Session A goal")
    write_context(
        tmp_save_dir, "2026-08-08-09-20-00", summary="Session B goal"
    )  # +20 min
    out = run_validator()
    names = [c["name"] for c in out["checks"]["context_candidates"]]
    assert names == [
        "CONTEXT-2026-08-08-09-20-00.md",
        "CONTEXT-2026-08-08-09-00-00.md",
    ]
    assert out["checks"]["context_candidates"][0]["summary"] == "Session B goal"


def test_single_file_one_candidate(tmp_save_dir, run_validator):
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="Only session")
    out = run_validator()
    assert len(out["checks"]["context_candidates"]) == 1


def test_two_files_outside_window_only_newest(tmp_save_dir, run_validator):
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="Old")
    write_context(tmp_save_dir, "2026-08-08-09-31-00", summary="New")  # +31 min
    out = run_validator()
    names = [c["name"] for c in out["checks"]["context_candidates"]]
    assert names == ["CONTEXT-2026-08-08-09-31-00.md"]


def test_boundary_exactly_thirty_minutes_included(tmp_save_dir, run_validator):
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="A")
    write_context(tmp_save_dir, "2026-08-08-09-30-00", summary="B")  # +30 min exact
    out = run_validator()
    assert len(out["checks"]["context_candidates"]) == 2


def test_summary_line_skips_heading(tmp_save_dir, run_validator):
    body = "## Session Summary\n\nRefactor the resolver\n\n## Next Steps\n- x\n"
    write_context_raw(tmp_save_dir, "2026-08-08-09-00-00", body)
    out = run_validator()
    assert out["checks"]["context_candidates"][0]["summary"] == "Refactor the resolver"


def test_context_file_flag_selects_report_target(tmp_save_dir, run_validator):
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="A")
    write_context(tmp_save_dir, "2026-08-08-09-10-00", summary="B")
    out = run_validator(args=["--context-file", "CONTEXT-2026-08-08-09-00-00.md"])
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-09-00-00.md"


def test_old_qualified_resume_line_still_accepted(tmp_save_dir, run_validator):
    # Pre-1.0 handoffs carry the old command line; resume must not reject them.
    body = (
        "# Resume Command\n\n/context-window-manager:context-window-resume\n\n"
        "## Session Summary\n\nold-format handoff\n"
    )
    write_context_raw(tmp_save_dir, "2026-08-08-09-00-00", body)
    out = run_validator(args=["--context-file", "CONTEXT-2026-08-08-09-00-00.md"])
    assert out["ok"] is True
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-09-00-00.md"


def test_new_resume_line_accepted(tmp_save_dir, run_validator):
    body = (
        "# Resume Command\n\n/context-window-manager:resume\n\n"
        "## Session Summary\n\nnew-format handoff\n"
    )
    write_context_raw(tmp_save_dir, "2026-08-08-09-00-00", body)
    out = run_validator(args=["--context-file", "CONTEXT-2026-08-08-09-00-00.md"])
    assert out["ok"] is True


def test_context_file_basename_still_resolves(tmp_path, tmp_save_dir):
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="A")

    out = _run_cf(tmp_path, tmp_save_dir.parent, "CONTEXT-2026-08-08-09-00-00.md")

    assert out["ok"] is True
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-09-00-00.md"


def test_context_file_non_timestamp_basename_resolves(tmp_path, tmp_save_dir):
    write_context(tmp_save_dir, "latest", summary="A")

    out = _run_cf(tmp_path, tmp_save_dir.parent, "CONTEXT-latest.md")

    assert out["ok"] is True
    assert out["checks"]["context_file"] == "CONTEXT-latest.md"


def test_context_file_existing_non_context_basename_reports_contract(
    tmp_path, tmp_save_dir
):
    context_file = "notes.md"
    (tmp_save_dir / context_file).write_text("## Session Summary\n\nNot a handoff\n")

    out = _run_cf(tmp_path, tmp_save_dir.parent, context_file)

    assert out["ok"] is False
    assert out["checks"]["context_file"] is None
    assert out["errors"] == [
        (
            "context file must be an existing CONTEXT-*.md basename or path inside "
            f"the current checkout or a registered sibling worktree: {context_file}"
        )
    ]


def test_context_file_empty_value_reports_contract(tmp_path, tmp_save_dir):
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="A")

    out = _run_cf(tmp_path, tmp_save_dir.parent, "")

    assert out["ok"] is False
    assert out["checks"]["context_file"] is None
    assert out["errors"] == [
        (
            "context file must be an existing CONTEXT-*.md basename or path inside "
            "the current checkout or a registered sibling worktree: "
        )
    ]


def test_context_file_with_appended_prose_gets_distinct_diagnostic(
    tmp_path, tmp_save_dir
):
    # Regression: the resume caller glued Step-3 guidance onto an otherwise-valid
    # CONTEXT-*.md path, so --context-file received
    # "<path> and make sure you cd to the correct working directory." The
    # validator must flag the appended text with a distinct diagnostic instead
    # of echoing the whole malformed string back through the generic contract
    # error.
    write_context(tmp_save_dir, "2026-08-26-07-17-47", summary="A", suffix="a1911e")
    ctx = tmp_save_dir / "CONTEXT-2026-08-26-07-17-47-a1911e.md"
    bad = f"{ctx} and make sure you cd to the correct working directory."

    out = _run_cf(tmp_path, tmp_save_dir.parent, bad)

    assert out["ok"] is False
    assert out["checks"]["context_file"] is None
    assert any("extra text was appended" in e for e in out["errors"])


# Characterization: pathlib preserves an absolute right-hand path when joining.
def test_context_file_full_path_resolves(tmp_path, tmp_save_dir):
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="A")
    context_file = tmp_save_dir / "CONTEXT-2026-08-08-09-00-00.md"

    out = _run_cf(tmp_path, tmp_save_dir.parent, context_file)

    assert out["ok"] is True
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-09-00-00.md"


def test_context_file_tilde_path_resolves(tmp_path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    save_root = home / ".config" / "sct" / "context-window-manager"
    save_dir = _save_dir(save_root, repo)
    write_context(save_dir, "2026-08-08-09-00-00", summary="A")
    context_file = (
        "~/.config/sct/context-window-manager/"
        f"{context_schema.repo_slug(repo)}/CONTEXT-2026-08-08-09-00-00.md"
    )

    out = _run_cf(repo, save_root, context_file, home=home)

    assert out["ok"] is True, out["errors"]
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-09-00-00.md"


def test_context_file_relative_path_reports_contract(tmp_path, tmp_save_dir):
    context_name = "CONTEXT-2026-08-08-09-00-00.md"
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="A")
    context_file = tmp_save_dir.relative_to(tmp_path) / context_name

    out = _run_cf(tmp_path, tmp_save_dir.parent, context_file)

    assert out["ok"] is False
    assert out["checks"]["context_file"] is None
    assert out["errors"] == [
        (
            "context file must be an existing CONTEXT-*.md basename or path inside "
            f"the current checkout or a registered sibling worktree: {context_file}"
        )
    ]


def test_context_file_missing_reports_contract(tmp_path, tmp_save_dir):
    context_file = "CONTEXT-does-not-exist.md"

    out = _run_cf(tmp_path, tmp_save_dir.parent, context_file)

    assert out["ok"] is False
    assert out["errors"] == [
        (
            "context file must be an existing CONTEXT-*.md basename or path inside "
            "the current checkout or a registered sibling worktree: CONTEXT-does-not-exist.md"
        )
    ]


def test_context_file_unknown_user_reports_contract(tmp_path, tmp_save_dir):
    context_file = "~definitely-no-such-user-7f3b91/CONTEXT-2026-08-08-09-00-00.md"

    out = _run_cf(tmp_path, tmp_save_dir.parent, context_file)

    assert out["ok"] is False
    assert out["errors"] == [
        (
            "context file must be an existing CONTEXT-*.md basename or path inside "
            "the current checkout or a registered sibling worktree: "
            "~definitely-no-such-user-7f3b91/CONTEXT-2026-08-08-09-00-00.md"
        )
    ]


def test_context_file_outside_save_dir_rejected(tmp_path, tmp_save_dir):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    write_context(outside_dir, "2026-08-08-09-00-00", summary="A")
    context_file = outside_dir / "CONTEXT-2026-08-08-09-00-00.md"

    out = _run_cf(tmp_path, tmp_save_dir.parent, context_file)

    assert out["ok"] is False, out
    assert out["checks"]["context_file"] is None
    assert out["errors"] == [
        (
            "context file must be an existing CONTEXT-*.md basename or path inside "
            f"the current checkout or a registered sibling worktree: {context_file}"
        )
    ]


def test_context_file_basename_symlink_outside_save_dir_rejected(
    tmp_path, tmp_save_dir
):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    context_file = "CONTEXT-2026-08-08-09-00-00.md"
    write_context(outside_dir, "2026-08-08-09-00-00", summary="Outside")
    (tmp_save_dir / context_file).symlink_to(outside_dir / context_file)

    out = _run_cf(tmp_path, tmp_save_dir.parent, context_file)

    assert out["ok"] is False
    assert out["checks"]["context_file"] is None
    assert out["errors"] == [
        (
            "context file must be an existing CONTEXT-*.md basename or path inside "
            f"the current checkout or a registered sibling worktree: {context_file}"
        )
    ]


def test_context_file_basename_symlink_to_sibling_worktree_rejected(tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/x", str(linked)],
        cwd=primary,
        check=True,
    )
    save_root = tmp_path / "sct"
    primary_save_dir = _save_dir(save_root, primary)
    sibling_save_dir = _save_dir(save_root, linked)
    context_file = "CONTEXT-2026-08-08-09-00-00.md"
    write_context(sibling_save_dir, "2026-08-08-09-00-00", summary="Sibling")
    (primary_save_dir / context_file).symlink_to(sibling_save_dir / context_file)

    out = _run_cf(primary, save_root, context_file)

    assert out["ok"] is False
    assert out["checks"]["context_file"] is None
    assert out["errors"] == [
        (
            "context file must be an existing CONTEXT-*.md basename or path inside "
            f"the current checkout or a registered sibling worktree: {context_file}"
        )
    ]


def test_default_selection_ignores_symlink_outside_save_dir(tmp_path, tmp_save_dir):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    context_file = "CONTEXT-2026-08-08-09-00-00.md"
    write_context(outside_dir, "2026-08-08-09-00-00", summary="Outside")
    (tmp_save_dir / context_file).symlink_to(outside_dir / context_file)

    out = _run(tmp_path, tmp_save_dir.parent)

    assert out["ok"] is True
    assert out["checks"]["context_file"] is None
    assert out["checks"]["context_candidates"] == []
    assert out["report"] is None


def test_default_selection_ignores_broken_context_symlink(tmp_path, tmp_save_dir):
    context_file = "CONTEXT-2026-08-08-09-00-00.md"
    (tmp_save_dir / context_file).symlink_to(tmp_path / "missing-context.md")

    out = _run(tmp_path, tmp_save_dir.parent)

    assert out["ok"] is True
    assert out["checks"]["context_file"] is None
    assert out["checks"]["context_candidates"] == []
    assert out["report"] is None


def test_default_selection_deduplicates_internal_symlink(tmp_path, tmp_save_dir):
    context_file = "CONTEXT-2026-08-08-09-00-00.md"
    alias = "CONTEXT-2026-08-08-09-01-00.md"
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="A")
    (tmp_save_dir / alias).symlink_to(tmp_save_dir / context_file)

    out = _run(tmp_path, tmp_save_dir.parent)

    assert out["ok"] is True
    assert out["checks"]["context_candidates"] == [
        {
            "name": context_file,
            "timestamp": "2026-08-08-09-00-00",
            "summary": "A",
        }
    ]


def test_default_selection_accepts_non_timestamp_context_name(tmp_path, tmp_save_dir):
    write_context(tmp_save_dir, "latest", summary="A")

    out = _run(tmp_path, tmp_save_dir.parent)

    assert out["ok"] is True
    assert out["checks"]["context_file"] == "CONTEXT-latest.md"
    assert out["checks"]["context_candidates"] == [
        {"name": "CONTEXT-latest.md", "timestamp": "latest", "summary": "A"}
    ]


def test_same_second_suffixed_handoffs_both_surface_as_candidates(
    tmp_save_dir, run_validator
):
    # Two parallel sessions handing off at the identical second (see
    # context_schema.context_filename): the random suffix keeps both files on
    # disk instead of one overwriting the other, and both must be reported.
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="A", suffix="a1b2c3")
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="B", suffix="d4e5f6")
    out = run_validator()
    candidates = out["checks"]["context_candidates"]
    assert len(candidates) == 2
    assert {c["name"] for c in candidates} == {
        "CONTEXT-2026-08-08-09-00-00-a1b2c3.md",
        "CONTEXT-2026-08-08-09-00-00-d4e5f6.md",
    }
    assert all(c["timestamp"] == "2026-08-08-09-00-00" for c in candidates)


def test_context_file_flag_invalid_name_errors_without_crash(
    tmp_save_dir, run_validator
):
    write_context(tmp_save_dir, "2026-08-08-09-00-00", summary="A")
    out = run_validator(args=["--context-file", "CONTEXT-does-not-exist.md"])
    assert out["ok"] is False
    assert out["errors"]


def test_continuity_report_present_over_rendered_block(tmp_path):
    # Producer -> consumer seam: build metadata with the real session_capture
    # probe, render it with the real render_metadata_block, then assert the
    # validator's single run surfaces report.continuity for every recorded
    # field. No hand-built block.
    import session_capture

    _init_repo(tmp_path)
    # Save root lives OUTSIDE the repo so writing the CONTEXT fixture does not
    # dirty the working tree the continuity check then recomputes.
    save_root = tmp_path.parent / f"{tmp_path.name}-sct"
    d = _save_dir(save_root, tmp_path)
    meta_obj = session_capture.collect_git(str(tmp_path))
    block = context_schema.render_metadata_block(meta_obj)
    (d / "CONTEXT-2026-08-08-10-00-00.md").write_text(
        block + "\n### Key Context\n- `src/keep.py:1`\n"
    )
    out = _run(str(tmp_path), save_root)
    cont = out["report"]["continuity"]
    assert set(cont) >= {"cwd", "commits", "working_tree", "remote", "pr", "paths"}
    # The lone commit from _init_repo is still resolvable in this same repo.
    assert cont["commits"][0]["resolvable"] is True
    assert cont["working_tree"]["current"] == "clean"
    # No PR was recorded, so the PR check reports the no-PR note, not a crash.
    assert cont["pr"]["current_state"] is None


def test_continuity_smoke_cwd_match_over_real_render(tmp_path):
    # Fresh-subprocess smoke over the real producer -> consumer seam
    # (subprocess-boundary smoke): capture with session_capture,
    # render with render_metadata_block, then run validator.py as its own
    # process and assert the round-tripped cwd matches and the working-tree
    # check is present. Exercises the real boundary, not a mock.
    import session_capture

    _init_repo(tmp_path)
    save_root = tmp_path.parent / f"{tmp_path.name}-smoke-sct"
    d = _save_dir(save_root, tmp_path)
    meta_obj = session_capture.collect_git(str(tmp_path))
    (d / "CONTEXT-2026-08-08-11-00-00.md").write_text(
        context_schema.render_metadata_block(meta_obj)
    )
    out = _run(str(tmp_path), save_root)
    cont = out["report"]["continuity"]
    assert cont["cwd"]["match"] is True
    assert "current" in cont["working_tree"]


def test_main_records_error_and_reports_not_ok(tmp_path, monkeypatch, capsys):
    save_root = tmp_path / "sct"
    monkeypatch.setattr(context_schema, "SAVE_ROOT", save_root)
    d = save_root / context_schema.repo_slug(tmp_path)
    d.mkdir(parents=True)
    (d / "CONTEXT-2026-07-19-14-30-00.md").write_text(
        "### Session Metadata\n\n- git_sha: deadbeef\n- git_branch: main\n"
    )

    def _boom(*a, **k):
        raise RuntimeError("drift exploded")

    monkeypatch.setattr(validator.git_drift, "compute_drift", _boom)
    monkeypatch.chdir(tmp_path)
    validator.main(argv=[])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["errors"]
    assert any("drift exploded" in e for e in out["errors"])


def test_sibling_newer_handoff_surfaced_as_candidate(tmp_path):
    # Primary checkout on main with a STALE handoff; a linked feature worktree
    # holds a strictly-newer handoff under its own slug save_dir. Resume run
    # from primary must keep the primary selection AND surface the sibling.
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/x", str(linked)],
        cwd=primary,
        check=True,
    )
    save_root = tmp_path / "sct"
    pd = _save_dir(save_root, primary)
    (pd / "CONTEXT-2026-08-08-09-09-00.md").write_text(
        "## Session Summary\n\nstale primary handoff\n"
    )
    ld = _save_dir(save_root, linked)
    (ld / "CONTEXT-2026-08-08-21-57-00.md").write_text(
        "## Session Summary\n\nactive feature handoff\n"
    )
    out = _run(str(primary), save_root)
    # No auto-switch: primary selection is still the stale main-slug file.
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-09-09-00.md"
    cands = out["checks"]["cross_worktree_candidates"]
    assert len(cands) == 1
    c = cands[0]
    assert c["name"] == "CONTEXT-2026-08-08-21-57-00.md"
    assert c["timestamp"] == "2026-08-08-21-57-00"
    assert c["summary"] == "active feature handoff"
    assert pathlib.Path(c["toplevel"]).resolve() == linked.resolve()


def test_single_worktree_no_cross_candidates(tmp_path):
    # A repo with no linked worktree must produce zero sibling candidates
    # (the cwd slug is excluded from its own scan).
    _init_repo(tmp_path)
    save_root = tmp_path / "sct"
    d = _save_dir(save_root, tmp_path)
    (d / "CONTEXT-2026-08-08-10-00-00.md").write_text(
        "## Session Summary\n\nonly handoff\n"
    )
    out = _run(str(tmp_path), save_root)
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-10-00-00.md"
    assert out["checks"]["cross_worktree_candidates"] == []


def test_non_git_no_cross_candidates(tmp_path):
    # tmp_path is not a git repo: worktree_toplevels degrades to [] and the
    # scan yields nothing, with the existing selection unaffected.
    save_root = tmp_path / "sct"
    d = save_root / context_schema.repo_slug(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / "CONTEXT-2026-08-08-11-00-00.md").write_text(
        "## Session Summary\n\nnongit handoff\n"
    )
    out = _run(str(tmp_path), save_root)
    assert out["ok"] is True
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-11-00-00.md"
    assert out["checks"]["cross_worktree_candidates"] == []


def test_sibling_older_handoff_not_surfaced(tmp_path):
    # A sibling whose newest handoff is OLDER than the primary selection must
    # not be surfaced (strictly-newer rule).
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/y", str(linked)],
        cwd=primary,
        check=True,
    )
    save_root = tmp_path / "sct"
    pd = _save_dir(save_root, primary)
    (pd / "CONTEXT-2026-08-08-22-00-00.md").write_text(
        "## Session Summary\n\nnewer primary\n"
    )
    ld = _save_dir(save_root, linked)
    (ld / "CONTEXT-2026-08-08-08-00-00.md").write_text(
        "## Session Summary\n\nolder sibling\n"
    )
    out = _run(str(primary), save_root)
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-22-00-00.md"
    assert out["checks"]["cross_worktree_candidates"] == []


def test_context_file_sibling_worktree_absolute_path_resolves(tmp_path):
    # Explicit absolute path to a sibling worktree's handoff, resume run from
    # the primary checkout, must resolve that file and compute the report
    # against its validated recorded CWD (continuity.cwd matches), not error out.
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/x", str(linked)],
        cwd=primary,
        check=True,
    )
    (linked / "src" / "linked_only.py").write_text("linked = True\n")
    subprocess.run(["git", "add", "-A"], cwd=linked, check=True)
    subprocess.run(["git", "commit", "-qm", "linked"], cwd=linked, check=True)
    linked_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=linked,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    save_root = tmp_path / "sct"
    ld = _save_dir(save_root, linked)
    sib = ld / "CONTEXT-2026-08-08-21-57-00.md"
    sib.write_text(
        "## Session Summary\n\nactive feature handoff\n\n"
        "### Session Metadata\n\n"
        f"- git_sha: {linked_sha}\n"
        "- git_branch: feat/x\n"
        f"- cwd: {linked.resolve()}\n\n"
        "## Key Context\n\n- `src/linked_only.py:1`\n"
    )
    out = _run_cf(primary, save_root, sib)
    assert out["ok"] is True, out["errors"]
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-21-57-00.md"
    assert out["report"]["drift"]["current_sha"] == linked_sha
    assert out["report"]["drift"]["current_branch"] == "feat/x"
    assert out["report"]["refs"] == [
        {
            "raw": "`src/linked_only.py:1`",
            "path": "src/linked_only.py",
            "line": 1,
            "exists": True,
            "line_ok": True,
        }
    ]
    assert out["report"]["continuity"]["cwd"]["match"] is True


def test_context_file_sibling_selection_exposes_canonical_path_and_marker(tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/x", str(linked)],
        cwd=primary,
        check=True,
    )
    save_root = tmp_path / "sct"
    sib = _save_dir(save_root, linked) / "CONTEXT-2026-08-08-21-57-00.md"
    sib.write_text(
        "## Session Summary\n\nactive\n\n### Session Metadata\n\n"
        f"- cwd: {linked.resolve()}\n"
    )

    out = _run_cf(primary, save_root, sib)

    assert out["checks"]["context_file"] == sib.name
    assert out["checks"]["context_path"] == str(sib.resolve())
    assert out["checks"]["explicit_sibling"] is True


def test_context_file_sibling_uses_recorded_subdirectory_for_report(tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/subdir", str(linked)],
        cwd=primary,
        check=True,
    )
    recorded = linked / "work"
    (recorded / "notes").mkdir(parents=True)
    (recorded / "notes" / "handoff.txt").write_text("linked\n")
    subprocess.run(["git", "add", "-A"], cwd=linked, check=True)
    subprocess.run(["git", "commit", "-qm", "linked"], cwd=linked, check=True)
    linked_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=linked,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    save_root = tmp_path / "sct"
    sib = _save_dir(save_root, linked) / "CONTEXT-2026-08-08-21-57-00.md"
    sib.write_text(
        "## Session Summary\n\nactive\n\n### Session Metadata\n\n"
        f"- git_sha: {linked_sha}\n"
        "- git_branch: feat/subdir\n"
        f"- cwd: {recorded.resolve()}\n\n"
        "## Key Context\n\n- `notes/handoff.txt:1`\n"
    )

    out = _run_cf(primary, save_root, sib)

    assert out["ok"] is True, out["errors"]
    assert out["report"]["drift"]["current_sha"] == linked_sha
    assert out["report"]["refs"][0]["exists"] is True
    assert out["report"]["continuity"]["cwd"]["match"] is True


@pytest.mark.parametrize(
    "recorded_kind",
    [
        "missing",
        "outside",
        "relative",
        "inaccessible",
        "unknown_user",
        "symlink_loop",
    ],
)
def test_context_file_sibling_rejects_invalid_recorded_cwd(
    tmp_path, recorded_kind, request
):
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/x", str(linked)],
        cwd=primary,
        check=True,
    )
    if recorded_kind == "missing":
        recorded = linked / "missing"
    elif recorded_kind == "outside":
        recorded = primary.resolve()
    elif recorded_kind == "relative":
        recorded = pathlib.Path("../linked")
    elif recorded_kind == "inaccessible":
        recorded = linked / "inaccessible"
        recorded.mkdir()
        recorded.chmod(0)
        request.addfinalizer(lambda: recorded.chmod(0o700))
        assert not os.access(recorded, os.X_OK)
    elif recorded_kind == "unknown_user":
        recorded = pathlib.Path("~definitely-no-such-user-7f3b91")
    else:
        recorded = linked / "cwd-loop"
        recorded.symlink_to(recorded)
    save_root = tmp_path / "sct"
    sib = _save_dir(save_root, linked) / "CONTEXT-2026-08-08-21-57-00.md"
    sib.write_text(
        f"## Session Summary\n\nactive\n\n### Session Metadata\n\n- cwd: {recorded}\n"
    )

    out = _run_cf(primary, save_root, sib)

    assert out["ok"] is False
    assert out["report"] is None
    assert out["errors"] == [
        (
            "recorded working directory for sibling context file must be an accessible "
            f"directory inside the selected worktree: {recorded}"
        )
    ]


def test_context_file_unregistered_worktree_path_rejected(tmp_path):
    # A real CONTEXT-*.md that is not inside the current checkout's save_dir
    # NOR any registered sibling worktree stays rejected with the contract error.
    _init_repo(tmp_path)  # single worktree, no siblings
    save_root = tmp_path / "sct"
    _save_dir(save_root, tmp_path)
    stray = tmp_path / "stray"
    stray.mkdir()
    orphan = stray / "CONTEXT-2026-08-08-09-00-00.md"
    orphan.write_text("## Session Summary\n\nstray\n")
    out = _run_cf(tmp_path, save_root, orphan)
    assert out["ok"] is False
    assert any("registered sibling worktree" in e for e in out["errors"])
    assert out["checks"]["context_file"] is None


def test_context_file_same_checkout_absolute_path_report_cwd_unchanged(tmp_path):
    _init_repo(tmp_path)
    save_root = tmp_path / "sct"
    d = _save_dir(save_root, tmp_path)
    f = d / "CONTEXT-2026-08-08-09-00-00.md"
    f.write_text(
        "## Session Summary\n\nA\n\n### Session Metadata\n\n- cwd: "
        + str(tmp_path.resolve())
        + "\n"
    )
    out = _run_cf(tmp_path, save_root, f)
    assert out["ok"] is True, out["errors"]
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-09-00-00.md"
    assert out["report"]["continuity"]["cwd"]["match"] is True


def test_cross_repo_explicit_handoff_rejected_before_cwd_restoration(tmp_path):
    # AC1 characterization: resumed from repo A, an explicit
    # --context-file pointing at an UNRELATED repo B's handoff (B is not a
    # linked worktree of A) is rejected by the Step 1 validator with the
    # generic context-file contract error, before any recorded-CWD restoration
    # can occur. This is the bug the pre-validator bootstrap seam fixes; the
    # validator itself stays unchanged, so this test locks the pre-fix
    # behavior rather than asserting the fix.
    repo_a = tmp_path / "repoA"
    repo_a.mkdir()
    _init_repo(repo_a)
    repo_b = tmp_path / "repoB"
    repo_b.mkdir()
    _init_repo(repo_b)
    # Handoff sits in an unrelated slug dir, not repo A's save_dir.
    b_save = tmp_path / "sct" / "repoB-unrelatedslug"
    b_save.mkdir(parents=True)
    handoff = b_save / "CONTEXT-2026-08-08-09-00-00.md"
    handoff.write_text(
        "## Session Summary\n\nB\n\n"
        + context_schema.render_metadata_block(
            context_schema.SessionMetadata(
                cwd=str(repo_b.resolve()), git_sha="x", git_branch="main"
            )
        )
    )

    out = _run_cf(repo_a, tmp_path / "sct", handoff)

    assert out["ok"] is False
    assert out["checks"]["context_file"] is None
    assert any("registered sibling worktree" in e for e in out["errors"])
