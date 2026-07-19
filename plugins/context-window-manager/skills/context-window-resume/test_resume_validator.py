import importlib.util
import json
import os
import subprocess
import sys
import pathlib

import context_schema

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
        [sys.executable, VAL], cwd=cwd, capture_output=True, text=True, env=env
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
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    save_root = tmp_path / "sct"
    d = _save_dir(save_root, tmp_path)
    (d / "CONTEXT-sample.md").write_text(
        "### Session Metadata\n\n"
        f"- git_sha: {stored}\n- git_sha_short: {stored[:7]}\n- git_branch: main\n\n"
        "### Key Context\n- `src/keep.py:1` and `src/gone.py`\n"
    )
    (tmp_path / "n.py").write_text("y=2\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "c2"], cwd=tmp_path, check=True)
    out = _run(str(tmp_path), save_root)
    assert out["checks"]["context_file"] == "CONTEXT-sample.md"
    assert out["report"]["drift"]["ahead"] == 1
    refs = {r["path"]: r["exists"] for r in out["report"]["refs"]}
    assert refs["src/keep.py"] is True and refs["src/gone.py"] is False


def test_newest_context_file_wins(tmp_path):
    # The skill's headline behavior: resume from the MOST RECENT handoff. Names
    # are CONTEXT-<sortable-timestamp>.md, so lexicographically-latest == newest.
    # Non-dated fixture names avoid the publish.sh leak-check false-positive on
    # CONTEXT-<year> while still exercising sorted(glob)[-1]. Written oldest-last
    # so filesystem creation order can't be what makes the assertion pass.
    _init_repo(tmp_path)
    stored = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    save_root = tmp_path / "sct"
    d = _save_dir(save_root, tmp_path)
    meta = (
        "### Session Metadata\n\n"
        f"- git_sha: {stored}\n- git_sha_short: {stored[:7]}\n- git_branch: main\n"
    )
    # Numeric prefixes stand in for the sortable timestamp: 2 sorts after 1, so
    # the "-2-" file is the newest and must win regardless of write order.
    (d / "CONTEXT-sample-2-new.md").write_text(meta)
    (d / "CONTEXT-sample-1-old.md").write_text(meta)
    out = _run(str(tmp_path), save_root)
    assert out["checks"]["context_file"] == "CONTEXT-sample-2-new.md"


def test_main_records_error_and_reports_not_ok(tmp_path, monkeypatch, capsys):
    save_root = tmp_path / "sct"
    monkeypatch.setattr(context_schema, "SAVE_ROOT", save_root)
    d = save_root / context_schema.repo_slug(tmp_path)
    d.mkdir(parents=True)
    (d / "CONTEXT-sample.md").write_text(
        "### Session Metadata\n\n- git_sha: deadbeef\n- git_branch: main\n"
    )

    def _boom(*a, **k):
        raise RuntimeError("drift exploded")

    monkeypatch.setattr(validator.git_drift, "compute_drift", _boom)
    monkeypatch.chdir(tmp_path)
    validator.main()
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["errors"]
    assert any("drift exploded" in e for e in out["errors"])
