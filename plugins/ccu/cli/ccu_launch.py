#!/usr/bin/env python3
"""Version-free launcher for the installed ccu cli scripts.

Copy once to ``~/.claude/.sct/ccu/ccu_launch.py``, then call it in place of a
versioned plugin cache path:

    python3 ~/.claude/.sct/ccu/ccu_launch.py ccu_tap.py -- \\
        python3 ~/.claude/.sct/ccu/ccu_launch.py ccu_forecast.py

It reads ``<claude_root>/plugins/installed_plugins.json``, picks the
highest-semver install of ``ccu@sandro-claude-tooling-dev`` or
``ccu@sandro-claude-tooling`` whose ``cli/<script>`` exists (dev wins a tie),
and execs it with the remaining arguments, so stdin passes through untouched.
When nothing resolves it pipes stdin to the command after ``--`` (if any) and
otherwise prints nothing and exits 0, so the status line never breaks.

Standalone on purpose: stdlib only, Python 3.9 safe, no import from the
plugin's ``lib/`` (it runs from outside the plugin).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO

# Dev listed last so it wins a version tie under max().
_KEYS = ("ccu@sandro-claude-tooling", "ccu@sandro-claude-tooling-dev")
# Numeric x.y.z only: git SHAs, pre-releases and other shapes are skipped.
_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")


def claude_root() -> Path:
    # Same rule as lib/config.py claude_root: strip, and blank means unset.
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env.strip()) if env and env.strip() else Path.home() / ".claude"


def resolve(script: str, root: Path) -> Path | None:
    """Return ``<installPath>/cli/<script>`` of the newest live ccu install."""
    if not script or Path(script).name != script:
        return None
    try:
        plugins = json.loads((root / "plugins" / "installed_plugins.json").read_text())[
            "plugins"
        ]
    except Exception:  # noqa: BLE001 - missing or malformed: nothing resolves
        return None
    best = None
    for rank, key in enumerate(_KEYS):
        entries = plugins.get(key) if isinstance(plugins, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            try:
                match = _VERSION.fullmatch(entry["version"])
                if match is None:
                    continue
                version = tuple(int(p) for p in match.groups())
                target = Path(entry["installPath"]) / "cli" / script
                if not target.is_file():
                    continue
            except Exception:  # noqa: BLE001, S112 - skip a bad or unreadable entry, keep the rest
                continue
            if best is None or (version, rank) > best[0]:
                best = ((version, rank), target)
    return best[1] if best else None


def main(argv: list[str] | None = None, stdin: BinaryIO | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    target = resolve(argv[0], claude_root()) if argv else None
    if target is not None:
        os.execv(sys.executable, [sys.executable, str(target), *argv[1:]])
    cmd = argv[argv.index("--") + 1 :] if "--" in argv else []
    if not cmd:
        return 0
    data = (stdin or sys.stdin.buffer).read()
    try:
        return subprocess.run(cmd, input=data, check=False).returncode
    except OSError as e:
        print(f"ccu_launch: cannot run {cmd[0]}: {e.strerror or e}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
