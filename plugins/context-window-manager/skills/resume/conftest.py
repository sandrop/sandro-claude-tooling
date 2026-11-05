"""Make the shared context-window-common modules importable as top-level
modules from this skill's tests. Deliberately does NOT add this skill's own
directory to sys.path: both skills ship an identically-named validator.py, so a
bare `import validator` must not be able to resolve to a sibling skill's copy
(resume loads its own validator by explicit path; handoff invokes its via
subprocess). Only the shared library dir goes on the path.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_COMMON = (
    Path(__file__).resolve().parent.parent.parent / "lib" / "context-window-common"
)
if str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))

import context_schema

_VAL_PATH = Path(__file__).parent / "validator.py"


def write_context_raw(
    save_dir: Path, ts: str, body: str, suffix: "str | None" = None
) -> None:
    name = f"CONTEXT-{ts}.md" if suffix is None else f"CONTEXT-{ts}-{suffix}.md"
    (Path(save_dir) / name).write_text(body)


def write_context(
    save_dir: Path, ts: str, summary: str, suffix: "str | None" = None
) -> None:
    write_context_raw(save_dir, ts, f"## Session Summary\n\n{summary}\n", suffix=suffix)


@pytest.fixture
def tmp_save_dir(tmp_path: Path) -> Path:
    """The save_dir a validator run against tmp_path (as cwd) resolves to,
    once CWM_SAVE_ROOT is redirected to tmp_path/'sct'. tmp_path itself is
    NOT a git repo here: the ambiguity-window tests only assert on
    context_candidates / context_file selection, and compute_drift/verify_refs
    degrade to is_git=False rather than raising outside a repo."""
    save_root = tmp_path / "sct"
    d = save_root / context_schema.repo_slug(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def run_validator(tmp_path: Path, tmp_save_dir: Path):
    """Callable that runs validator.py as a subprocess against tmp_path (cwd)
    with CWM_SAVE_ROOT redirected to tmp_save_dir's save_root, returning the
    parsed JSON stdout. Optional `args` forwards extra CLI flags."""
    save_root = tmp_save_dir.parent

    def _run(args: "list[str] | None" = None) -> dict:
        env = {**os.environ, "CWM_SAVE_ROOT": str(save_root)}
        cmd = [sys.executable, str(_VAL_PATH), *(args or [])]
        r = subprocess.run(
            cmd, cwd=tmp_path, capture_output=True, text=True, env=env, check=False
        )
        return json.loads(r.stdout)

    return _run
