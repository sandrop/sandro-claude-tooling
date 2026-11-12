"""Self-referential smoke test for the retention-prune seam.

Per docs/patterns/subprocess-boundary-smoke.md: the unit tests exercise
retention.py in-process, but the wiring that matters is the handoff validator
invoking the prune. This runs the REAL validator.py as a fresh subprocess
(through the run_handoff_validator fixture, which also drops any exported
retention vars) with CWM_SAVE_ROOT pointed at a tmp dir seeded with a stale
sibling slug dir, then asserts on the filesystem (not a mock) that the whole
dir now lives under zArchives/<YYYY-MM>/.
"""

from datetime import date


def test_prune_real_move_across_process_boundary(tmp_path, run_handoff_validator):
    save_root = tmp_path / "sct"
    slug = save_root / "old-99998888"
    slug.mkdir(parents=True)
    stale = slug / "CONTEXT-2026-06-01-00-00-00-aaaaaa.md"
    stale.write_text("x")
    run_handoff_validator(cwd=tmp_path, save_root=save_root)
    assert (save_root / "zArchives" / "2026-06" / "old-99998888" / stale.name).exists()
    assert not slug.exists()  # the whole slug dir moved


def test_handoff_config_days_prunes_every_handoff(tmp_path, run_handoff_validator):
    save_root = tmp_path / "sct"
    slug = save_root / "old-77776666"
    slug.mkdir(parents=True)
    for ts in ("2026-07-01-00-00-00", "2026-08-01-00-00-00"):
        (slug / f"CONTEXT-{ts}-aaaaaa.md").write_text("x")
    (save_root / "config.toml").write_text("[retention]\ndays = 30\n")
    # A legacy once-a-day marker for today must not stop the prune.
    (save_root / ".last_prune").write_text(date.today().isoformat())  # noqa: DTZ011 - naive local date, as the validator uses
    out = run_handoff_validator(cwd=tmp_path, save_root=save_root)
    assert out["ok"] is True
    assert out["checks"]["retention"] == {
        "days": 30,
        "dirs_moved": ["2026-08/old-77776666"],
    }
    dest = save_root / "zArchives" / "2026-08" / "old-77776666"
    assert sorted(p.name for p in dest.iterdir()) == [
        "CONTEXT-2026-07-01-00-00-00-aaaaaa.md",
        "CONTEXT-2026-08-01-00-00-00-aaaaaa.md",
    ]
    assert not slug.exists()
