#!/usr/bin/env python3
# bootstrap_cwd.py - resolve+verify the recorded code CWD of an explicit
# handoff BEFORE the resume Step 1 validator runs. Data-only: the
# handoff and its recorded cwd are untrusted values, never shell syntax. The
# caller binds the resolved `cwd` via command substitution and cd's to it, so
# this never emits anything but a single JSON envelope on stdout (exit 0
# always; failures ride in `errors`, matching validator.py's ok/errors
# convention).
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parent.parent.parent / "lib" / "context-window-common"
sys.path.insert(0, str(COMMON))
import context_schema

_GIT_TIMEOUT = 10


def _sanitize(s: str) -> str:
    """Escape control characters before an untrusted handoff string is placed
    into a warning or error the caller prints verbatim, so a crafted handoff
    cannot inject terminal escape sequences. Printable characters (including
    spaces and non-ASCII letters) pass through unchanged."""
    out = []
    for ch in s:
        if ch.isprintable():
            out.append(ch)
        elif ord(ch) < 0x100:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(f"\\u{ord(ch):04x}")
    return "".join(out)


def _is_git_worktree(path: Path) -> bool:
    try:
        r = subprocess.run(
            ("git", "-C", str(path), "rev-parse", "--show-toplevel"),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _verify_recorded(recorded: str) -> "tuple[Path | None, str | None]":
    """Strictly verify the recorded code CWD: absolute, canonical (resolved),
    an accessible directory, and a git working tree. Any failure returns a
    single error carrying the exact recorded value (fail loud, AC4)."""
    err = (
        "recorded code working directory is not an accessible git working tree: "
        f"{_sanitize(recorded)}"
    )
    try:
        p = Path(recorded).expanduser()
        if not p.is_absolute():
            return None, err
        resolved = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, err
    if (
        not resolved.is_dir()
        or not os.access(resolved, os.X_OK)
        or not _is_git_worktree(resolved)
    ):
        return None, err
    return resolved, None


# A `Working directory (code):` prose line the model may author in the handoff
# body, optionally as a `- ` bullet and optionally wrapping the path in
# backticks. It is advisory only (resolved pre-start decision Q1): Session
# Metadata.cwd stays authoritative; a divergence is warned, never fatal.
_PROSE_CWD_RE = re.compile(
    r"^\s*-?\s*Working directory \(code\):\s*`?([^`\n]+?)`?\s*$",
    re.MULTILINE,
)


def _warn_on_prose_divergence(text: str, recorded: str, out: dict) -> None:
    m = _PROSE_CWD_RE.search(text)
    if not m:
        return
    prose = m.group(1).strip()
    if prose and prose != recorded:
        out["warnings"].append(
            f"prose 'Working directory (code)' ({_sanitize(prose)}) diverges from "
            f"Session Metadata.cwd ({_sanitize(recorded)}); using Session Metadata.cwd"
        )


def main(argv=None):
    # Build the JSON envelope FIRST and keep argparse inside the try, so a
    # missing or malformed argument rides in `errors` with exit 0 like every
    # other failure, never a bare argparse SystemExit to stderr (the docstring
    # contract: a single JSON envelope on stdout, exit 0 always).
    out = {
        "ok": True,
        "bootstrap": False,
        "cwd": None,
        "recorded": None,
        "warnings": [],
        "errors": [],
    }
    ap = argparse.ArgumentParser()
    ap.add_argument("--context-file", dest="context_file")
    try:
        args = ap.parse_args(argv)
        if not args.context_file:
            out.update(
                ok=False,
                errors=["--context-file is required (a bare CONTEXT-*.md path)"],
            )
        elif not (
            # Match the validator's filename contract exactly (any CONTEXT-*.md
            # via CONTEXT_GLOB, not only timestamp-shaped names), so an explicit
            # handoff the validator would accept is never rejected here first.
            Path(Path(args.context_file).expanduser().name).match(
                context_schema.CONTEXT_GLOB
            )
            and Path(args.context_file).expanduser().is_file()
        ):
            out.update(
                ok=False,
                errors=[
                    (
                        "context file must be an existing CONTEXT-*.md path: "
                        f"{_sanitize(args.context_file)}"
                    )
                ],
            )
        else:
            target = Path(args.context_file).expanduser()
            # Decode strictly: a corrupted byte in the handoff must fail loud
            # via the broad handler below, never be silently stripped into a
            # different (possibly valid-looking) recorded CWD.
            text = target.read_text(encoding="utf-8")
            meta = context_schema.parse_metadata_block(text)
            recorded = meta.get("cwd")
            out["recorded"] = recorded
            if not recorded:
                # Legacy handoff with no recorded code cwd: bootstrap is a
                # no-op signal and the caller keeps current same-repo behavior.
                pass
            else:
                resolved, err = _verify_recorded(recorded)
                if err:
                    out.update(ok=False, errors=[err])
                else:
                    _warn_on_prose_divergence(text, recorded, out)
                    out.update(bootstrap=True, cwd=str(resolved))
    except SystemExit as e:  # argparse failure (unknown/malformed argument)
        out.update(ok=False, errors=[f"invalid arguments (argparse exit {e.code})"])
    except Exception as e:  # noqa: BLE001 - surface any failure via errors/ok
        out.update(ok=False, errors=[f"{type(e).__name__}: {e}"])
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
