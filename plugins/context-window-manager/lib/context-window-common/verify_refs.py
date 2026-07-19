# verify_refs.py
import re
from pathlib import Path

_TOKEN_RE = re.compile(r"`([^`\s]+?)(?::(\d+))?`")


def _looks_like_path(tok: str) -> bool:
    # Require a real path separator. This deliberately drops bare filenames
    # (`foo.py`) to avoid false positives on prose that merely looks dotted:
    # dotted attributes (`obj.map`), version strings (`3.14`), and library
    # names (`Node.js`) all lack a slash. Your handoff convention cites
    # paths with a directory (`src/app.py:42`), so requiring `/` keeps real
    # refs while cutting the noise that matters most for the stale-ref report.
    if "/" not in tok:
        return False
    segs = tok.split("/")
    # A real cited path has a file extension in its last segment, or three or
    # more segments, or at least one segment of 4+ chars. This keeps
    # `src/app.py`, `path/to/file`, and `docs/design-notes` while dropping short
    # prose pairs like `n/a` and `and/or`.
    has_ext = "." in segs[-1]
    return has_ext or len(segs) >= 3 or any(len(seg) >= 4 for seg in segs)


def extract_refs(text: str) -> list:
    refs, seen = [], set()
    for m in _TOKEN_RE.finditer(text):
        tok, line = m.group(1), m.group(2)
        if not _looks_like_path(tok) or tok in seen:
            continue
        seen.add(tok)
        refs.append(
            {"raw": m.group(0), "path": tok, "line": int(line) if line else None}
        )
    return refs


def verify_refs(refs: list, cwd: str) -> list:
    out = []
    cwd_resolved = Path(cwd).resolve()
    home_resolved = Path.home().resolve()
    for r in refs:
        # A cited token may be a repo-relative path, an absolute path, or a
        # `~/...` path; expand and resolve all three so real cited paths are
        # not falsely flagged stale.
        raw = Path(r["path"]).expanduser()
        p = raw if raw.is_absolute() else Path(cwd) / r["path"]
        exists = p.exists()
        line_ok = None
        if exists and r["line"] is not None and p.is_file():
            # Only read files inside cwd or inside the user's home directory;
            # an untrusted CONTEXT file must not cause arbitrary out-of-tree
            # reads. Paths outside both leave line_ok as None.
            resolved = p.resolve()
            if resolved.is_relative_to(cwd_resolved) or resolved.is_relative_to(
                home_resolved
            ):
                try:
                    n = len(
                        p.read_text(encoding="utf-8", errors="ignore").splitlines()
                    )
                    line_ok = r["line"] <= n
                except OSError:
                    line_ok = None
        out.append({**r, "exists": exists, "line_ok": line_ok})
    return out
