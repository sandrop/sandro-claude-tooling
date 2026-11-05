"""Make the shared context-window-common modules importable as top-level
modules from this skill's tests. Deliberately does NOT add this skill's own
directory to sys.path: both skills ship an identically-named validator.py, so a
bare `import validator` must not be able to resolve to a sibling skill's copy
(handoff invokes its validator via subprocess; resume loads its own by explicit
path). Only the shared library dir goes on the path.
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

_VAL_PATH = Path(__file__).parent / "validator.py"


@pytest.fixture
def run_handoff_validator():
    """Callable that runs this skill's validator.py as a fresh subprocess with
    CWM_SAVE_ROOT redirected, returning the parsed JSON stdout. Mirrors
    resume/conftest.py's run_validator: the subprocess boundary
    is deliberate, since the handoff skill invokes its validator that way.

    - cwd: working directory the validator runs in (its own save_dir derives
      from this). Defaults to a required caller-supplied path.
    - save_root: value for CWM_SAVE_ROOT (where prune walks).
    - extra_env: extra environment overrides (e.g. SCT_CWM_RETENTION_DAYS).
    - args: extra CLI flags forwarded to validator.py.
    """

    def _run(cwd, save_root, extra_env=None, args=None) -> dict:
        env = {**os.environ, "CWM_SAVE_ROOT": str(save_root)}
        # Hermetic: ignore any SCT_CWM_RETENTION_DAYS the developer exported, so
        # the prune tests do not depend on local config. A test that wants a
        # specific value passes it explicitly via extra_env below.
        env.pop("SCT_CWM_RETENTION_DAYS", None)
        if extra_env:
            env.update(extra_env)
        cmd = [sys.executable, str(_VAL_PATH), *(args or [])]
        r = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, env=env, check=False
        )
        return json.loads(r.stdout)

    return _run
