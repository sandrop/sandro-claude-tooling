"""Install-seam smoke tests for the resume skill's CWD-restoration contract.

Unlike test_skill_cwd_restore_contract.py, which reads the *development* SKILL.md
next to this file, these tests resolve the skill through the real installed
runtime boundary ``${CLAUDE_PLUGIN_ROOT}``. That is the copy the Claude Code
runtime actually invokes, so a source-only contract test cannot stay green while
the invoked plugin cache remains stale.

Skip-guarded when ``CLAUDE_PLUGIN_ROOT`` is unset, mirroring
handoff/test_handoff_smoke.py. A skip in the repo gate is
acceptable; a skipped *failure* is not.
"""

import os
import subprocess
from pathlib import Path

import pytest


def _skill_path() -> Path:
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        pytest.skip("install-seam smoke needs CLAUDE_PLUGIN_ROOT (installed runtime)")
    return Path(root) / "skills" / "resume" / "SKILL.md"


def _extract(skill_text: str, name: str) -> str:
    """Return the ``# <name>`` ... ``# end-<name>`` block, markers included.

    Raises ValueError ("substring not found") when the marker is absent, which
    is precisely the RED signal against a pre-contract (v0.6.1) cached skill.
    """
    start_marker = f"# {name}"
    end_marker = f"# end-{name}"
    start = skill_text.index(start_marker)
    end = skill_text.index(end_marker, start)
    return skill_text[start : end + len(end_marker)]


def _run_restore(contract: str, start: Path, recorded: Path, marker: Path):
    script = 'cd() { : > "$CD_MARKER"; builtin cd "$@"; }\n' + contract + "\npwd -P\n"
    return subprocess.run(
        ["bash", "-c", script],
        cwd=start,
        env={
            **os.environ,
            "recorded_cwd": str(recorded),
            "CD_MARKER": str(marker),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_installed_skill_restores_cwd_across_process_boundary(tmp_path):
    skill = _skill_path()
    assert skill.is_file(), f"invoked skill missing: {skill}"
    contract = _extract(skill.read_text(), "cwd-restore-contract")

    start = tmp_path / "session"
    start.mkdir()
    recorded = tmp_path / "task-worktree"
    recorded.mkdir()
    marker = tmp_path / "cd-marker"

    result = _run_restore(contract, start, recorded, marker)

    assert marker.exists(), f"no cd side effect; stderr={result.stderr}"
    assert result.stdout.strip().endswith(str(recorded)), result.stdout


def test_matching_cwd_is_a_noop(tmp_path):
    skill = _skill_path()
    contract = _extract(skill.read_text(), "cwd-restore-contract")

    start = tmp_path / "same"
    start.mkdir()
    marker = tmp_path / "cd-marker"

    result = _run_restore(contract, start, start, marker)

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "matching CWD must not trigger a cd"


def test_missing_recorded_cwd_stops_before_confirm(tmp_path):
    skill = _skill_path()
    contract = _extract(skill.read_text(), "cwd-restore-contract")

    start = tmp_path / "session"
    start.mkdir()
    missing = tmp_path / "does-not-exist"

    result = subprocess.run(
        ["bash", "-c", contract + "\n"],
        cwd=start,
        env={**os.environ, "recorded_cwd": str(missing)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, "missing recorded CWD must stop (non-zero exit)"
    assert str(missing) in (result.stdout + result.stderr), (
        "must name the offending path"
    )
