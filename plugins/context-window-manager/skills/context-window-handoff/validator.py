#!/usr/bin/env python3
# validator.py - context-window-handoff pre-flight.
import json
import subprocess
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parent.parent / "context-window-common"
sys.path.insert(0, str(COMMON))


# Cap the pre-flight git calls so a hung invocation (credential prompt, broken
# submodule, unresponsive network .git) or a missing git binary degrades to a
# structured "not a git repo" result instead of hanging the whole pre-flight.
_GIT_TIMEOUT = 10


def _git(*args):
    try:
        return subprocess.run(
            ("git", *args), capture_output=True, text=True, timeout=_GIT_TIMEOUT
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")


def main():
    checks, errors = {}, []
    try:
        import context_schema

        checks["common_import"] = "ok"
        # Report the home-scoped, per-repo save dir the handoff will write to,
        # so the skill can surface the target path. Read-only: the directory is
        # created at write time via context_schema.save_dir(cwd, create=True).
        checks["save_dir"] = str(context_schema.save_dir(Path.cwd()))
    except Exception as e:  # pragma: no cover
        errors.append(f"cannot import context-window-common: {e}")
        checks["common_import"] = "fail"
    is_git = _git("rev-parse", "--is-inside-work-tree").returncode == 0
    checks["is_git"] = is_git
    checks["head_resolvable"] = is_git and _git("rev-parse", "HEAD").returncode == 0
    print(json.dumps({"ok": not errors, "checks": checks, "errors": errors}, indent=2))


if __name__ == "__main__":
    main()
