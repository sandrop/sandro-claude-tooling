# test_save_paths.py - save-path contract for the plugin-relocated context_schema.
# Save files live at SAVE_ROOT/<repo-slug>/CONTEXT-*.md, home-scoped and
# per-repo-namespaced, replacing the old project-local save path.
from pathlib import Path

import context_schema


def test_save_root_is_home_scoped():
    assert (
        context_schema.SAVE_ROOT
        == Path.home() / ".config" / "sct" / "context-window-manager"
    )


def test_save_dir_is_home_scoped_and_repo_namespaced(tmp_path):
    # Pure path computation; create defaults False so nothing hits disk.
    expected = context_schema.SAVE_ROOT / context_schema.repo_slug(tmp_path)
    assert context_schema.save_dir(tmp_path) == expected
    assert not expected.exists()


def test_save_dir_create_makes_directory(tmp_path, monkeypatch):
    # Redirect SAVE_ROOT so the test never writes into the real home dir.
    monkeypatch.setattr(context_schema, "SAVE_ROOT", tmp_path / "sct")
    d = context_schema.save_dir(tmp_path, create=True)
    assert d.is_dir()
    assert d == (tmp_path / "sct") / context_schema.repo_slug(tmp_path)


def test_save_dir_create_restricts_existing_directory_permissions(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(context_schema, "SAVE_ROOT", tmp_path / "sct")
    d = context_schema.save_dir(tmp_path)
    d.mkdir(parents=True, mode=0o755)
    d.chmod(0o755)

    context_schema.save_dir(tmp_path, create=True)

    assert d.stat().st_mode & 0o777 == 0o700


def test_repo_slug_falls_back_to_cwd_name_outside_git(tmp_path):
    # tmp_path is not inside a git repo; slug is basename + path-derived suffix.
    slug = context_schema.repo_slug(tmp_path)
    prefix, sep, suffix = slug.rpartition("-")
    assert prefix == tmp_path.resolve().name
    assert sep == "-"
    assert len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix)


def test_repo_slug_disambiguates_same_basename_across_dirs(tmp_path):
    # Two dirs sharing a basename in different parents must NOT collide: the
    # path-derived suffix is what keeps their handoffs in distinct subdirs.
    a = tmp_path / "parent_a" / "foo"
    b = tmp_path / "parent_b" / "foo"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    slug_a = context_schema.repo_slug(a)
    slug_b = context_schema.repo_slug(b)
    assert slug_a != slug_b
    assert slug_a.startswith("foo-") and slug_b.startswith("foo-")


def test_context_dir_constant_removed():
    # The old project-local constant must be gone from the plugin copy.
    assert not hasattr(context_schema, "CONTEXT_DIR")


class _FakeDeadline:
    def __init__(self, expired):
        self._expired = expired

    def expired(self):
        return self._expired


def test_repo_slug_skips_git_probe_when_deadline_expired(tmp_path, monkeypatch):
    # An expired shared budget must skip the git call outright rather than spend
    # another _GIT_TIMEOUT; the cwd fallback slug is used instead.
    calls = {"n": 0}
    real_run = context_schema.subprocess.run

    def spy(*args, **kwargs):
        calls["n"] += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(context_schema.subprocess, "run", spy)
    slug = context_schema.repo_slug(tmp_path, deadline=_FakeDeadline(True))
    assert calls["n"] == 0, "git probe must not run once the shared budget is gone"
    assert slug.startswith(tmp_path.resolve().name + "-")


def test_repo_slug_probes_git_when_deadline_live(tmp_path, monkeypatch):
    # A live budget preserves the original always-probe behavior.
    calls = {"n": 0}
    real_run = context_schema.subprocess.run

    def spy(*args, **kwargs):
        calls["n"] += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(context_schema.subprocess, "run", spy)
    context_schema.repo_slug(tmp_path, deadline=_FakeDeadline(False))
    assert calls["n"] >= 1, "a live budget must still attempt the git probe"


def test_save_dir_forwards_deadline_to_repo_slug(tmp_path, monkeypatch):
    # save_dir must thread its deadline through to repo_slug's probe gate.
    seen = {}

    def fake_slug(_cwd, deadline=None):
        seen["deadline"] = deadline
        return "slug-00000000"

    monkeypatch.setattr(context_schema, "repo_slug", fake_slug)
    sentinel = _FakeDeadline(True)
    context_schema.save_dir(tmp_path, deadline=sentinel)
    assert seen["deadline"] is sentinel
