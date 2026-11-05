"""Test-time bootstrap for the ccu plugin.

Puts the plugin's lib/ dir on sys.path so `import config` / `import forecast`
/ `import usage_source` resolve in every test under this tree, and the cli/
dir so `import ccu_forecast` resolves regardless of collection order. Mirrors
the pluginator plugin's bootstrap so ./run_tests.sh discovers this suite the
same way.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

_CLI = Path(__file__).resolve().parent / "cli"
if str(_CLI) not in sys.path:
    sys.path.insert(0, str(_CLI))
