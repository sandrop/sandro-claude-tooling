import json
import subprocess
import sys
import pathlib

VAL = str(pathlib.Path(__file__).parent / "validator.py")


def _run(cwd):
    r = subprocess.run([sys.executable, VAL], cwd=cwd, capture_output=True, text=True)
    return json.loads(r.stdout)


def test_non_git_dir_is_ok_and_flags_non_git(tmp_path):
    out = _run(str(tmp_path))
    assert out["ok"] is True
    assert out["checks"]["is_git"] is False


def test_reports_home_scoped_save_dir(tmp_path):
    out = _run(str(tmp_path))
    sd = out["checks"]["save_dir"]
    assert "/.claude/.sct/context-window-manager/" in sd
    # Per-repo namespace = cwd basename + path-derived suffix (collision-proof).
    leaf = pathlib.Path(sd).name
    assert leaf.startswith(tmp_path.name + "-")
    assert not pathlib.Path(sd).exists()  # read-only: reporting must not create it


def test_git_dir_reports_head(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=tmp_path, check=True)
    out = _run(str(tmp_path))
    assert out["ok"] is True
    assert out["checks"]["is_git"] is True
    assert out["checks"]["head_resolvable"] is True
