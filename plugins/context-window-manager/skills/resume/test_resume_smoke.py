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

_VAL = Path(__file__).parent / "validator.py"


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
