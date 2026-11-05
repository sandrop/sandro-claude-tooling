"""Make the shared context-window-common modules importable as top-level
modules from the hook tests. Only the shared library dir goes on sys.path, so
the parse-back assertion can `import context_schema` in-process while the hook
itself runs as a fresh subprocess.
"""

import sys
from pathlib import Path

_COMMON = Path(__file__).resolve().parent.parent / "lib" / "context-window-common"
if str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))
