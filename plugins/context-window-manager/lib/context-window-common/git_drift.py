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
                    cwd,
                    "rev-parse",
                    "--verify",
                    "--quiet",
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
            rl = _git(
                cwd, "rev-list", "--left-right", "--count", f"{stored_sha}...HEAD"
            )
            if rl.returncode == 0 and rl.stdout.strip():
                behind, ahead = rl.stdout.split()
                out["behind"], out["ahead"] = int(behind), int(ahead)
        else:
            out["note"] = "stored SHA not found in this repo"
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        out["note"] = out["note"] or "git unavailable"
    return out


def verify_commits(commits: "list[str]", cwd: str) -> "list[dict]":
    """Check whether each stored commit is still resolvable in the repo at
    `cwd`. Each entry rides as `<shortsha> <subject>` (see
    session_capture._commits), so the leading token is split off as the sha
    before probing with `git cat-file -e`; a rebase or force-push that dropped
    the commit shows up as resolvable False. A non-hex leading token, a non-git
    dir, or any git failure yields resolvable False rather than raising."""
    out: "list[dict]" = []
    for entry in commits:
        parts = entry.split(maxsplit=1)
        sha = parts[0] if parts else ""
        subject = parts[1] if len(parts) > 1 else ""
        resolvable = False
        if sha and _HEX_RE.match(sha):
            try:
                resolvable = _git(cwd, "cat-file", "-e", sha).returncode == 0
            except (FileNotFoundError, OSError, subprocess.SubprocessError):
                resolvable = False
        out.append({"sha": sha, "subject": subject, "resolvable": resolvable})
    return out


def recompute_remote(cwd: str, stored_upstream: "str | None" = None) -> dict:
    """Recompute the remote comparison at resume time, using the SAME prober
    capture used (session_capture.remote_state) so resume and capture cannot
    diverge. remote_state resolves the upstream via `@{upstream}` and, failing
    that, the same-named ref on any configured remote (so a branch pushed
    without `-u` is still seen as pushed), then returns ahead/behind and a
    pushed flag.

    `stored_upstream` is the ref the handoff recorded. It is a last resort:
    used only when the live prober finds no upstream (e.g. the remote branch
    was pushed under a different name) and the stored ref still exists. A
    missing upstream, a non-git dir, or any git failure degrades to None
    counts with a note, never a false 0: "could not check" stays distinct from
    "checked, even." Returns upstream (resolved ref or None), ahead, behind,
    pushed ('yes'/'no'/'' when unknown), and note."""
    # Deferred import: session_capture imports only context_schema, so there is
    # no import cycle, but keeping it function-local avoids loading it for the
    # drift-only callers of this module.
    import session_capture

    out = {"ahead": None, "behind": None, "upstream": None, "pushed": "", "note": ""}
    try:
        inside = _git(cwd, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0:
            out["note"] = "not a git repo"
            return out
        upstream, ahead, behind, pushed = session_capture.remote_state(cwd)
        if not upstream and stored_upstream:
            # The live prober found nothing; fall back to the recorded ref if it
            # still resolves, and recompute against it.
            exists = (
                _git(
                    cwd, "rev-parse", "--verify", "--quiet", stored_upstream
                ).returncode
                == 0
            )
            if exists:
                rl = _git(
                    cwd,
                    "rev-list",
                    "--left-right",
                    "--count",
                    f"{stored_upstream}...HEAD",
                )
                parts = rl.stdout.split()
                if rl.returncode == 0 and len(parts) == 2:
                    upstream = stored_upstream
                    behind, ahead = int(parts[0]), int(parts[1])
                    pushed = "yes" if ahead == 0 else "no"
        out["upstream"] = upstream or None
        out["ahead"], out["behind"] = ahead, behind
        out["pushed"] = pushed or ""
        if out["ahead"] is None:
            out["note"] = (
                "no upstream configured for current branch"
                if not upstream
                else "ahead/behind not computable"
            )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        out["note"] = out["note"] or "git unavailable"
    return out
