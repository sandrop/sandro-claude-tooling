#!/usr/bin/env python3
"""PreCompact failsafe hook for context-window-manager.

Runs at the compaction boundary (autocompaction or `/compact`) before any
manual `/context-window-manager:handoff` has a chance to fire. Writes a deterministic
failsafe CONTEXT-*.md so the session is still resumable. A hook cannot run the
model, so the narrative sections are an honest auto-capture stub; the machine
half (the Session Metadata block) is rendered through the same shared helper
the handoff path uses, so resume parses it identically.

The hook must never fail or block compaction: every step is wrapped, and it
always emits {"continue": true} and exits 0.
"""

import json
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parent.parent / "lib" / "context-window-common"
if str(COMMON) not in sys.path:
    sys.path.insert(0, str(COMMON))


def _emit(cont=True):
    print(json.dumps({"continue": cont, "suppressOutput": True}))


# A manual handoff (or a failsafe already written by an earlier compaction this
# session) counts as "recent" within this window. Mirrors resume's own
# concurrent-handoff window so the failsafe never shadows a fresh manual handoff
# that resume would otherwise offer as a candidate.
_RECENT_HANDOFF_WINDOW_SECONDS = 30 * 60


def _recent_handoff_exists(save_directory, context_schema):
    """True if `save_directory` already holds a CONTEXT-*.md touched within the
    recent window. A hook cannot cheaply tell a rich manual handoff from a prior
    deterministic stub, but skipping when either is recent is safe: a recent
    manual handoff is richer and must not be shadowed, and a recent stub is
    already a valid resumable floor."""
    import time

    cutoff = time.time() - _RECENT_HANDOFF_WINDOW_SECONDS
    for f in save_directory.glob(context_schema.CONTEXT_GLOB):
        try:
            if f.stat().st_mtime >= cutoff:
                return True
        except OSError:
            continue
    return False


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001 - malformed stdin must never block compaction
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        import context_schema
        import session_capture

        cwd = payload.get("cwd") or os.getcwd()
        trigger = payload.get("trigger", "unknown")
        # One wall-clock budget shared across every probe below, so a degraded
        # git environment degrades within roughly one _GIT_TIMEOUT total instead
        # of collect_git, collect_context, and save_dir each spending their own
        # in turn. The PreCompact hook runs synchronously ahead of compaction,
        # so bounded latency is part of the "never block compaction" contract.
        deadline = session_capture.Deadline()
        # Resolve the canonical save directory FIRST, while the shared budget is
        # still fresh. If a slow capture below exhausted the deadline before this
        # ran, repo_slug would skip its git-toplevel probe and hash the payload
        # cwd instead; when cwd is a repo subdirectory that yields a different
        # slug than a later unbounded resume resolves, writing the failsafe where
        # resume never looks.
        save_directory = context_schema.save_dir(
            Path(cwd), create=True, deadline=deadline
        )
        # Do not shadow a recent manual handoff (or an earlier failsafe): resume
        # defaults to the newest file, so writing another stub on top of a fresh
        # model-authored handoff would demote the richer snapshot.
        if _recent_handoff_exists(save_directory, context_schema):
            _emit(True)
            return
        meta = session_capture.collect_context(
            session_capture.collect_git(cwd, deadline=deadline),
            deadline=deadline,
        )
        block = context_schema.render_metadata_block(meta)
        target = save_directory / context_schema.context_filename()
        summary = (
            f"Auto-captured at the PreCompact boundary (trigger: {trigger}). "
            "Deterministic failsafe snapshot; narrative not authored by the model. "
            "The Session Metadata block below reflects git/task state at the "
            "compaction boundary."
        )
        none = "None (failsafe auto-capture)."
        body = (
            "# Resume Command\n\n/context-window-manager:resume\n\n"
            f"## Session Summary\n\n{summary}\n\n"
            f"## Completed Work\n\n{none}\n\n"
            f"## Work In Progress\n\n{none}\n\n"
            f"## Next Steps\n\n{none}\n\n"
            f"## Key Context\n\n{none}\n\n"
            f"## Todo List State\n\n{none}\n\n" + block
        )
        # Publish atomically: write a dot-prefixed temp file (never a
        # CONTEXT-*.md candidate) and os.replace it into place, so a partial
        # write from disk exhaustion or a killed process can never leave a
        # truncated newest file that resume would select over a valid handoff.
        tmp_target = target.with_name("." + target.name + ".tmp")
        try:
            tmp_target.write_text(body, encoding="utf-8")
            os.replace(tmp_target, target)
        except Exception:
            try:
                tmp_target.unlink()
            except OSError:
                pass
            raise
    except Exception as exc:  # noqa: BLE001 - see comment below
        # Never fail compaction, but do not vanish either: a non-blocking
        # stderr diagnostic (type name only, no message, so no path or secret
        # leaks) records that the failsafe snapshot could not be written.
        print(
            f"precompact_handoff: snapshot not written: {type(exc).__name__}",
            file=sys.stderr,
        )
    _emit(True)


if __name__ == "__main__":
    main()
