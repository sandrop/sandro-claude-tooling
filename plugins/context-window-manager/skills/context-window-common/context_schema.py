# context_schema.py - schema constants and save-path helpers for the
# CONTEXT-*.md handoff file.
import hashlib
import os
import re
import subprocess
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
    or (Path.home() / ".claude" / ".sct" / "context-window-manager")
)

META_HEADING = "### Session Metadata"
_META_KEYS = ("git_sha", "git_sha_short", "git_branch")
_META_LINE_RE = re.compile(r"^-\s+(git_sha|git_sha_short|git_branch):\s*(.*)$")


def render_metadata_block(sha: str, sha_short: str, branch: str) -> str:
    """Render the Session Metadata markdown block. Empty values render as
    the literal 'n/a' so parse round-trips and the non-git case is explicit."""

    def v(x: str) -> str:
        return x.strip() if x and x.strip() else "n/a"

    return (
        f"{META_HEADING}\n\n"
        f"- git_sha: {v(sha)}\n"
        f"- git_sha_short: {v(sha_short)}\n"
        f"- git_branch: {v(branch)}\n"
    )


def parse_metadata_block(text: str) -> dict:
    """Extract {git_sha, git_sha_short, git_branch} from a CONTEXT file body.
    Only the lines under the META_HEADING (up to the next markdown heading) are
    scanned, so a decoy `- git_sha:`-shaped line elsewhere in the file cannot
    shadow the real block. If META_HEADING is absent, all keys map to None.
    Missing keys map to None. 'n/a' values map to None."""
    out: dict[str, str | None] = {k: None for k in _META_KEYS}
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == META_HEADING:
            start = i + 1
            break
    if start is None:
        return out
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        m = _META_LINE_RE.match(stripped)
        if m:
            val = m.group(2).strip()
            out[m.group(1)] = None if val in ("", "n/a") else val
    return out


def repo_slug(cwd: Path) -> str:
    """Namespace segment for the current repo: `<basename>-<hash>`, where the
    base is the git toplevel when `cwd` is inside a repo (else `cwd` itself) and
    the hash is the first 8 hex chars of the sha256 of that root's absolute path.
    Basename alone is NOT collision-free: two repos sharing a directory name
    (e.g. `~/work/foo` and `~/personal/foo`, or two clones) would land in the
    same subdir and intermix their CONTEXT-*.md files. The path-derived suffix
    keeps such repos in distinct subdirs so resume never serves a handoff from
    the wrong repo."""
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


def save_dir(cwd: Path, create: bool = False) -> Path:
    """Resolve the per-repo handoff directory: SAVE_ROOT/<repo-slug>. When
    `create` is True, make the directory (and parents) first, so a handoff can
    write into it unconditionally."""
    d = SAVE_ROOT / repo_slug(cwd)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d
