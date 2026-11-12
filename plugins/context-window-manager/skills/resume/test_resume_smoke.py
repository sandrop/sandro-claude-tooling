"""Fresh-subprocess boundary smoke for cross-worktree candidate surfacing.

Per the subprocess-boundary-smoke pattern (docs/patterns/subprocess-boundary-smoke.md):
the cross-worktree scan crosses the validator's process boundary (it shells out to
`git worktree list --porcelain` and reads sibling save_dirs), so it ships one
fresh-subprocess smoke that invokes the real validator.py against a real
two-worktree git layout and asserts the actual side effect, not a mock.

conftest.py already puts the shared context-window-common dir on sys.path, so
`context_schema` imports as a top-level module here.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import context_schema
from test_installed_cwd_restore_smoke import _extract
from test_skill_cwd_restore_contract import _cwd_contract, _selection_contract

_VAL = Path(__file__).parent / "validator.py"
_SKILL = Path(__file__).with_name("SKILL.md")
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent


def _init_repo(p: Path) -> None:
    for a in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(a, cwd=p, check=True)
    (p / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=p, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=p, check=True)


def test_cross_worktree_candidate_surfaced_via_real_subprocess(tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/smoke", str(linked)],
        cwd=primary,
        check=True,
    )
    save_root = tmp_path / "sct"
    pd = save_root / context_schema.repo_slug(primary)
    pd.mkdir(parents=True)
    (pd / "CONTEXT-2026-08-08-09-09-00.md").write_text("## Session Summary\n\nstale\n")
    ld = save_root / context_schema.repo_slug(linked)
    ld.mkdir(parents=True)
    (ld / "CONTEXT-2026-08-08-21-57-00.md").write_text("## Session Summary\n\nactive\n")
    env = {**os.environ, "CWM_SAVE_ROOT": str(save_root)}
    r = subprocess.run(
        [sys.executable, str(_VAL)],
        cwd=primary,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    # No auto-switch: the primary same-slug selection is untouched.
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-09-09-00.md"
    cands = out["checks"]["cross_worktree_candidates"]
    assert [c["name"] for c in cands] == ["CONTEXT-2026-08-08-21-57-00.md"]
    assert Path(cands[0]["toplevel"]).resolve() == linked.resolve()


def test_explicit_sibling_path_resolves_via_real_subprocess(tmp_path):
    # An explicit absolute sibling-worktree --context-file path resolves against
    # its own worktree through the real validator subprocess: the resolution and
    # the report-cwd rebind (continuity.cwd.match) are the asserted side effect.
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/smoke", str(linked)],
        cwd=primary,
        check=True,
    )
    recorded_cwd = linked / "recorded-cwd"
    (recorded_cwd / "linked-only").mkdir(parents=True)
    (recorded_cwd / "linked-only" / "ref.txt").write_text("linked\n")
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
    ld = save_root / context_schema.repo_slug(linked)
    ld.mkdir(parents=True)
    sib = ld / "CONTEXT-2026-08-08-21-57-00.md"
    sib.write_text(
        "## Session Summary\n\nactive\n\n### Session Metadata\n\n"
        f"- git_sha: {linked_sha}\n"
        "- git_branch: feat/smoke\n"
        f"- cwd: {recorded_cwd.resolve()}\n\n"
        "## Key Context\n\n- `linked-only/ref.txt:1`\n"
    )
    env = {**os.environ, "CWM_SAVE_ROOT": str(save_root)}
    r = subprocess.run(
        [sys.executable, str(_VAL), "--context-file", str(sib)],
        cwd=primary,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True, out["errors"]
    assert out["checks"]["context_file"] == "CONTEXT-2026-08-08-21-57-00.md"
    assert out["checks"]["context_path"] == str(sib.resolve())
    assert out["checks"]["explicit_sibling"] is True
    assert out["report"]["drift"]["current_sha"] == linked_sha
    assert out["report"]["drift"]["current_branch"] == "feat/smoke"
    assert out["report"]["refs"][0]["path"] == "linked-only/ref.txt"
    assert out["report"]["refs"][0]["exists"] is True
    assert out["report"]["refs"][0]["line_ok"] is True
    assert out["report"]["continuity"]["cwd"]["match"] is True


def test_appended_text_diagnostic_via_real_subprocess(tmp_path):
    # Appended-text subprocess-boundary smoke: a --context-file value with
    # instruction prose glued onto a real CONTEXT-*.md path must fail loudly at the real
    # process boundary with the distinct "extra text was appended" diagnostic,
    # never resuming the wrong file.
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    save_root = tmp_path / "sct"
    pd = save_root / context_schema.repo_slug(primary)
    pd.mkdir(parents=True)
    ctx = pd / "CONTEXT-2026-08-26-07-17-47-a1911e.md"
    ctx.write_text("## Session Summary\n\nactive\n")
    bad = f"{ctx} and make sure you cd to the correct working directory."
    env = {**os.environ, "CWM_SAVE_ROOT": str(save_root)}
    r = subprocess.run(
        [sys.executable, str(_VAL), "--context-file", bad],
        cwd=primary,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert out["checks"]["context_file"] is None
    assert any("extra text was appended" in e for e in out["errors"])


def _continue_run(tmp_path: Path, drift_commit: bool) -> dict:
    # Real repo, real captured Session Metadata, real validator subprocess, fed
    # the exact paste-line suffix the handoff prints.
    import session_capture

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    save_root = tmp_path / "sct"
    d = save_root / context_schema.repo_slug(repo)
    d.mkdir(parents=True)
    ctx = d / "CONTEXT-2026-11-11-10-00-00.md"
    ctx.write_text(
        "## Session Summary\n\nx\n\n"
        + context_schema.render_metadata_block(session_capture.collect_git(str(repo)))
    )
    if drift_commit:
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "d"], cwd=repo, check=True
        )
    r = subprocess.run(
        [sys.executable, str(_VAL), "--context-file", f"{ctx} and continue"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "CWM_SAVE_ROOT": str(save_root)},
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True, out["errors"]
    assert out["checks"]["context_path"] == str(ctx)
    assert out["checks"]["continue_requested"] is True
    return out


def test_continue_directive_via_real_subprocess(tmp_path):
    assert (
        _continue_run(tmp_path, drift_commit=False)["report"]["auto_continue"] is True
    )


def test_continue_directive_with_drift_shows_menu_via_real_subprocess(tmp_path):
    assert (
        _continue_run(tmp_path, drift_commit=True)["report"]["auto_continue"] is False
    )


def _step3_recipe() -> str:
    # The whole Step 3 shell recipe from SKILL.md: bind, restore, refresh.
    region = _cwd_contract()
    start = region.index("# cwd-bind-contract")
    end = region.index("\n```", start)
    return region[start:end]


def test_cwd_refresh_drops_continue_directive_via_real_subprocess(tmp_path):
    # A Step 3 cwd mismatch must still end at the menu. Once the restored cwd
    # matches and HEAD and the tree stay clean, the only thing keeping
    # report.auto_continue false is that the refresh re-runs the validator
    # with the bare path. Drives the real Step 3 recipe from SKILL.md.
    import session_capture

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    sub = repo / "sub"
    sub.mkdir()  # an empty dir leaves `git status` clean
    save_root = tmp_path / "sct"
    d = save_root / context_schema.repo_slug(repo)
    d.mkdir(parents=True)
    ctx = d / "CONTEXT-2026-11-11-10-00-00.md"
    ctx.write_text(
        "## Session Summary\n\nx\n\n"
        + context_schema.render_metadata_block(session_capture.collect_git(str(repo)))
    )
    env = {**os.environ, "CWM_SAVE_ROOT": str(save_root)}
    report_file = tmp_path / "validator-report.json"
    r = subprocess.run(
        [sys.executable, str(_VAL), "--context-file", f"{ctx} and continue"],
        cwd=sub,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    report_file.write_text(r.stdout)
    first = json.loads(r.stdout)
    assert first["ok"] is True, first["errors"]
    assert first["checks"]["continue_requested"] is True
    assert first["report"]["continuity"]["cwd"]["match"] is False
    assert first["report"]["auto_continue"] is False

    _bash(
        _step3_recipe(),
        sub,
        {
            **env,
            "validator_report_file": str(report_file),
            "CLAUDE_PLUGIN_ROOT": str(_PLUGIN_ROOT),
        },
    )
    refreshed = json.loads(report_file.read_text())
    assert refreshed["ok"] is True, refreshed["errors"]
    assert refreshed["checks"]["context_path"] == str(ctx)
    assert refreshed["report"]["continuity"]["cwd"]["match"] is True
    assert refreshed["report"]["drift"]["ahead"] == 0
    assert refreshed["report"]["drift"]["behind"] == 0
    assert refreshed["report"]["continuity"]["working_tree"]["current"] == "clean"
    assert refreshed["checks"]["continue_requested"] is False
    assert refreshed["report"]["auto_continue"] is False


def test_step4_auto_continue_exception_reads_validator_boolean():
    # The menu skip is driven only by report.auto_continue, sits after the
    # menu it overrides, and falls back to that menu otherwise. Deleting or
    # loosening the exception must fail here.
    text = _SKILL.read_text()
    step4 = text[
        text.index("### Step 4: Confirm Direction") : text.index("## Error Handling")
    ]
    menu = step4.index("1. Continue with the next steps listed above")
    exception = step4.index("**Auto-continue exception.**")
    assert menu < exception
    assert "`report.auto_continue` true, skip the menu" in step4
    assert "start the handoff's Next Steps" in step4
    assert "Otherwise show the menu above unchanged." in step4
    rules = text[text.index("## Rules") :]
    assert "The one exception is `report.auto_continue` true" in rules


def _step0_recipe() -> str:
    # The Step 0 bootstrap recipe from SKILL.md.
    return _extract(_SKILL.read_text(), "cwd-bootstrap-contract")


def _step1_recipe() -> str:
    # The Step 1 selection recipe from SKILL.md (the first bash block there).
    region = _selection_contract()
    start = region.index("```bash\n") + len("```bash\n")
    return region[start : region.index("\n```", start)]


def _bash(script: str, cwd: Path, env: dict) -> str:
    r = subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _printed(stdout: str, label: str) -> "str | None":
    for line in stdout.splitlines():
        if line.startswith(label):
            return line[len(label) :]
    return None


def _explicit_resume_flow(tmp_path: Path, start_in_subdir: bool) -> dict:
    # Drives the real Step 0, Step 1 and Step 3 recipes from SKILL.md with the
    # exact paste-line value, binding each step's printed values into the next
    # the way the skill does, and returns the final validator report.
    import session_capture

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    sub = repo / "sub"
    sub.mkdir()  # an empty dir leaves `git status` clean
    save_root = tmp_path / "sct"
    d = save_root / context_schema.repo_slug(repo)
    d.mkdir(parents=True)
    ctx = d / "CONTEXT-2026-11-11-10-00-00.md"
    ctx.write_text(
        "## Session Summary\n\nx\n\n"
        + context_schema.render_metadata_block(session_capture.collect_git(str(repo)))
    )
    start = sub if start_in_subdir else repo
    env = {
        **os.environ,
        "CWM_SAVE_ROOT": str(save_root),
        "CLAUDE_PLUGIN_ROOT": str(_PLUGIN_ROOT),
        "TMPDIR": str(tmp_path),
        "explicit_context_file": f"{ctx} and continue",
    }
    step0 = _bash(_step0_recipe(), start, env)
    boot_cwd = _printed(step0, "bootstrapped code CWD: ")
    assert boot_cwd is not None, step0
    step1 = _bash(_step1_recipe(), start, {**env, "boot_cwd": boot_cwd})
    report_file = _printed(step1, "validator report file: ")
    assert report_file is not None, step1
    # Later tool calls run from the restored directory, so Step 3 sees no
    # mismatch and never runs its directive-dropping refresh.
    _bash(
        _step3_recipe(), Path(boot_cwd), {**env, "validator_report_file": report_file}
    )
    out = json.loads(Path(report_file).read_text())
    assert out["ok"] is True, out["errors"]
    assert out["checks"]["context_path"] == str(ctx)
    assert out["checks"]["continue_requested"] is True
    return out


def test_cwd_restored_by_bootstrap_auto_continues_via_real_subprocess(tmp_path):
    # The usual paste: a fresh session opened outside the recorded worktree.
    # Step 0 restores the recorded cwd before the validator runs, so the
    # report's cwd check matches and auto-continue stays on.
    out = _explicit_resume_flow(tmp_path, start_in_subdir=True)
    assert out["report"]["continuity"]["cwd"]["match"] is True
    assert out["report"]["auto_continue"] is True


def test_matching_cwd_through_bootstrap_auto_continues_via_real_subprocess(tmp_path):
    out = _explicit_resume_flow(tmp_path, start_in_subdir=False)
    assert out["report"]["auto_continue"] is True
