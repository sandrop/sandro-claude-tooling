import json

import pytest
from conftest import write_context_raw


# A handoff under SAVE_ROOT/zArchives/<slug>/ (legacy layout) or
# SAVE_ROOT/zArchives/YYYY-MM/<slug>/ (dated layout) must never be selected or
# listed by resume, while a live handoff under SAVE_ROOT/<slug>/ still is.
# Resume's scan globs a single slug dir non-recursively and its cross-worktree
# fallback only visits real repo-slug dirs, so zArchives/ is excluded
# structurally at any depth; this locks that (AC #5).
@pytest.mark.parametrize("archive_subdir", ["", "2026-09"], ids=["legacy", "dated"])
def test_resume_ignores_zarchives(run_validator, tmp_save_dir, archive_subdir):
    # tmp_save_dir is SAVE_ROOT/<own-slug>; put a LIVE file there.
    write_context_raw(
        tmp_save_dir,
        "2026-09-06-00-00-00",
        "## Session Summary\n\nlive\n",
        suffix="aaaaaa",
    )
    # A NEWER file inside the archive must be ignored despite being newer than
    # the live one.
    arch_dir = tmp_save_dir.parent / "zArchives" / archive_subdir / tmp_save_dir.name
    arch_dir.mkdir(parents=True)
    (arch_dir / "CONTEXT-2026-09-07-00-00-00-bbbbbb.md").write_text(
        "## Session Summary\n\narchived\n"
    )
    result = run_validator()
    assert result["checks"]["context_file"].endswith(
        "CONTEXT-2026-09-06-00-00-00-aaaaaa.md"
    )
    assert "zArchives" not in json.dumps(result)
