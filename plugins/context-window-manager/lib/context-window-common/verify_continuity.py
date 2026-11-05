# verify_continuity.py - resume-side verification of the non-git handoff
# metadata fields. Consumes the dict produced by
# context_schema.parse_metadata_block and reports, per field, whether the
# stored value still holds. Read-only: no filesystem writes, no git-mutating
# command, no `gh` write. Every probe degrades to a structured note rather
# than raising, so a missing `gh`, an offline network, or a deleted PR never
# crashes resume.
import json
import re
import subprocess
from pathlib import Path

import git_drift
from session_capture import collect_git

# Mirrors git_drift._GIT_TIMEOUT: a hung `gh` call must degrade to a note
# rather than block resume.
_GH_TIMEOUT = 10

# Extracts `owner/repo` from a GitHub PR URL so the re-query can be bound to the
# recorded repository. PR numbers are unique only within a repo, so binding by
# number alone lets a different repo's PR #N masquerade as the recorded one.
_PR_URL_RE = re.compile(r"github\.com/([^/]+/[^/]+)/pull/\d+", re.IGNORECASE)


def _owner_repo(url: "str | None") -> "str | None":
    """Parse `owner/repo` from a recorded GitHub PR url, or None when the url is
    absent or not a recognizable PR url. Used to bind the `gh pr view` query to
    the recorded repository via `--repo`."""
    if not url:
        return None
    m = _PR_URL_RE.search(url)
    return m.group(1) if m else None


def _as_int(value) -> "int | None":
    """Coerce a parse_metadata_block scalar (str or None) to int, or None when
    it is absent or non-numeric. parse maps 'n/a'/missing to None already, so a
    None here means the count was never recorded."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _default_gh_runner(cwd, number, url) -> "dict | None":
    """Re-query the RECORDED PR by number, bound to the RECORDED repository, with
    a read-only `gh pr view <number> --repo owner/repo --json state,number,url`.

    Binding to both the recorded number and the recorded repo is the whole point:
    PR numbers are unique only within a repository, so resuming on a different
    branch must not substitute that branch's PR state, and resuming in a
    different repo that also has PR #<number> must not masquerade as the recorded
    PR. `--repo` is derived from the recorded pr_url; when the url is absent or
    unparseable the query falls back to the unbound form (the caller's url
    equality gate still catches a cross-repo resolution). Returns the parsed JSON
    dict on success, or None when `gh` is missing, the call fails or times out,
    the PR cannot be resolved, or the output is unparseable. Never raises and
    never writes: resume stays read-only and a machine without `gh` is a normal
    case, not an error."""
    argv = ["gh", "pr", "view", str(number)]
    repo = _owner_repo(url)
    if repo:
        argv += ["--repo", repo]
    argv += ["--json", "state,number,url"]
    try:
        r = subprocess.run(
            tuple(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        data = json.loads(r.stdout)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _check_pr(meta: dict, cwd: str, gh_runner) -> dict:
    """Re-query the RECORDED PR's current state as evidence at resume time. The
    handoff stores pr_number / pr_url (no state), so there is no stored baseline
    to diff; this surfaces the live state (e.g. MERGED) of that specific PR and
    leaves comparison to the human reading the report.

    The PR is looked up by its recorded number AND bound to the recorded
    repository (via the recorded pr_url), never by the resume cwd's current
    branch, so resuming on a different branch cannot report a different PR's
    state as if it were the recorded one, and resuming in a different repo that
    also has PR #<number> cannot masquerade as it. If the runner resolves a PR
    whose number differs from the recorded one, OR whose url differs from the
    recorded pr_url (a cross-repo collision the numbers alone would miss), the
    state is withheld and the mismatch is noted rather than trusted. Any `gh`
    failure degrades to a note."""
    number, url = meta.get("pr_number"), meta.get("pr_url")
    if not number:
        return {
            "number": None,
            "url": None,
            "current_state": None,
            "note": "no PR recorded in handoff metadata",
        }
    runner = gh_runner or _default_gh_runner
    try:
        data = runner(cwd, number, url)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        data = None
    if not isinstance(data, dict) or not data.get("state"):
        return {
            "number": number,
            "url": url,
            "current_state": None,
            "note": f"recorded PR #{number} could not be resolved; "
            "current state not re-queried",
        }
    resolved = data.get("number")
    if resolved is not None and str(resolved) != str(number):
        return {
            "number": number,
            "url": url,
            "current_state": None,
            "note": f"resolved PR #{resolved} does not match recorded PR "
            f"#{number}; state not trusted",
        }
    resolved_url = data.get("url")
    if url and resolved_url and resolved_url != url:
        return {
            "number": number,
            "url": url,
            "current_state": None,
            "note": f"resolved PR url {resolved_url} does not match recorded "
            f"{url} (repo mismatch); state not trusted",
        }
    return {
        "number": number,
        "url": url,
        "current_state": data["state"],
        "note": "",
    }


def _path_exists(stored: str, cwd: str) -> bool:
    """Resolve a stored path the same way verify_refs.verify_refs does: a
    `~/...` path is expanded, an absolute path is used as-is, and a relative
    path is taken against `cwd`. Existence only, no reads."""
    raw = Path(stored).expanduser()
    p = raw if raw.is_absolute() else Path(cwd) / stored
    return p.exists()


def _check_paths(meta: dict, cwd: str) -> list:
    """Existence check for every path-valued handoff field, in a stable order:
    the cobtask file, the external TDD plan, then each ADR. Absent scalars
    (None / 'n/a' already mapped to None by parse) and empty ADR entries are
    skipped so the report only carries paths the handoff actually recorded."""
    out = []
    for kind, key in (("cobtask", "cobtask_path"), ("tdd_plan", "tdd_plan_path")):
        stored = meta.get(key)
        if stored:
            out.append(
                {"kind": kind, "path": stored, "exists": _path_exists(stored, cwd)}
            )
    for stored in meta.get("adrs") or []:
        if stored:
            out.append(
                {"kind": "adr", "path": stored, "exists": _path_exists(stored, cwd)}
            )
    return out


def _check_working_tree(meta: dict, cwd: str) -> dict:
    """Compare the stored working-tree state against the current one, recomputed
    by reusing session_capture.collect_git (no duplicate porcelain parsing).
    collect_git renders an unknown state as "" for a non-git dir; that maps to
    a None current. Divergence is only claimed when BOTH stored and current are
    known and differ, so a missing baseline degrades to a note, never a false
    "diverged"."""
    stored = meta.get("working_tree")
    current = collect_git(cwd).working_tree or None
    if not stored:
        return {
            "stored": stored,
            "current": current,
            "diverged": False,
            "note": "no working-tree state recorded in handoff metadata",
        }
    if current is None:
        return {
            "stored": stored,
            "current": None,
            "diverged": False,
            "note": "current working-tree state could not be determined",
        }
    return {
        "stored": stored,
        "current": current,
        "diverged": stored != current,
        "note": "",
    }


def _check_remote(meta: dict, cwd: str) -> dict:
    """Recompute the remote comparison (via git_drift.recompute_remote) and
    compare it to every stored remote field: ahead/behind counts, the upstream
    ref, and the pushed flag. The stored upstream rides into the recompute so a
    branch pushed without `-u` (no local tracking) is still resolved against the
    ref capture recorded, instead of reporting unknown.

    `changed` is True when any comparable pair is known on both sides and
    differs: counts, upstream ref, or push state. Every comparison that cannot
    be made on both sides is simply left out of `changed` (never forced to a
    difference or a false match), and the recompute note is carried through, so
    "could not check" never reads as "unchanged"."""
    stored_ahead = _as_int(meta.get("remote_ahead"))
    stored_behind = _as_int(meta.get("remote_behind"))
    stored_upstream = meta.get("remote_upstream") or None
    stored_pushed = meta.get("remote_pushed") or None
    current = git_drift.recompute_remote(cwd, stored_upstream=stored_upstream)
    cur_ahead, cur_behind = current["ahead"], current["behind"]
    cur_upstream = current["upstream"]
    cur_pushed = current["pushed"] or None
    counts_known = None not in (stored_ahead, stored_behind, cur_ahead, cur_behind)
    counts_changed = counts_known and (
        stored_ahead != cur_ahead or stored_behind != cur_behind
    )
    upstream_changed = bool(
        stored_upstream and cur_upstream and stored_upstream != cur_upstream
    )
    pushed_changed = bool(stored_pushed and cur_pushed and stored_pushed != cur_pushed)
    changed = bool(counts_changed or upstream_changed or pushed_changed)
    return {
        "stored_ahead": stored_ahead,
        "stored_behind": stored_behind,
        "current_ahead": cur_ahead,
        "current_behind": cur_behind,
        "stored_upstream": stored_upstream,
        "current_upstream": cur_upstream,
        "stored_pushed": stored_pushed,
        "current_pushed": cur_pushed,
        "changed": changed,
        "note": current["note"],
    }


def verify_continuity(meta: dict, cwd: str, gh_runner=None) -> dict:
    """Verify each recorded continuity field against the current environment.

    `meta` is the parse_metadata_block dict (scalars are str or None, list
    keys are lists of strings). `cwd` is the resuming session's working
    directory. `gh_runner` is injectable so tests never shell out.
    """
    stored_cwd = meta.get("cwd")
    return {
        "cwd": {
            "stored": stored_cwd,
            "current": cwd,
            "match": stored_cwd == cwd,
        },
        "paths": _check_paths(meta, cwd),
        "pr": _check_pr(meta, cwd, gh_runner),
        "commits": git_drift.verify_commits(meta.get("commits") or [], cwd),
        "working_tree": _check_working_tree(meta, cwd),
        "remote": _check_remote(meta, cwd),
    }
