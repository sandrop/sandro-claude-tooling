# test_save_paths.py - save-path contract for the plugin-relocated context_schema.
# Save files live at SAVE_ROOT/<repo-slug>/CONTEXT-*.md, home-scoped and
# per-repo-namespaced, replacing the old project-local save path.
from pathlib import Path

import context_schema


def test_save_root_is_home_scoped():
    assert (
        context_schema.SAVE_ROOT
        == Path.home() / ".claude" / ".sct" / "context-window-manager"
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
