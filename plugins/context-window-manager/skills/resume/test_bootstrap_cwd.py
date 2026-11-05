"""Tests for bootstrap_cwd.py: the pre-validator CWD bootstrap that
resolves and strictly verifies an explicit handoff's recorded code working
directory before the Step 1 validator runs. Every test drives the helper as a
fresh subprocess, mirroring how the resume skill invokes it."""

import json
import os
import subprocess
import sys
from pathlib import Path

import context_schema
from context_schema import SessionMetadata

BOOT = Path(__file__).parent / "bootstrap_cwd.py"
VAL = Path(__file__).parent / "validator.py"


def _git_init(d: Path) -> None:
    subprocess.run(["git", "init", "-q", str(d)], check=True)


def _write_handoff(
    save_dir: Path, ts: str, meta: SessionMetadata, extra_body: str = ""
) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "## Session Summary\n\nx\n\n"
        + extra_body
        + context_schema.render_metadata_block(meta)
    )
    p = save_dir / f"CONTEXT-{ts}.md"
    p.write_text(body)
    return p


def _run(handoff: Path, cwd: Path) -> dict:
    r = subprocess.run(
        [sys.executable, str(BOOT), "--context-file", str(handoff)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    # Contract: a single JSON envelope on stdout, exit 0 always. A regression
    # that printed the JSON then exited nonzero would break callers under
    # `set -e`, so assert the return code on every invocation.
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_valid_recorded_cwd_resolves_and_signals_bootstrap(tmp_path):
    repo_b = tmp_path / "repoB"
    repo_b.mkdir()
    _git_init(repo_b)
    handoff = _write_handoff(
        tmp_path / "sct" / "slugB",
        "2026-09-03-10-00-00",
        SessionMetadata(cwd=str(repo_b), git_sha="deadbeef", git_branch="main"),
    )
    repo_a = tmp_path / "repoA"
    repo_a.mkdir()
    _git_init(repo_a)

    out = _run(handoff, cwd=repo_a)

    assert out["ok"] is True
    assert out["bootstrap"] is True
    assert Path(out["cwd"]).resolve() == repo_b.resolve()


def test_legacy_handoff_without_recorded_cwd_is_bootstrap_noop(tmp_path):
    # Empty SessionMetadata() renders `- cwd: n/a`, which parses back to None:
    # bootstrap is a no-op signal and the caller keeps current same-repo
    # behavior (resolved pre-start decision Q3).
    handoff = _write_handoff(
        tmp_path / "sct" / "slug", "2026-09-03-11-00-00", SessionMetadata()
    )
    repo_a = tmp_path / "repoA"
    repo_a.mkdir()

    out = _run(handoff, cwd=repo_a)

    assert out["ok"] is True
    assert out["bootstrap"] is False
    assert out["cwd"] is None


def test_fail_loud_relative_recorded_cwd(tmp_path):
    handoff = _write_handoff(
        tmp_path / "sct" / "slug",
        "2026-09-03-11-10-00",
        SessionMetadata(cwd="rel/path", git_sha="x", git_branch="main"),
    )
    out = _run(handoff, cwd=tmp_path)
    assert out["ok"] is False
    assert out["bootstrap"] is False
    assert "rel/path" in out["errors"][0]


def test_fail_loud_absolute_missing_recorded_cwd(tmp_path):
    missing = tmp_path / "nope"
    handoff = _write_handoff(
        tmp_path / "sct" / "slug",
        "2026-09-03-11-11-00",
        SessionMetadata(cwd=str(missing), git_sha="x", git_branch="main"),
    )
    out = _run(handoff, cwd=tmp_path)
    assert out["ok"] is False
    assert out["bootstrap"] is False
    assert str(missing) in out["errors"][0]


def test_fail_loud_non_git_recorded_cwd(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    handoff = _write_handoff(
        tmp_path / "sct" / "slug",
        "2026-09-03-11-12-00",
        SessionMetadata(cwd=str(plain), git_sha="x", git_branch="main"),
    )
    out = _run(handoff, cwd=tmp_path)
    assert out["ok"] is False
    assert out["bootstrap"] is False
    assert str(plain) in out["errors"][0]


def test_not_context_file_rejected_as_data(tmp_path):
    other = tmp_path / "notes.md"
    other.write_text("## Session Summary\n\nx\n")
    r = subprocess.run(
        [sys.executable, str(BOOT), "--context-file", str(other)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert out["bootstrap"] is False
    assert str(other) in out["errors"][0]


def test_nonexistent_context_path_rejected_as_data(tmp_path):
    ghost = tmp_path / "CONTEXT-2026-09-03-11-13-00.md"
    r = subprocess.run(
        [sys.executable, str(BOOT), "--context-file", str(ghost)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert str(ghost) in out["errors"][0]


def test_prose_working_directory_divergence_warns_but_bootstraps(tmp_path):
    repo_b = tmp_path / "repoB"
    repo_b.mkdir()
    _git_init(repo_b)
    prose = "- Working directory (code): /some/other/place\n\n"
    handoff = _write_handoff(
        tmp_path / "sct" / "slugB",
        "2026-09-03-11-20-00",
        SessionMetadata(cwd=str(repo_b), git_sha="x", git_branch="main"),
        extra_body=prose,
    )
    out = _run(handoff, cwd=tmp_path)

    assert out["ok"] is True
    assert out["bootstrap"] is True
    assert Path(out["cwd"]).resolve() == repo_b.resolve()
    assert out["warnings"]
    assert "/some/other/place" in out["warnings"][0]


def test_validator_observes_restored_repo_after_bootstrap(tmp_path):
    # End-to-end fresh-subprocess smoke (AC6): resumed from repo A,
    # bootstrap_cwd.py resolves repo B's recorded cwd; the REAL validator, run
    # from that resolved directory with the explicit handoff, then returns
    # ok:true and reports repo B as report.meta.cwd -- proving the validator
    # observes the restored repository, not A. The handoff must live in repo
    # B's real save_dir so the post-bootstrap validator resolves it as a
    # same-checkout selection.
    save_root = tmp_path / "sct"
    repo_a = tmp_path / "repoA"
    repo_a.mkdir()
    _git_init(repo_a)
    repo_b = tmp_path / "repoB"
    repo_b.mkdir()
    _git_init(repo_b)
    b_save = save_root / context_schema.repo_slug(repo_b)
    handoff = _write_handoff(
        b_save,
        "2026-09-03-12-00-00",
        SessionMetadata(
            cwd=str(repo_b.resolve()), git_sha="deadbeef", git_branch="main"
        ),
    )

    boot = _run(handoff, cwd=repo_a)
    assert boot["ok"] is True
    assert boot["bootstrap"] is True

    r = subprocess.run(
        [sys.executable, str(VAL), "--context-file", str(handoff)],
        cwd=boot["cwd"],
        capture_output=True,
        text=True,
        env={**os.environ, "CWM_SAVE_ROOT": str(save_root)},
        check=False,
    )
    j = json.loads(r.stdout)
    assert j["ok"] is True, j["errors"]
    assert Path(j["report"]["meta"]["cwd"]).resolve() == repo_b.resolve()


def test_non_timestamp_context_filename_is_accepted_like_the_validator(tmp_path):
    # The validator accepts any CONTEXT-*.md name (e.g. CONTEXT-latest.md, locked
    # by test_context_file_non_timestamp_basename_resolves). The bootstrap must
    # apply the same filename contract, or an explicit cross-repo handoff the
    # validator would accept is rejected here before it ever runs.
    repo_b = tmp_path / "repoB"
    repo_b.mkdir()
    _git_init(repo_b)
    handoff = _write_handoff(
        tmp_path / "sct" / "slugB",
        "latest",
        SessionMetadata(cwd=str(repo_b), git_sha="x", git_branch="main"),
    )
    assert handoff.name == "CONTEXT-latest.md"
    repo_a = tmp_path / "repoA"
    repo_a.mkdir()
    _git_init(repo_a)

    out = _run(handoff, cwd=repo_a)

    assert out["ok"] is True
    assert out["bootstrap"] is True
    assert Path(out["cwd"]).resolve() == repo_b.resolve()


def test_missing_context_file_argument_stays_in_json_contract(tmp_path):
    # A missing --context-file must ride in the JSON envelope with exit 0, not a
    # bare argparse SystemExit(2) to stderr, so a caller parsing stdout under
    # `set -e` gets a structured error rather than an empty stdout and a crash.
    r = subprocess.run(
        [sys.executable, str(BOOT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert out["bootstrap"] is False
    assert "--context-file" in out["errors"][0]


def test_unknown_argument_stays_in_json_contract(tmp_path):
    # An unknown flag triggers argparse's SystemExit path; it too must resolve
    # to a JSON envelope with exit 0.
    r = subprocess.run(
        [sys.executable, str(BOOT), "--bogus", "x"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert out["errors"]


def test_invalid_utf8_handoff_fails_loud_not_silently_stripped(tmp_path):
    # A corrupted byte in the handoff must fail loud (ok:false), never be
    # silently discarded by a lenient decode that could reshape the recorded
    # CWD into a different, valid-looking path.
    repo_b = tmp_path / "repoB"
    repo_b.mkdir()
    _git_init(repo_b)
    save_dir = tmp_path / "sct" / "slugB"
    save_dir.mkdir(parents=True)
    handoff = save_dir / "CONTEXT-2026-09-03-13-00-00.md"
    body = "## Session Summary\n\nx\n\n" + context_schema.render_metadata_block(
        SessionMetadata(cwd=str(repo_b), git_sha="x", git_branch="main")
    )
    # Append a raw invalid UTF-8 byte after otherwise valid content.
    handoff.write_bytes(body.encode("utf-8") + b"\xff")

    out = _run(handoff, cwd=tmp_path)

    assert out["ok"] is False
    assert out["bootstrap"] is False
    assert out["errors"]


def test_control_chars_in_prose_cwd_are_escaped_in_warning(tmp_path):
    # Untrusted prose 'Working directory (code)' text is emitted in a warning
    # the caller prints verbatim, so a crafted escape sequence must be escaped,
    # never re-materialized as a raw terminal control character.
    repo_b = tmp_path / "repoB"
    repo_b.mkdir()
    _git_init(repo_b)
    prose = "- Working directory (code): /evil\x1b[2Jplace\n\n"
    handoff = _write_handoff(
        tmp_path / "sct" / "slugB",
        "2026-09-03-13-10-00",
        SessionMetadata(cwd=str(repo_b), git_sha="x", git_branch="main"),
        extra_body=prose,
    )

    out = _run(handoff, cwd=tmp_path)

    assert out["ok"] is True
    assert out["bootstrap"] is True
    assert out["warnings"]
    joined = " ".join(out["warnings"])
    assert "\x1b" not in joined
    assert "\\x1b" in joined
