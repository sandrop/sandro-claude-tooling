import verify_refs


SAMPLE = """### Key Context

- See `src/app.py:42` for the entry point
- Config at `configs/settings.toml`
- Not a path: `some phrase here`
"""


def test_extract_finds_path_and_line():
    refs = verify_refs.extract_refs(SAMPLE)
    paths = {r["path"]: r["line"] for r in refs}
    assert paths.get("src/app.py") == 42
    assert "configs/settings.toml" in paths
    assert "some phrase here" not in paths


def test_extract_ignores_prose_that_looks_dotted_or_slashed():
    # These backticked tokens are NOT file paths and must not be extracted,
    # or the resume stale-ref report would surface spurious "missing" warnings.
    prose = (
        "Use `n/a` when absent, call `obj.map`, pin `3.14`, ship `Node.js`, "
        "and read `and/or` carefully."
    )
    paths = {r["path"] for r in verify_refs.extract_refs(prose)}
    assert paths == set()


def test_verify_flags_missing(tmp_path):
    (tmp_path / "here.py").write_text("x = 1\n")
    refs = [
        {"raw": "`here.py:1`", "path": "here.py", "line": 1},
        {"raw": "`gone.py`", "path": "gone.py", "line": None},
    ]
    res = {r["path"]: r for r in verify_refs.verify_refs(refs, str(tmp_path))}
    assert res["here.py"]["exists"] is True
    assert res["gone.py"]["exists"] is False


def test_absolute_path_inside_cwd_is_read(tmp_path):
    f = tmp_path / "abs.py"
    f.write_text("a = 1\nb = 2\n")
    refs = [{"raw": f"`{f}:2`", "path": str(f), "line": 2}]
    res = verify_refs.verify_refs(refs, str(tmp_path))[0]
    assert res["exists"] is True
    assert res["line_ok"] is True


def test_out_of_tree_file_exists_but_is_not_read(tmp_path):
    (tmp_path / "secret.txt").write_text("s\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    refs = [{"raw": "`../secret.txt:1`", "path": "../secret.txt", "line": 1}]
    res = verify_refs.verify_refs(refs, str(repo))[0]
    assert res["exists"] is True
    assert res["line_ok"] is None


def test_tilde_prefixed_nonexistent_token_does_not_raise(tmp_path):
    refs = [{"raw": "`~/does-not-exist-xyz.py`", "path": "~/does-not-exist-xyz.py",
             "line": None}]
    res = verify_refs.verify_refs(refs, str(tmp_path))[0]
    assert res["exists"] is False
