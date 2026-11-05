"""Tests for the PreCompact failsafe hook.

The core test runs the hook as a fresh subprocess against THIS repository,
reads back the CONTEXT file it writes with the shared parser, and asserts the
metadata block round-trips across a genuine process boundary (the
subprocess-boundary-smoke pattern). Mirrors
skills/handoff/test_handoff_smoke.py, adjusted for the shallower
hooks/ location (repo root is parents[3], not parents[4]).
"""

import json
import os
import pathlib
import subprocess
import sys

import context_schema
import pytest

HERE = pathlib.Path(__file__).resolve()
HOOK = str(HERE.parent / "precompact_handoff.py")
REPO_ROOT = HERE.parents[3]


def _is_git_repo(path):
    return (
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            check=False,
            text=True,
        ).returncode
        == 0
    )


@pytest.mark.skipif(
    not _is_git_repo(REPO_ROOT), reason="smoke test needs a real git checkout"
)
def test_precompact_writes_context_file_via_fresh_subprocess(tmp_path):
    save_root = tmp_path / "save"
    payload = json.dumps(
        {"hook_event_name": "PreCompact", "trigger": "auto", "cwd": str(REPO_ROOT)}
    )
    r = subprocess.run(
        [sys.executable, HOOK],
        input=payload,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CWM_SAVE_ROOT": str(save_root)},
        capture_output=True,
        check=False,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout).get("continue") is True
    files = list(save_root.rglob("CONTEXT-*.md"))
    assert len(files) == 1, "dispatched side-effect (CONTEXT file) was never written"
    body = files[0].read_text(encoding="utf-8")
    parsed = context_schema.parse_metadata_block(body)
    assert parsed["cwd"] == str(REPO_ROOT)
    assert parsed["git_sha"] and len(parsed["git_sha"]) >= 7
    assert parsed["git_branch"]
    assert "# Resume Command\n\n/context-window-manager:resume\n" in body
    # Narrative prose must not leak into the parsed metadata block. Use a
    # prose-only sentinel: the token "failsafe" alone also occurs in this
    # task's branch name, which is legitimate git_branch metadata.
    assert "not authored by the model" in body
    assert "not authored by the model" not in str(parsed.values()).lower()


def test_hooks_json_registers_precompact():
    hj = HERE.parent / "hooks.json"
    assert hj.exists()
    data = json.loads(hj.read_text(encoding="utf-8"))
    entries = data["hooks"]["PreCompact"]
    hooks = [h for e in entries for h in e["hooks"]]
    cmds = [h["command"] for h in hooks]
    assert any(
        "precompact_handoff.py" in c and "${CLAUDE_PLUGIN_ROOT}" in c for c in cmds
    )
    assert all(e.get("matcher", "*") in ("*", "manual", "auto") for e in entries)
    # A hard outer timeout bounds the synchronous hook even when a filesystem
    # op (not just git/gh) hangs, so it can never block compaction indefinitely.
    ph = next(h for h in hooks if "precompact_handoff.py" in h["command"])
    assert isinstance(ph.get("timeout"), int) and 0 < ph["timeout"] <= 30


@pytest.mark.skipif(
    not _is_git_repo(REPO_ROOT), reason="smoke test needs a real git checkout"
)
def test_precompact_degrades_outside_git(tmp_path):
    work = tmp_path / "nogit"
    work.mkdir()
    save_root = tmp_path / "save"
    payload = json.dumps(
        {"hook_event_name": "PreCompact", "trigger": "manual", "cwd": str(work)}
    )
    r = subprocess.run(
        [sys.executable, HOOK],
        input=payload,
        cwd=str(work),
        env={**os.environ, "CWM_SAVE_ROOT": str(save_root)},
        capture_output=True,
        check=False,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout).get("continue") is True
    files = list(save_root.rglob("CONTEXT-*.md"))
    assert len(files) == 1
    parsed = context_schema.parse_metadata_block(files[0].read_text(encoding="utf-8"))
    # 'n/a' -> None: honest about the missing git state, never fabricated.
    assert parsed["git_sha"] is None


@pytest.mark.skipif(
    not _is_git_repo(REPO_ROOT), reason="smoke test needs a real git checkout"
)
def test_precompact_skips_when_recent_handoff_exists(tmp_path):
    # A recent CONTEXT-*.md (a manual handoff, or an earlier failsafe) must not
    # be shadowed by another deterministic stub, since resume defaults to newest.
    save_root = tmp_path / "save"
    slug_dir = save_root / context_schema.repo_slug(REPO_ROOT)
    slug_dir.mkdir(parents=True)
    existing = slug_dir / "CONTEXT-2026-01-01-00-00-00-abcdef.md"
    existing.write_text(
        "# Resume Command\n\n/context-window-manager:resume\n\nmanual handoff sentinel\n",
        encoding="utf-8",
    )  # freshly written, so its mtime is inside the recent window
    before = {p.name for p in slug_dir.glob("CONTEXT-*.md")}
    payload = json.dumps(
        {"hook_event_name": "PreCompact", "trigger": "auto", "cwd": str(REPO_ROOT)}
    )
    r = subprocess.run(
        [sys.executable, HOOK],
        input=payload,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CWM_SAVE_ROOT": str(save_root)},
        capture_output=True,
        check=False,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout).get("continue") is True
    after = {p.name for p in slug_dir.glob("CONTEXT-*.md")}
    assert after == before, "failsafe must not add a file when a recent handoff exists"
    assert existing.read_text(encoding="utf-8").endswith("manual handoff sentinel\n")


@pytest.mark.skipif(
    not _is_git_repo(REPO_ROOT), reason="smoke test needs a real git checkout"
)
def test_precompact_write_leaves_no_temp_file(tmp_path):
    # The atomic publish (temp + os.replace) must not leave a dot-prefixed
    # .tmp file behind on success, and the only CONTEXT-*.md is the final one.
    save_root = tmp_path / "save"
    payload = json.dumps(
        {"hook_event_name": "PreCompact", "trigger": "auto", "cwd": str(REPO_ROOT)}
    )
    r = subprocess.run(
        [sys.executable, HOOK],
        input=payload,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CWM_SAVE_ROOT": str(save_root)},
        capture_output=True,
        check=False,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert len(list(save_root.rglob("CONTEXT-*.md"))) == 1
    assert list(save_root.rglob(".*.tmp")) == [], "atomic temp file must not linger"


def test_precompact_reports_write_failure_on_stderr(tmp_path):
    # Point the save root at a regular file so save_dir's mkdir raises. The hook
    # must still return continue:true, but the failure must surface on stderr
    # rather than vanish.
    work = tmp_path / "nogit"
    work.mkdir()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    payload = json.dumps(
        {"hook_event_name": "PreCompact", "trigger": "manual", "cwd": str(work)}
    )
    r = subprocess.run(
        [sys.executable, HOOK],
        input=payload,
        cwd=str(work),
        env={**os.environ, "CWM_SAVE_ROOT": str(blocker)},
        capture_output=True,
        check=False,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout).get("continue") is True
    assert "precompact_handoff: snapshot not written" in r.stderr
    assert not list((tmp_path).rglob("CONTEXT-*.md"))


@pytest.mark.parametrize("stdin_text", ["", "not json", "{"])
def test_precompact_tolerates_malformed_stdin(tmp_path, stdin_text):
    save_root = tmp_path / "save"
    r = subprocess.run(
        [sys.executable, HOOK],
        input=stdin_text,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CWM_SAVE_ROOT": str(save_root)},
        capture_output=True,
        check=False,
        text=True,
    )
    # Malformed or empty stdin must never fail compaction. A file may or may
    # not be written (cwd falls back to os.getcwd()); only non-failure is
    # asserted here.
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout).get("continue") is True
