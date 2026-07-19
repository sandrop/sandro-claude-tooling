"""Make the shared context-window-common modules importable as top-level
modules from this skill's tests. Deliberately does NOT add this skill's own
directory to sys.path: both skills ship an identically-named validator.py, so a
bare `import validator` must not be able to resolve to a sibling skill's copy
(resume loads its own validator by explicit path; handoff invokes its via
subprocess). Only the shared library dir goes on the path.
"""

import sys
from pathlib import Path

_COMMON = Path(__file__).resolve().parent.parent / "context-window-common"
if str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))
