#!/usr/bin/env python3
# validator.py - handoff pre-flight.
import argparse
import json
import subprocess
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parent.parent.parent / "lib" / "context-window-common"
sys.path.insert(0, str(COMMON))


# Cap the pre-flight git calls so a hung invocation (credential prompt, broken
# submodule, unresponsive network .git) or a missing git binary degrades to a
# structured "not a git repo" result instead of hanging the whole pre-flight.
_GIT_TIMEOUT = 10


def _git(*args):
    try:
        return subprocess.run(
            ("git", *args),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")


def _parse_args(argv):
    p = argparse.ArgumentParser(description="handoff pre-flight")
    # Values only the live session knows. Nothing here is probed for, because
    # guessing them would put fabricated state into the handoff.
    p.add_argument("--cobtask-id", default="")
    p.add_argument("--cobtask-path", default="")
    p.add_argument("--tdd-plan-path", default="")
    p.add_argument("--adrs", default="", help="comma-separated ADR paths")
    p.add_argument("--vault-root", default="")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    checks, errors = {}, []
    metadata_block = ""
    try:
        import context_schema
        import session_capture

        checks["common_import"] = "ok"
        # Create the home-scoped, per-repo save dir securely before the skill
        # writes the handoff, and report the resulting target path.
        _save_dir = context_schema.save_dir(Path.cwd(), create=True)
        checks["save_dir"] = str(_save_dir)
        # Opportunistic once/day retention prune: archive CONTEXT-*.md handoffs
        # older than N days into the terminal zArchives/ sibling. Runs before
        # this session's own file is written, so the fresh handoff is never a
        # candidate. A config error is surfaced loudly as a check value; a prune
        # must never abort a handoff, so any failure is captured, not raised.
        import datetime as _dt

        import retention

        try:
            checks["retention"] = retention.maybe_prune(
                context_schema.SAVE_ROOT,
                today=_dt.date.today(),  # noqa: DTZ011 - naive local calendar day; marker is a local-date string
                keep_dir=_save_dir,  # never relocate this session's own save dir
            )
        except retention.RetentionConfigError as exc:
            checks["retention_error"] = str(exc)  # loud, visible, non-silent
        except Exception as exc:  # noqa: BLE001 - prune must never break a handoff
            checks["retention_error"] = f"{type(exc).__name__}: {exc}"
        # Generate the output filename once here (timestamp + random suffix)
        # so two parallel sessions handing off within the same second never
        # collide on one path; the skill must use this value, not a fresh
        # `date` call, when it writes the file.
        checks["filename"] = context_schema.context_filename()
        # Resolve the whole Session Metadata block here and hand the skill the
        # finished text. Rendering in one place is what keeps the written block
        # in lockstep with the resume-time parser.
        meta = session_capture.collect_context(
            session_capture.collect_git(str(Path.cwd())),
            cobtask_id=args.cobtask_id,
            cobtask_path=args.cobtask_path,
            tdd_plan_path=args.tdd_plan_path,
            adrs=[a.strip() for a in args.adrs.split(",") if a.strip()],
            vault_root=args.vault_root or None,
        )
        metadata_block = context_schema.render_metadata_block(meta)
        # Resolve the finished absolute handoff path once here, so the resume
        # paste line resume prints reads a deterministic value instead of
        # reassembling save_dir + filename at write time.
        _resume_abspath = str(_save_dir / checks["filename"])
        checks["resume_abspath"] = _resume_abspath
        # The single copy-pasteable resume line: the slash command selecting the
        # handoff explicitly via --context-file. The explicit flag makes resume
        # bootstrap this exact file (and restore the recorded cwd from it) instead
        # of falling to its no-argument newest-handoff discovery path. No cd prefix:
        # resume's own bootstrap performs the real directory restore from the
        # context file, so a cd here only duplicated that and added noise.
        checks["resume_command"] = (
            f"/context-window-manager:resume --context-file {_resume_abspath}"
        )
        # A short human-readable digest of the same capture, so the skill can
        # show what it recorded without re-parsing the block.
        checks["capture"] = {
            "working_tree": meta.working_tree or "n/a",
            "staged": meta.staged,
            "unstaged": meta.unstaged,
            "untracked": meta.untracked,
            "stash_count": meta.stash_count,
            "commit_count": len(meta.commits),
            "remote_upstream": meta.remote_upstream or "n/a",
            "remote_pushed": meta.remote_pushed or "n/a",
            "cobtask_id": meta.cobtask_id or "n/a",
            "pr_number": meta.pr_number or "n/a",
            "adr_count": len(meta.adrs),
        }
    except Exception as e:  # noqa: BLE001 - degrade to structured error; pre-flight must never crash  # pragma: no cover
        errors.append(f"cannot import context-window-common: {e}")
        checks["common_import"] = "fail"
    is_git = _git("rev-parse", "--is-inside-work-tree").returncode == 0
    checks["is_git"] = is_git
    checks["head_resolvable"] = is_git and _git("rev-parse", "HEAD").returncode == 0
    print(
        json.dumps(
            {
                "ok": not errors,
                "checks": checks,
                "metadata_block": metadata_block,
                "errors": errors,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
