# session_capture.py - read-only probes that populate the SessionMetadata a
# handoff writes into its CONTEXT-*.md Session Metadata block.
import json
import os
import re
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from context_schema import SessionMetadata

# Mirrors git_drift._GIT_TIMEOUT. A hung git call (credential prompt, broken
# submodule, unresponsive network .git) must degrade to "unknown" rather than
# block the handoff.
_GIT_TIMEOUT = 10

# The commit list rides inside a file whose whole purpose is context economy,
# so a long-lived branch must not dump hundreds of lines into it.
_MAX_COMMITS = 20

# Refs a repo is likely to treat as its integration base, tried in order when
# the remote does not publish its own HEAD.
_BASE_CANDIDATES = ("origin/main", "origin/master", "main", "master")

# Task ids ride through branch names in lowercase (`feat/sct-0033-slug`), so the
# match is case-insensitive and the captured id is upper-cased to match how the
# vault stores it.
_TASK_ID_RE = re.compile(r"\b([A-Za-z]{3})-(\d{4})\b")

# Where task files live inside a cobtask vault. The vault root itself is never
# hardcoded: it comes from the caller or COBTASK_VAULT_ROOT, so this library
# carries no assumption about any particular machine's layout.
_VAULT_ITEMS_DIR = "items"
_VAULT_ROOT_ENV = "COBTASK_VAULT_ROOT"

_TDD_HEADING = "## TDD Plan"


class _Deadline:
    """Wall-clock budget shared across the probes a single collect_git or
    collect_context call issues. `_git` (and the default `gh` runner) skip
    their subprocess call outright once this has passed, instead of each
    waiting out its own _GIT_TIMEOUT: without it, a degraded git installation
    (broken submodule, hung credential prompt) makes every one of the
    dozen-plus independent probes those two functions issue wait out its own
    timeout in sequence, turning a ~10s problem into minutes."""

    __slots__ = ("_expires_at",)

    def __init__(self, budget_seconds: float = _GIT_TIMEOUT):
        self._expires_at = time.monotonic() + budget_seconds

    def expired(self) -> bool:
        return time.monotonic() >= self._expires_at


# Public name so callers in sibling modules (e.g. the PreCompact hook) can
# construct one budget and share it across collect_git, collect_context, and
# context_schema.save_dir, instead of each call spending its own _GIT_TIMEOUT
# in turn.
Deadline = _Deadline


def _git(
    cwd, *args, deadline: "_Deadline | None" = None
) -> "subprocess.CompletedProcess[str]":
    """Run a git command. A missing binary, a timeout, or any OS-level failure
    comes back as returncode 1 with empty output, so every caller can treat
    "the call failed" and "git had nothing to say" the same way. When
    `deadline` has already passed, the subprocess is never started at all:
    this is what keeps a degraded git installation from making every probe
    in the chain wait out its own timeout."""
    if deadline is not None and deadline.expired():
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")
    try:
        return subprocess.run(
            ("git", *args),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")


def _out(cwd, *args, deadline: "_Deadline | None" = None) -> str:
    r = _git(cwd, *args, deadline=deadline)
    return r.stdout.strip() if r.returncode == 0 else ""


def _working_tree(
    cwd, deadline: "_Deadline | None" = None
) -> "tuple[str, int | None, int | None, int | None]":
    """Classify the working copy from a single porcelain call, returning
    (state, staged, unstaged, untracked). State is empty when git could not
    answer, so the caller can render 'n/a' instead of claiming 'clean'; the
    three counts are None in that case too, so a failed probe can never be
    mistaken for a probe that ran and found zero changes."""
    r = _git(cwd, "status", "--porcelain", deadline=deadline)
    if r.returncode != 0:
        return "", None, None, None
    staged = unstaged = untracked = 0
    for line in r.stdout.splitlines():
        code = line[:2]
        if len(code) < 2:
            continue
        if code == "??":
            untracked += 1
            continue
        if code[0] != " ":
            staged += 1
        if code[1] != " ":
            unstaged += 1
    state = "dirty" if (staged or unstaged or untracked) else "clean"
    return state, staged, unstaged, untracked


def _stash_count(cwd, deadline: "_Deadline | None" = None) -> "int | None":
    """Number of stash entries, or None when the probe itself failed. `_out`
    collapses a failed call and a real empty stash list to the same "" value,
    so this reads the CompletedProcess directly to keep the two distinguishable."""
    r = _git(cwd, "stash", "list", deadline=deadline)
    return len(r.stdout.splitlines()) if r.returncode == 0 else None


def _configured_remotes(cwd, deadline: "_Deadline | None" = None) -> "list[str]":
    """Remote names in git's own list order, with 'origin' moved first when
    present: most repos have only one remote, and origin was already the
    preferred fallback, so this keeps that common case identical while still
    checking every other configured remote."""
    remotes = [r for r in _out(cwd, "remote", deadline=deadline).splitlines() if r]
    if "origin" in remotes:
        remotes.remove("origin")
        remotes.insert(0, "origin")
    return remotes


def remote_state(
    cwd, deadline: "_Deadline | None" = None
) -> "tuple[str, int | None, int | None, str]":
    """Upstream name plus ahead/behind counts, as
    (upstream, ahead, behind, pushed). Having no upstream is not proof the
    branch was never pushed: `git push <remote> <branch>` without `-u`, or a
    later `git branch --unset-upstream`, both leave commits on the remote
    with no tracking config locally. Before concluding 'no', fall back to
    the same-named ref on every configured remote, not just `origin`: a
    remote named `upstream`, `fork`, or anything else can hold a genuinely
    pushed branch too. Only when neither the configured upstream nor any
    remote's same-named ref exists is the branch actually unpushed. A failed
    or malformed rev-list probe leaves pushed state unknown (renders 'n/a')
    rather than a false 'no': the probe never established that any commits
    are unpushed."""
    upstream = _out(
        cwd,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        deadline=deadline,
    )
    if not upstream:
        branch = _out(cwd, "branch", "--show-current", deadline=deadline)
        fallback = ""
        if branch:
            for remote in _configured_remotes(cwd, deadline=deadline):
                candidate = f"{remote}/{branch}"
                if (
                    _git(
                        cwd,
                        "rev-parse",
                        "--verify",
                        "--quiet",
                        candidate,
                        deadline=deadline,
                    ).returncode
                    == 0
                ):
                    fallback = candidate
                    break
        if not fallback:
            return "", None, None, "no"
        upstream = fallback
    r = _git(
        cwd,
        "rev-list",
        "--left-right",
        "--count",
        f"{upstream}...HEAD",
        deadline=deadline,
    )
    if r.returncode != 0:
        return upstream, None, None, ""
    parts = r.stdout.split()
    if len(parts) != 2:
        return upstream, None, None, ""
    behind, ahead = int(parts[0]), int(parts[1])
    return upstream, ahead, behind, ("yes" if ahead == 0 else "no")


def _base_ref(cwd, deadline: "_Deadline | None" = None) -> str:
    """The ref this branch most likely diverged from. Prefers whatever the
    remote publishes as its HEAD, then the conventional names."""
    published = _out(
        cwd, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", deadline=deadline
    )
    for candidate in (published, *_BASE_CANDIDATES):
        if (
            candidate
            and _git(
                cwd, "rev-parse", "--verify", "--quiet", candidate, deadline=deadline
            ).returncode
            == 0
        ):
            return candidate
    return ""


def _neutralize_backticks(line: str) -> str:
    """Strip backticks from a commit subject before it enters the handoff.

    A commit subject is attacker-influenceable text (anyone who can push a
    commit controls it), yet it rides into the rendered Session Metadata
    block as plain list prose. Resume's verify_refs.extract_refs scans the
    whole CONTEXT file for backtick-delimited tokens and probes each one
    against the filesystem, including `~/...` paths. Left verbatim, a subject
    like `` `~/.ssh/config:1` `` would be picked up as a "cited reference"
    and read. Backticks cannot survive capture, so no commit subject can ever
    form such a token."""
    return line.replace("`", "'")


def _commits(cwd, deadline: "_Deadline | None" = None) -> "list[str]":
    """Commits this branch added on top of its base, newest first, as
    `<shortsha> <subject>`. When HEAD is itself the base, or no base resolves,
    there is no divergence to report, so fall back to the most recent commits:
    a session spent working directly on the base branch still needs its work
    listed."""
    base = _base_ref(cwd, deadline=deadline)
    if base:
        diverged = _out(
            cwd,
            "log",
            f"-n{_MAX_COMMITS}",
            "--format=%h %s",
            f"{base}..HEAD",
            deadline=deadline,
        )
        if diverged:
            return [_neutralize_backticks(line) for line in diverged.splitlines()]
    return [
        _neutralize_backticks(line)
        for line in _out(
            cwd, "log", f"-n{_MAX_COMMITS}", "--format=%h %s", deadline=deadline
        ).splitlines()
    ]


def _task_id_from_branch(branch: str) -> str:
    m = _TASK_ID_RE.search(branch or "")
    return f"{m.group(1).upper()}-{m.group(2)}" if m else ""


def _resolve_vault_root(vault_root) -> str:
    return str(vault_root) if vault_root else os.environ.get(_VAULT_ROOT_ENV, "")


def _find_task_file(vault_root: str, task_id: str) -> str:
    """Locate `<vault>/items/**/<ID>-*.md`. Searches recursively because a vault
    may route some areas into subdirectories. Returns the first match by sorted
    path so the answer is stable across runs."""
    if not (vault_root and task_id):
        return ""
    items = Path(vault_root) / _VAULT_ITEMS_DIR
    if not items.is_dir():
        return ""
    matches = sorted(items.rglob(f"{task_id}-*.md"))
    return str(matches[0]) if matches else ""


def _has_populated_tdd_plan(task_path: str) -> bool:
    """True when the task file's `## TDD Plan` section holds a real dated plan
    rather than the template placeholder. A plan is stored as one or more
    `### <date>` subsections, so the presence of a subsection is the signal."""
    if not task_path:
        return False
    try:
        text = Path(task_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == _TDD_HEADING)
    except StopIteration:
        return False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            return False
        if stripped.startswith("### "):
            return True
    return False


def _default_gh_runner(cwd, *args) -> "subprocess.CompletedProcess[str]":
    cmd = ("gh", "pr") + (args if args else ("view", "--json", "number,url"))
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )


def _pull_request(
    cwd,
    gh_runner,
    deadline: "_Deadline | None" = None,
    head: str = "",
    head_oid: str = "",
) -> "tuple[str, str]":
    """The branch's PR as (number, url). Having no PR is the normal pre-PR
    state, and a missing `gh` is a normal machine without the CLI, so both
    degrade to empty rather than surfacing an error.

    When the default `gh pr view` probe returns no PR (common in worktrees
    whose local branch name differs from the remote head), a second probe
    with `gh pr list --head <head>` is issued. `head` is the remote branch
    name without the remote prefix (e.g. `feat/my-branch`). The candidate
    must uniquely match `head_oid`; branch-name-only matches are ambiguous
    across forks and reused branch names."""
    runner = gh_runner or _default_gh_runner
    # A repo with no remote cannot have a PR, so skip the `gh` round-trip
    # entirely. Likewise, once the capture deadline has passed, this probe is
    # skipped rather than issued and left to wait out its own timeout. Only
    # the default runner is short-circuited; an injected one is the caller's
    # business and always runs.
    if gh_runner is None and (
        (deadline is not None and deadline.expired())
        or not _out(cwd, "remote", deadline=deadline)
    ):
        return "", ""
    try:
        r = runner(cwd)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return "", ""
    if r.returncode != 0 or not r.stdout.strip():
        if head and not (deadline is not None and deadline.expired()):
            try:
                r = runner(
                    cwd,
                    "list",
                    "--head",
                    head,
                    "--state",
                    "all",
                    "--json",
                    "number,url,headRefOid",
                )
            except (FileNotFoundError, OSError, TypeError, subprocess.SubprocessError):
                return "", ""
        else:
            return "", ""
    if r.returncode != 0 or not r.stdout.strip():
        return "", ""
    try:
        data = json.loads(r.stdout)
    except (ValueError, TypeError):
        return "", ""
    if isinstance(data, list):
        matches = [
            item
            for item in data
            if isinstance(item, dict) and item.get("headRefOid") == head_oid
        ]
        if not head_oid or len(matches) != 1:
            return "", ""
        data = matches[0]
    if not isinstance(data, dict):
        return "", ""
    number, url = data.get("number", ""), data.get("url", "")
    return (str(number) if number else ""), (str(url) if url else "")


def collect_context(
    meta: SessionMetadata,
    *,
    cobtask_id: str = "",
    cobtask_path: str = "",
    tdd_plan_path: str = "",
    adrs=None,
    vault_root=None,
    gh_runner=None,
    deadline: "_Deadline | None" = None,
) -> SessionMetadata:
    """Fill in the session-context half of the metadata: task, PR, plan, ADRs.

    Everything here is optional by design. A session with no task, no PR, no
    external plan, and no ADRs returns a metadata object whose corresponding
    fields stay empty, and the rendered block says 'n/a' for each. Nothing is
    invented to fill a gap.

    `gh_runner` is injectable so tests never shell out to a real `gh`.
    `deadline` bounds this call's own probes the same way collect_git's does;
    a fresh one is created when the caller doesn't supply one.
    """
    if deadline is None:
        deadline = _Deadline()
    task_id = cobtask_id or _task_id_from_branch(meta.git_branch)
    task_path = cobtask_path or _find_task_file(
        _resolve_vault_root(vault_root), task_id
    )
    # The plan link exists to point at a plan that is NOT already in the task
    # file; once the task holds it, a second pointer is noise.
    external_plan = "" if _has_populated_tdd_plan(task_path) else tdd_plan_path
    head = ""
    if meta.remote_upstream and "/" in meta.remote_upstream:
        head = meta.remote_upstream.split("/", 1)[1]
    elif meta.git_branch:
        head = meta.git_branch
    head_ref = meta.remote_upstream or meta.git_branch
    head_oid = (
        _out(meta.cwd, "rev-parse", head_ref, deadline=deadline)
        if meta.cwd and head_ref
        else ""
    )
    pr_number, pr_url = _pull_request(
        meta.cwd or None,
        gh_runner,
        deadline=deadline,
        head=head,
        head_oid=head_oid,
    )
    return replace(
        meta,
        cobtask_id=task_id,
        cobtask_path=task_path,
        tdd_plan_path=external_plan,
        pr_number=pr_number,
        pr_url=pr_url,
        adrs=list(adrs or []),
    )


def collect_git(cwd, *, deadline: "_Deadline | None" = None) -> SessionMetadata:
    """Everything about the working copy at `cwd` that a handoff records.

    Also carries `cwd` itself, since it is the anchor every other probe reads
    relative to and the value a resuming session asserts against. Outside a git
    repo every git-derived field stays at its default, so the rendered block
    reads 'n/a' rather than inventing a value.

    `deadline` bounds the total time every probe below may take combined; a
    fresh one is created when the caller doesn't supply one, so this always
    degrades within roughly _GIT_TIMEOUT seconds even when git itself is
    hanging, rather than each probe waiting out its own timeout in turn.
    """
    if deadline is None:
        deadline = _Deadline()
    meta = SessionMetadata(cwd=str(Path(cwd).resolve()))
    if (
        _git(cwd, "rev-parse", "--is-inside-work-tree", deadline=deadline).returncode
        != 0
    ):
        return meta
    state, staged, unstaged, untracked = _working_tree(cwd, deadline=deadline)
    upstream, ahead, behind, pushed = remote_state(cwd, deadline=deadline)
    return replace(
        meta,
        git_sha=_out(cwd, "rev-parse", "HEAD", deadline=deadline),
        git_sha_short=_out(cwd, "rev-parse", "--short", "HEAD", deadline=deadline),
        git_branch=_out(cwd, "branch", "--show-current", deadline=deadline),
        working_tree=state,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        stash_count=_stash_count(cwd, deadline=deadline),
        remote_upstream=upstream,
        remote_ahead=ahead,
        remote_behind=behind,
        remote_pushed=pushed,
        commits=_commits(cwd, deadline=deadline),
    )
