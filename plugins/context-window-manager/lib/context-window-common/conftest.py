"""Make this skill's modules importable as top-level modules from any test
in this directory. Mirrors the sp-request-review conftest pattern; needed
because the repo runs pytest with --import-mode=importlib, which does not
add the test file's directory to sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))
