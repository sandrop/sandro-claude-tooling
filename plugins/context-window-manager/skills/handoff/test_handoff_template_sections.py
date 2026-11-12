"""Guard the handoff template's required-section list.

Two prose sections were added to the Step 3 capture list: "Traps / dead ends"
and "Skills to invoke on resume". These assertions pin those headings and the
global explicit-None rule that governs them, so a future edit to SKILL.md that
drops either section (or the rule they inherit) fails loudly.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap

import pytest

SKILL = pathlib.Path(__file__).resolve().parent / "SKILL.md"


def _skill_text():
    return SKILL.read_text(encoding="utf-8")


def test_traps_section_present():
    assert "**Traps / dead ends.**" in _skill_text()


def test_skills_to_invoke_section_present():
    assert "**Skills to invoke on resume.**" in _skill_text()


def test_explicit_none_rule_present():
    # AC #2: the two new sections inherit the global explicit-None rule.
    # Verbatim wording (straight quotes) so a rewording that weakens it fails.
    rule = 'write the heading with an explicit "None" rather than omitting it.'
    assert rule in _skill_text()


def test_no_versioned_cache_paths_rule_present():
    assert "Never cite a versioned plugin cache path" in _skill_text()


def _step6():
    text = _skill_text()
    start = text.index("6. **Spawn the next session.**")
    return text[start : text.index("## Rules", start)]


def test_step6_runs_spawn_with_resume_command():
    step = _step6()
    assert '/skills/handoff/spawn.py" --resume-command' in step
    assert "checks.resume_command" in step


def test_step6_names_opt_out_and_needs_choice_rerun():
    step = _step6()
    assert "SCT_CWM_SPAWN=0" in step
    assert "needs-choice" in step
    assert "--stage" in step


def test_step6_refuses_a_multiline_resume_command():
    # A line break could end the heredoc early, so the skill must not run it.
    assert "contains a line break, do not run the command" in _step6()


def _step6_snippet():
    step = _step6()
    start = step.index("```bash\n") + len("```bash\n")
    return textwrap.dedent(step[start : step.index("```", start)])


HOSTILE = (
    "/context-window-manager:resume --context-file "
    "/x/a b $(touch subst) `touch tick` \"q\" 'q' $HOME/CONTEXT-a.md and continue"
)


@pytest.mark.parametrize("shell", [["bash", "-c"], ["zsh", "-f", "-c"]])
def test_step6_snippet_passes_resume_command_literally(tmp_path, shell):
    if shutil.which(shell[0]) is None:
        pytest.skip(f"{shell[0]} not installed")
    stub = tmp_path / "plugin" / "skills" / "handoff" / "spawn.py"
    stub.parent.mkdir(parents=True)
    stub.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python3 = bin_dir / "python3"
    python3.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python3.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(tmp_path / "plugin"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }
    env.pop("BASH_ENV", None)
    snippet = _step6_snippet().replace("<checks.resume_command>", HOSTILE)
    r = subprocess.run(
        [*shell, snippet],
        cwd=work,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["--resume-command", HOSTILE]
    assert list(work.iterdir()) == []  # no substitution ran `touch`
