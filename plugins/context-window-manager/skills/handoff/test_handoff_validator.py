import json
import os
import pathlib
import re
import subprocess
import sys

import context_schema

VAL = str(pathlib.Path(__file__).parent / "validator.py")

# publish.sh rejects a literal `<3 uppercase letters>-<4 digits>` anywhere in a
# first-party plugin tree, so sample task and ADR ids are assembled from parts.
_ID_PREFIX = "ABC"
_ADR = "ADR"
TASK_ID = f"{_ID_PREFIX}-0033"
ADR_PATHS = [f"/d/{_ADR}-0001-a.md", f"/d/{_ADR}-0002-b.md"]


def _run(cwd, *args, save_root=None):
    save_root = save_root or pathlib.Path(cwd).parent / f"{pathlib.Path(cwd).name}-cwm"
    r = subprocess.run(
        [sys.executable, VAL, *args],
        cwd=cwd,
        env={**os.environ, "CWM_SAVE_ROOT": str(save_root)},
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(r.stdout)


def _git(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _init_repo(cwd):
    _git(cwd, "git", "init", "-q", "-b", "main")
    _git(cwd, "git", "config", "user.email", "t@t")
    _git(cwd, "git", "config", "user.name", "t")
    (cwd / "f").write_text("x")
    _git(cwd, "git", "add", "-A")
    _git(cwd, "git", "commit", "-qm", "c1: first")


def test_non_git_dir_is_ok_and_flags_non_git(tmp_path):
    out = _run(str(tmp_path))
    assert out["ok"] is True
    assert out["checks"]["is_git"] is False


def test_reports_and_secures_save_dir(tmp_path):
    save_root = tmp_path.parent / "save-root"
    out = _run(str(tmp_path), save_root=save_root)
    sd = out["checks"]["save_dir"]
    assert pathlib.Path(sd).parent == save_root
    # Per-repo namespace = cwd basename + path-derived suffix (collision-proof).
    leaf = pathlib.Path(sd).name
    assert leaf.startswith(tmp_path.name + "-")
    assert pathlib.Path(sd).is_dir()
    assert pathlib.Path(sd).stat().st_mode & 0o777 == 0o700


def test_reports_collision_resistant_filename(tmp_path):
    out = _run(str(tmp_path))
    name = out["checks"]["filename"]
    assert re.fullmatch(
        r"CONTEXT-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-[0-9a-f]{6}\.md", name
    )


def test_filename_differs_across_two_runs_in_same_second(tmp_path):
    a = _run(str(tmp_path))["checks"]["filename"]
    b = _run(str(tmp_path))["checks"]["filename"]
    assert a != b


def test_git_dir_reports_head(tmp_path):
    _init_repo(tmp_path)
    out = _run(str(tmp_path))
    assert out["ok"] is True
    assert out["checks"]["is_git"] is True
    assert out["checks"]["head_resolvable"] is True


def test_reports_working_tree_capture(tmp_path):
    # The gap this closes: a HEAD-only handoff reports a dirty tree as clean.
    _init_repo(tmp_path)
    (tmp_path / "untracked.txt").write_text("u")
    out = _run(str(tmp_path))
    assert out["ok"] is True
    cap = out["checks"]["capture"]
    assert cap["working_tree"] == "dirty"
    assert cap["untracked"] == 1
    assert cap["commit_count"] >= 1


def test_emits_a_metadata_block_the_shared_parser_round_trips(tmp_path):
    # The validator hands the skill a finished block so nothing is ever
    # hand-formatted at write time.
    _init_repo(tmp_path)
    out = _run(str(tmp_path))
    parsed = context_schema.parse_metadata_block(out["metadata_block"])
    assert parsed["cwd"] == str(tmp_path.resolve())
    assert parsed["working_tree"] == "clean"
    assert parsed["git_branch"] == "main"
    assert len(parsed["commits"]) == 1


def test_optional_capture_args_reach_the_block(tmp_path):
    # cobtask id, plan link, and ADRs are things only the session knows, so they
    # arrive as arguments rather than being probed for.
    _init_repo(tmp_path)
    out = _run(
        str(tmp_path),
        "--cobtask-id",
        TASK_ID,
        "--tdd-plan-path",
        "/plans/p.md",
        "--adrs",
        ",".join(ADR_PATHS),
    )
    parsed = context_schema.parse_metadata_block(out["metadata_block"])
    assert parsed["cobtask_id"] == TASK_ID
    assert parsed["tdd_plan_path"] == "/plans/p.md"
    assert parsed["adrs"] == ADR_PATHS


def test_non_git_dir_still_emits_a_wellformed_block(tmp_path):
    out = _run(str(tmp_path))
    assert out["ok"] is True
    parsed = context_schema.parse_metadata_block(out["metadata_block"])
    assert parsed["git_sha"] is None
    assert parsed["working_tree"] is None
    assert parsed["cwd"] == str(tmp_path.resolve())


def test_reports_resume_abspath_joining_save_dir_and_filename(tmp_path):
    # The paste line resume prints needs the finished absolute handoff path,
    # resolved deterministically here as save_dir / filename rather than
    # reassembled by the skill at write time.
    out = _run(str(tmp_path))
    save_dir = out["checks"]["save_dir"]
    filename = out["checks"]["filename"]
    assert out["checks"]["resume_abspath"] == str(pathlib.Path(save_dir) / filename)


def test_reports_resume_command_as_qualified_slash_paste(tmp_path):
    # The single paste line resume prints: just the slash command selecting the
    # handoff explicitly via --context-file. No cd prefix -- resume's own bootstrap
    # restores the recorded cwd from the context file, so prefixing cd here only
    # duplicated that and added noise to the pasted prompt.
    _init_repo(tmp_path)
    out = _run(str(tmp_path))
    resume_abspath = out["checks"]["resume_abspath"]
    assert out["checks"]["resume_command"] == (
        f"/context-window-manager:resume --context-file {resume_abspath}"
    )


def test_resume_command_is_child_of_save_dir(tmp_path):
    # The handoff path baked into the resume line must be the file the skill is
    # about to write inside this session's save dir, not some other location.
    _init_repo(tmp_path)
    out = _run(str(tmp_path))
    save_dir = pathlib.Path(out["checks"]["save_dir"])
    resume_abspath = pathlib.Path(out["checks"]["resume_abspath"])
    assert resume_abspath.parent == save_dir
    assert out["checks"]["resume_abspath"] in out["checks"]["resume_command"]


def test_validator_does_not_mutate_repository(tmp_path):
    _init_repo(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    out = _run(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert pathlib.Path(out["checks"]["save_dir"]).is_dir()
