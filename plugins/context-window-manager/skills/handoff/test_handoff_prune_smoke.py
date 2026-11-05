"""Self-referential smoke test for the retention-prune seam.

Per docs/patterns/subprocess-boundary-smoke.md: the unit tests exercise
retention.py in-process, but the wiring that matters is the handoff validator
invoking the gated prune. This runs the REAL validator.py as a fresh
subprocess with CWM_SAVE_ROOT pointed at a tmp dir seeded with one stale
sibling handoff, then asserts on the filesystem (not a mock) that the stale
file now lives under zArchives/ and the once/day marker was stamped.
"""

import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve()
VALIDATOR = str(HERE.parent / "validator.py")


def test_prune_real_move_across_process_boundary(tmp_path):
    save_root = tmp_path / "sct"
    slug = save_root / "old-99998888"
    slug.mkdir(parents=True)
    stale = slug / "CONTEXT-2026-06-01-00-00-00-aaaaaa.md"
    stale.write_text("x")
    env = {**os.environ, "CWM_SAVE_ROOT": str(save_root)}
    # Hermetic: a developer's exported SCT_CWM_RETENTION_DAYS must not change
    # whether the fixed June fixture counts as stale, so pin the default.
    env.pop("SCT_CWM_RETENTION_DAYS", None)
    proc = subprocess.run(
        [sys.executable, VALIDATOR],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (save_root / "zArchives" / "old-99998888" / stale.name).exists()
    assert not stale.exists()
    assert (save_root / ".last_prune").read_text().strip()  # marker stamped
