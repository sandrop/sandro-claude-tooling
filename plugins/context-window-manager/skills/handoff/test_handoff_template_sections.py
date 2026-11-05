"""Guard the handoff template's required-section list.

Two prose sections were added to the Step 3 capture list: "Traps / dead ends"
and "Skills to invoke on resume". These assertions pin those headings and the
global explicit-None rule that governs them, so a future edit to SKILL.md that
drops either section (or the rule they inherit) fails loudly.
"""

import pathlib

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
