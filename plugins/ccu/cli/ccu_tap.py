#!/usr/bin/env python3
"""ccu statusline tap.

Run as Claude Code's ``statusLine`` command, or in front of an existing one:

    python3 ccu_tap.py                      # records only, prints nothing
    python3 ccu_tap.py -- npx -y ccstatusline@latest

Reads the statusline JSON Claude Code pipes on stdin and records its
``rate_limits`` object to ``<claude_root>/.sct/ccu/usage.json`` as
``{"captured_at": <epoch seconds float>, "rate_limits": {...verbatim...}}``.
The write is atomic (same-dir temp file, then ``os.replace``). Input without a
valid ``seven_day`` window and file-recording failures are ignored: the
previous file stays untouched and the wrapped command still receives stdin.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import BinaryIO

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from config import default_tap_file
from usage_source import usage_from_seven_day


def record(data: bytes, tap_file: Path) -> bool:
    """Write ``rate_limits`` from ``data`` to ``tap_file``; True when written."""
    tmp_name: str | None = None
    try:
        rate_limits = json.loads(data).get("rate_limits")
        if not isinstance(rate_limits, dict) or not isinstance(
            rate_limits.get("seven_day"), dict
        ):
            return False
        # Refuse what the reader would refuse, so a bad render never replaces
        # a usable file.
        usage_from_seven_day(rate_limits["seven_day"])
        tap_file.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {"captured_at": time.time(), "rate_limits": rate_limits}, sort_keys=True
        )
        with tempfile.NamedTemporaryFile(
            "w", dir=tap_file.parent, prefix=".usage-", suffix=".tmp", delete=False
        ) as tmp:
            tmp_name = tmp.name
            tmp.write(body)
        os.replace(tmp_name, tap_file)
    except Exception:  # noqa: BLE001 - the tap must never break the status line
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                Path(tmp_name).unlink(missing_ok=True)
        return False
    return True


def forward(data: bytes, cmd: list[str]) -> int:
    """Pipe ``data`` to ``cmd`` with inherited stdout/stderr; return its exit code."""
    try:
        return subprocess.run(cmd, input=data, check=False).returncode
    except (FileNotFoundError, PermissionError) as e:
        print(f"ccu_tap: cannot run {cmd[0]}: {e.strerror}", file=sys.stderr)
        return 127


def main(argv: list[str] | None = None, stdin: BinaryIO | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    data = (stdin or sys.stdin.buffer).read()
    record(data, default_tap_file())
    if not argv:
        return 0
    if argv[0] != "--" or len(argv) < 2:
        print("usage: ccu_tap.py [-- <statusLine command>...]", file=sys.stderr)
        return 0
    return forward(data, argv[1:])


if __name__ == "__main__":
    sys.exit(main())
