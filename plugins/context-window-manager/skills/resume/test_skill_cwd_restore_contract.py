import json
import os
import re
import subprocess
from pathlib import Path

SKILL_PATH = Path(__file__).with_name("SKILL.md")


def test_mktemp_template_ends_with_x_for_macos():
    text = SKILL_PATH.read_text()

    assert 'mktemp "${TMPDIR:-/tmp}/cwm-resume.XXXXXX"' in text


def test_skill_forbids_appending_prose_to_context_file_arg():
    # Both --context-file recipes must tell the caller to pass ONLY the bare
    # path with nothing appended, so a calling agent can never glue
    # adjacent guidance prose onto the argument.
    for region in (_selection_contract(), _cwd_contract()):
        low = region.lower()
        assert "bare path" in low
        assert "nothing after it" in low or "never append" in low


def _cwd_contract() -> str:
    text = SKILL_PATH.read_text()
    start = text.index("### Step 3:")
    end = text.index("### Step 4: Confirm Direction")
    return text[start:end]


def _bootstrap_contract() -> str:
    text = SKILL_PATH.read_text()
    start = text.index("### Step 0:")
    end = text.index("### Step 1:")
    return text[start:end]


def test_bootstrap_contract_block_present_and_precedes_step1_validator():
    # A pre-Step-1 bootstrap (explicit --context-file only) resolves
    # and restores the handoff's recorded code CWD before the Step 1 validator
    # runs, so a cross-repo explicit handoff is validated from repo B, not A.
    text = SKILL_PATH.read_text()
    region = _bootstrap_contract()

    assert "# cwd-bootstrap-contract" in region
    assert "# end-cwd-bootstrap-contract" in region
    assert "bootstrap_cwd.py" in region

    # Step 0 must precede the Step 1 validator invocation.
    step0 = text.index("### Step 0:")
    step1_validator = text.index('validator.py" > "$validator_report_file"')
    assert step0 < step1_validator

    # Fires only for an explicit handoff; bare-path-only argument, same rule as
    # the Step 1 / Step 3 --context-file recipes.
    assert "--context-file" in region
    low = region.lower()
    assert "bare path" in low
    assert "nothing after it" in low or "never append" in low

    # Explicit-path-only guard: Step 0 runs ONLY for an explicit --context-file
    # and is skipped entirely otherwise, so a normal auto-discovery resume never
    # attempts bootstrap. A future edit that drops the guard must fail here.
    assert "explicit `--context-file`" in low
    assert "skip step 0 entirely" in low


def test_step1_recipe_forwards_explicit_context_file_after_bootstrap():
    # After Step 0 bootstraps from an explicit handoff, Step 1 must forward that
    # bound path to the validator; otherwise, running from the restored repo,
    # the argument-less validator would auto-select the repo's newest handoff
    # instead of the explicitly requested one.
    region = _selection_contract()
    assert 'if [ -n "${explicit_context_file:-}" ]' in region
    assert 'validator.py" --context-file "$explicit_context_file"' in region
    # Discovery path (no explicit file) keeps the argument-less form.
    assert 'validator.py" > "$validator_report_file"' in region


def test_step1_recipe_reenters_bootstrapped_cwd_before_validating():
    # Shell state does not persist across Bash tool calls, so Step 0's `cd` into
    # repo B is gone by Step 1. On the explicit path the recipe must re-enter the
    # bootstrapped directory (bound boot_cwd) before running the validator;
    # otherwise the validator runs back in repo A and rejects the cross-repo
    # handoff, reproducing the bug this feature fixes.
    region = _selection_contract()
    assert 'if [ -n "${boot_cwd:-}" ]' in region
    assert 'cd -- "$boot_cwd"' in region
    # The re-enter must precede the forwarded validator invocation.
    reenter = region.index('cd -- "$boot_cwd"')
    forward = region.index('validator.py" --context-file "$explicit_context_file"')
    assert reenter < forward


def test_step1_forwards_basename_selection_independent_of_bootstrap():
    # A basename --context-file skips Step 0 (which is absolute/tilde only) but
    # must still be forwarded. Binding explicit_context_file is tied to "user
    # supplied --context-file in any form", not to "Step 0 ran"; otherwise a
    # basename selection silently resumes the newest handoff. boot_cwd stays
    # coupled to Step 0 having bootstrapped.
    low = _selection_contract().lower()
    assert "any form" in low
    assert "basename" in low
    assert "only when step 0" in low
    assert "these two bindings are independent" in low


def _selection_contract() -> str:
    text = SKILL_PATH.read_text()
    start = text.index("### Step 1:")
    end = text.index("### Step 2:")
    return text[start:end]


def _script(name: str) -> str:
    contract = _cwd_contract()
    start_marker = f"# {name}"
    end_marker = f"# end-{name}"
    start = contract.index(start_marker)
    end = contract.index(end_marker, start)
    return contract[start : end + len(end_marker)]


def _run_restore(start: Path, recorded: Path, marker: Path):
    script = (
        'cd() { : > "$CD_MARKER"; builtin cd "$@"; }\n'
        + _script("cwd-restore-contract")
        + "\npwd -P\n"
    )
    env = {
        **os.environ,
        "recorded_cwd": str(recorded),
        "CD_MARKER": str(marker),
    }
    return subprocess.run(
        ["bash", "-c", script],
        cwd=start,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_validator_report_binding_survives_new_shell_and_preserves_data(tmp_path):
    injected = tmp_path / "injected"
    literal_path = tmp_path / "$(touch injected)"
    report_file = tmp_path / "validator-report.json"
    report_file.write_text(
        json.dumps(
            {
                "checks": {
                    "context_file": "CONTEXT-$(touch injected).md",
                    "context_path": str(literal_path / "CONTEXT-$(touch injected).md"),
                    "explicit_sibling": True,
                },
                "report": {"meta": {"cwd": str(literal_path)}},
            }
        )
    )
    script = (
        _script("cwd-bind-contract")
        + '\nprintf "%s\\n%s\\n" "$recorded_cwd" "$selected_context_file"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "validator_report_file": str(report_file)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        str(literal_path),
        str(literal_path / "CONTEXT-$(touch injected).md"),
    ]
    assert not injected.exists()


def test_explicit_sibling_selection_skips_both_candidate_pickers():
    contract = _selection_contract()

    bypass_rule = (
        "**`checks.explicit_sibling` is true**: skip both the "
        "`checks.context_candidates` and `checks.cross_worktree_candidates` pickers"
    )
    bypass = contract.index(bypass_rule)
    context_picker = contract.index("**`len(checks.context_candidates) > 1`**")
    cross_worktree_picker = contract.index(
        "**`checks.cross_worktree_candidates` non-empty**"
    )

    assert bypass < context_picker
    assert bypass < cross_worktree_picker


def test_validator_error_gate_precedes_selection_and_report_access():
    text = SKILL_PATH.read_text()
    contract = _selection_contract()

    error_gate = contract.index("**Validator error gate (before selection):**")
    absolute_error_gate = text.index("**Validator error gate (before selection):**")
    bypass = contract.index("**`checks.explicit_sibling` is true**")
    no_context = contract.index(
        "If `ok` is true, `report` is null, and `checks.context_path` is null"
    )
    report_accesses = [
        match.start() for match in re.finditer(r'report\.[a-z_]+|\["report"\]', text)
    ]

    assert error_gate < bypass
    assert error_gate < no_context
    assert report_accesses
    assert all(absolute_error_gate < access for access in report_accesses)
    assert absolute_error_gate < text.index("### Step 2:")
    assert "surface the captured `errors` verbatim and stop" in contract


def test_mismatch_restores_recorded_cwd_before_confirm_direction(tmp_path):
    start = tmp_path / "start"
    target = tmp_path / "target"
    start.mkdir()
    target.mkdir()
    marker = tmp_path / "cd-called"

    result = _run_restore(start, target, marker)

    assert result.returncode == 0
    assert Path(result.stdout.splitlines()[-1]) == target
    assert marker.exists()

    contract = _cwd_contract()
    restore_end = contract.index("# end-cwd-restore-contract")
    refresh_guard = contract.index('if [ "$cwd_changed" -eq 1 ]')
    refresh_command = contract.index('validator.py" --context-file')
    assert restore_end < refresh_guard < refresh_command
    assert "re-run the validator with `--context-file`" in contract
    assert "replace the captured JSON" in contract
    assert "explicit `cwd` or `workdir`" in contract


def test_matching_cwd_does_not_change_directory(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    marker = tmp_path / "cd-called"

    result = _run_restore(target, target, marker)

    assert result.returncode == 0
    assert Path(result.stdout.splitlines()[-1]) == target
    assert not marker.exists()


def test_unavailable_recorded_cwd_stops_before_confirm_direction(tmp_path):
    start = tmp_path / "start"
    start.mkdir()
    missing = tmp_path / "missing"
    marker = tmp_path / "cd-called"

    result = _run_restore(start, missing, marker)

    assert result.returncode != 0
    assert str(missing) in result.stderr
    assert not marker.exists()
