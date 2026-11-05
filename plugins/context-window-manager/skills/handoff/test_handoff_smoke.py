"""Self-referential smoke test for the handoff capture seam.

The unit tests exercise the helpers against synthetic repos. This one runs the
real validator as a fresh subprocess against THIS repository, writes the block
it returns into a real CONTEXT file, and reads it back with the shared parser,
so the producer and the consumer meet across a genuine process boundary rather
than an in-process import.
"""

import json
import os
import pathlib
import subprocess
import sys

import context_schema
import pytest

HERE = pathlib.Path(__file__).resolve()
VAL = str(HERE.parent / "validator.py")
REPO_ROOT = HERE.parents[4]


def _is_git_repo(path):
    r = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


@pytest.mark.skipif(
    not _is_git_repo(REPO_ROOT), reason="smoke test needs a real git checkout"
)
def test_real_handoff_round_trips_through_a_written_context_file(tmp_path):
    # CWM_SAVE_ROOT redirects the write away from the real home dir.
    env = {**os.environ, "CWM_SAVE_ROOT": str(tmp_path)}
    r = subprocess.run(
        [sys.executable, VAL],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True, out["errors"]

    # Do what the skill does: join the reported dir and filename, then append
    # the block verbatim under some session prose.
    save_dir = pathlib.Path(out["checks"]["save_dir"])
    assert save_dir.is_dir()
    assert save_dir.stat().st_mode & 0o777 == 0o700
    target = save_dir / out["checks"]["filename"]
    target.write_text(
        "## Session Summary\n\nSmoke test.\n\n"
        "## Next Steps\n\nNone\n\n" + out["metadata_block"],
        encoding="utf-8",
    )

    # The file on disk is the artifact a resume would read.
    parsed = context_schema.parse_metadata_block(target.read_text(encoding="utf-8"))
    assert parsed["cwd"] == str(REPO_ROOT)
    assert parsed["git_sha"] and len(parsed["git_sha"]) >= 7
    assert parsed["git_branch"]
    assert parsed["working_tree"] in ("clean", "dirty")
    assert len(parsed["commits"]) >= 1
    # Prose sitting above the block must not leak into it.
    assert "Smoke test." not in str(parsed.values())


@pytest.mark.skipif(
    not _is_git_repo(REPO_ROOT), reason="smoke test needs a real git checkout"
)
def test_real_handoff_does_not_mutate_the_repository(tmp_path):
    # Handoff is read-only against git; prove it against the live repo rather
    # than a fixture, since that is where a stray write would actually hurt.
    def _state():
        return subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        ).stdout

    before = _state()
    subprocess.run(
        [sys.executable, VAL],
        cwd=str(REPO_ROOT),
        env={**os.environ, "CWM_SAVE_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert _state() == before
