# git_drift.py
import re
import subprocess
from typing import TypedDict

_GIT_TIMEOUT = 10
_HEX_RE = re.compile(r"[0-9a-fA-F]{4,64}\Z")


class DriftResult(TypedDict):
    is_git: bool
    current_sha: "str | None"
    current_branch: "str | None"
    stored_resolvable: bool
    branch_exists: "bool | None"
    ahead: "int | None"
    behind: "int | None"
    note: str


def _git(cwd: str, *args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )


def compute_drift(
    stored_sha: "str | None", stored_branch: "str | None", cwd: str
) -> DriftResult:
    out: DriftResult = {
        "is_git": False,
        "current_sha": None,
        "current_branch": None,
        "stored_resolvable": False,
        "branch_exists": None,
        "ahead": None,
        "behind": None,
        "note": "",
    }
    # Every git call is wrapped so a timeout or a missing git binary on any
    # call (not just the first) degrades to a structured result instead of
    # propagating to a direct caller of this helper.
    try:
        inside = _git(cwd, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0:
            out["note"] = "not a git repo"
            return out
        out["is_git"] = True
        out["current_sha"] = _git(cwd, "rev-parse", "HEAD").stdout.strip() or None
        out["current_branch"] = (
            _git(cwd, "branch", "--show-current").stdout.strip() or None
        )
        if stored_branch:
            out["branch_exists"] = (
                _git(
                    cwd, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{stored_branch}",
                ).returncode
                == 0
            )
        if stored_sha is None:
            out["note"] = "no SHA recorded in handoff metadata"
        elif (
            _HEX_RE.match(stored_sha)
            and _git(cwd, "cat-file", "-e", stored_sha).returncode == 0
        ):
            out["stored_resolvable"] = True
            rl = _git(cwd, "rev-list", "--left-right", "--count", f"{stored_sha}...HEAD")
            if rl.returncode == 0 and rl.stdout.strip():
                behind, ahead = rl.stdout.split()
                out["behind"], out["ahead"] = int(behind), int(ahead)
        else:
            out["note"] = "stored SHA not found in this repo"
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        out["note"] = out["note"] or "git unavailable"
    return out
