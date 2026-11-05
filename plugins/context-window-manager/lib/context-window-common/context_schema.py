# context_schema.py - schema constants and save-path helpers for the
# CONTEXT-*.md handoff file.
import hashlib
import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CONTEXT_GLOB = "CONTEXT-*.md"

# Cap on the single git call in repo_slug so a hung invocation (credential
# prompt, broken submodule, unresponsive network .git) degrades to the cwd
# fallback instead of blocking. Mirrors git_drift._GIT_TIMEOUT.
_GIT_TIMEOUT = 10

# Home-scoped, per-repo-namespaced save root (plugin .sct storage convention):
# CONTEXT files live at SAVE_ROOT/<repo-slug>/CONTEXT-*.md, replacing the old
# project-local save path. CWM_SAVE_ROOT overrides the root (used
# by tests to redirect writes away from the real home dir; also a manual escape
# hatch), otherwise it is home-scoped.
SAVE_ROOT = Path(
    os.environ.get("CWM_SAVE_ROOT")
    or (Path.home() / ".config" / "sct" / "context-window-manager")
)

META_HEADING = "### Session Metadata"

# Free-text scalars. An empty value renders as the literal 'n/a' so parse
# round-trips and "we looked and there was nothing" stays distinguishable from
# a truncated block.
_META_STR_KEYS = (
    "git_sha",
    "git_sha_short",
    "git_branch",
    "cwd",
    "cobtask_id",
    "cobtask_path",
    "tdd_plan_path",
    "pr_number",
    "pr_url",
    "working_tree",
    "remote_upstream",
    "remote_pushed",
)
# Every count-valued key is optional. A probe that never ran (no upstream for
# ahead/behind) or that failed (a git status/stash call that errored or timed
# out) leaves the count unknown, rendered as an explicit 'n/a'. A probe that
# succeeded and found nothing renders a bare 0, so 'n/a' and '0' stay
# distinguishable: "we don't know" versus "we checked and there wasn't any."
_META_OPT_COUNT_KEYS = (
    "staged",
    "unstaged",
    "untracked",
    "stash_count",
    "remote_ahead",
    "remote_behind",
)
# List-valued keys render as a `- key:` header followed by indented `  - item`
# lines.
_META_LIST_KEYS = ("commits", "adrs")

_META_SCALAR_KEYS = _META_STR_KEYS + _META_OPT_COUNT_KEYS

# Render order, kept separate from the kind groupings above so related fields
# (git, cobtask, PR, working tree, remote) stay adjacent in the written block.
_META_ORDER = (
    "git_sha",
    "git_sha_short",
    "git_branch",
    "cwd",
    "cobtask_id",
    "cobtask_path",
    "tdd_plan_path",
    "pr_number",
    "pr_url",
    "working_tree",
    "staged",
    "unstaged",
    "untracked",
    "stash_count",
    "remote_upstream",
    "remote_ahead",
    "remote_behind",
    "remote_pushed",
    "commits",
    "adrs",
)

# Matches any `- key: value` line. The key is validated against the known key
# sets after the match, so a `- foo: bar` prose line inside the block is ignored
# rather than inventing a key.
_META_LINE_RE = re.compile(r"^-\s+([a-z_]+):\s*(.*)$")


@dataclass
class SessionMetadata:
    """Every value the handoff records in the `### Session Metadata` block.

    Defaults are the "nothing known" case for each kind, so a caller outside a
    git repo (or with no cobtask, PR, or upstream) can construct the dataclass
    with only the fields it resolved and still render a complete, well-formed
    block.
    """

    git_sha: str = ""
    git_sha_short: str = ""
    git_branch: str = ""
    cwd: str = ""
    cobtask_id: str = ""
    cobtask_path: str = ""
    tdd_plan_path: str = ""
    pr_number: str = ""
    pr_url: str = ""
    working_tree: str = ""
    staged: "int | None" = None
    unstaged: "int | None" = None
    untracked: "int | None" = None
    stash_count: "int | None" = None
    remote_upstream: str = ""
    remote_ahead: "int | None" = None
    remote_behind: "int | None" = None
    remote_pushed: str = ""
    commits: "list[str]" = field(default_factory=list)
    adrs: "list[str]" = field(default_factory=list)


def _sanitize_meta_value(value: str) -> str:
    """Neutralize characters a captured value could use to smuggle its own
    Markdown line(s) into the Session Metadata block, or to form a
    `` `path` `` token for resume's reference scanner. Every scalar and list
    value here can carry caller-supplied or git-derived text this module
    does not control (commit subjects, branch-derived ids, CLI-supplied
    paths), so every value renders through this instead of trusting
    producers to sanitize case by case. Joins on str.splitlines() rather than
    replacing literal \\r/\\n so every break char parse_metadata_block's own
    text.splitlines() would treat as a line boundary (U+2028, U+2029, \\v,
    \\f, ...) is neutralized too, not just \\r/\\n."""
    return " ".join(value.splitlines()).replace("`", "'")


def render_metadata_block(meta: SessionMetadata) -> str:
    """Render the Session Metadata markdown block. Empty values render as
    the literal 'n/a' so parse round-trips and the non-git case is explicit."""

    def v(x: str) -> str:
        sanitized = _sanitize_meta_value(x).strip()
        return sanitized if sanitized else "n/a"

    lines = [META_HEADING, ""]
    for key in _META_ORDER:
        value = getattr(meta, key)
        if key in _META_LIST_KEYS:
            lines.append(f"- {key}:")
            lines.extend(f"  - {_sanitize_meta_value(item)}" for item in value)
        elif key in _META_OPT_COUNT_KEYS:
            lines.append(f"- {key}: {'n/a' if value is None else int(value)}")
        else:
            lines.append(f"- {key}: {v(value)}")
    return "\n".join(lines) + "\n"


def parse_metadata_block(text: str) -> dict:
    """Read a rendered Session Metadata block back out of a CONTEXT file body.

    Only the lines under the META_HEADING (up to the next markdown heading) are
    scanned, so a decoy `- git_sha:`-shaped line elsewhere in the file cannot
    shadow the real block. If META_HEADING is absent, every scalar key maps to
    None and every list key to []. Scalars come back as strings regardless of
    how they were rendered (the file is text); 'n/a' and missing keys map to
    None, so a consumer that wants a number coerces it itself."""
    out: dict = {k: None for k in _META_SCALAR_KEYS}
    out.update({k: [] for k in _META_LIST_KEYS})
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == META_HEADING:
            start = i + 1
            break
    if start is None:
        return out
    current_list_key = None
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        indented = line[:1].isspace()
        # An indented `  - item` line continues the list opened by the most
        # recent `- key:` header. Anything else, including a blank line,
        # closes the list, so a stray indented bullet sitting in prose cannot
        # be absorbed into commits or adrs.
        if current_list_key and indented and stripped.startswith("- "):
            out[current_list_key].append(stripped[2:].strip())
            continue
        current_list_key = None
        # Only flush-left lines may set a scalar; an indented `- cobtask_id: x`
        # is decoration inside someone's prose, not the block's own value.
        if indented or not stripped:
            continue
        m = _META_LINE_RE.match(stripped)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key in _META_LIST_KEYS:
            current_list_key = key
        elif key in _META_SCALAR_KEYS:
            out[key] = None if val in ("", "n/a") else val
    return out


def context_filename(now: "datetime | None" = None) -> str:
    """Generate a collision-resistant CONTEXT-*.md filename: a
    second-resolution timestamp plus a random 6-hex-char suffix, so two
    parallel sessions handing off within the same second never silently
    overwrite each other's file (both remain on disk for resume to surface)."""
    ts = (now or datetime.now()).strftime("%Y-%m-%d-%H-%M-%S")
    return f"CONTEXT-{ts}-{secrets.token_hex(3)}.md"


def repo_slug(cwd: Path, deadline=None) -> str:
    """Namespace segment for the current repo: `<basename>-<hash>`, where the
    base is the git toplevel when `cwd` is inside a repo (else `cwd` itself) and
    the hash is the first 8 hex chars of the sha256 of that root's absolute path.
    Basename alone is NOT collision-free: two repos sharing a directory name
    (e.g. `~/work/foo` and `~/personal/foo`, or two clones) would land in the
    same subdir and intermix their CONTEXT-*.md files. The path-derived suffix
    keeps such repos in distinct subdirs so resume never serves a handoff from
    the wrong repo.

    `deadline` (any object with an `.expired()` method, e.g.
    `session_capture.Deadline`) lets a caller that already spent its budget on
    earlier probes skip this git call outright rather than wait out another
    `_GIT_TIMEOUT`; when the budget is gone the cwd fallback is used. Callers
    that pass nothing keep the original always-probe behavior."""
    if deadline is not None and deadline.expired():
        base = Path(cwd)
    else:
        try:
            r = subprocess.run(
                ("git", "-C", str(cwd), "rev-parse", "--show-toplevel"),
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT,
            )
            base = (
                Path(r.stdout.strip())
                if r.returncode == 0 and r.stdout.strip()
                else Path(cwd)
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            base = Path(cwd)
    root = base.resolve()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
    return f"{root.name}-{digest}"


def worktree_toplevels(cwd: Path) -> "list[Path]":
    """Absolute, resolved toplevel paths of every git worktree linked to
    `cwd`'s repo, parsed from `git worktree list --porcelain` (primary first,
    git's order preserved). Returns [] when `cwd` is not inside a git repo or
    git is unavailable, mirroring repo_slug's degrade-to-local behavior so the
    caller never has to guard git absence."""
    try:
        r = subprocess.run(
            ("git", "-C", str(cwd), "worktree", "list", "--porcelain"),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    tops = []
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            tops.append(Path(line[len("worktree ") :]).resolve())
    return tops


def save_dir(cwd: Path, create: bool = False, deadline=None) -> Path:
    """Resolve the per-repo handoff directory: SAVE_ROOT/<repo-slug>. When
    `create` is True, make the directory (and parents) first and restrict the
    per-repo directory to its owner, so a handoff can write into it safely.

    `deadline` is forwarded to `repo_slug` so a latency-sensitive caller (the
    PreCompact hook) can bound the slug's git probe with the same budget it
    shared across its earlier captures."""
    d = SAVE_ROOT / repo_slug(cwd, deadline=deadline)
    if create:
        d.mkdir(parents=True, mode=0o700, exist_ok=True)
        d.chmod(0o700)
    return d
