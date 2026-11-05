# Validator pre-flight should archive a stale sibling handoff once/day and
# surface a retention summary, without touching this session's fresh file.


def test_validator_prunes_stale_handoff(tmp_path, run_handoff_validator):
    save_root = tmp_path / "sct"
    # seed a stale handoff under some other slug dir
    old = save_root / "old-11112222"
    old.mkdir(parents=True)
    (old / "CONTEXT-2026-06-01-00-00-00-aaaaaa.md").write_text("x")
    result = run_handoff_validator(cwd=tmp_path, save_root=save_root)
    assert result["ok"] is True
    assert "retention" in result["checks"]  # summary surfaced
    assert (save_root / "zArchives" / "old-11112222").exists()
    assert not old.exists()


def test_validator_surfaces_config_error_without_aborting(
    tmp_path, run_handoff_validator
):
    save_root = tmp_path / "sct"
    old = save_root / "old-33334444"
    old.mkdir(parents=True)
    (old / "CONTEXT-2026-06-01-00-00-00-aaaaaa.md").write_text("x")
    result = run_handoff_validator(
        cwd=tmp_path,
        save_root=save_root,
        extra_env={"SCT_CWM_RETENTION_DAYS": "nope"},
    )
    # Loud, visible, but the handoff pre-flight still succeeds.
    assert result["ok"] is True
    assert "retention_error" in result["checks"]
    assert "retention" not in result["checks"]
    assert old.exists()  # nothing moved when config is invalid
