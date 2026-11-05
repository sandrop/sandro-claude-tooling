from pathlib import Path

SKILLS = Path(__file__).resolve().parent


def _name(skill_md: Path) -> str:
    for line in skill_md.read_text().splitlines()[1:]:
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"{skill_md} has no name: line")


def test_skill_dirs_are_short_names():
    dirs = sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md"))
    assert dirs == ["handoff", "resume"]


def test_frontmatter_name_matches_dir():
    for skill_md in SKILLS.glob("*/SKILL.md"):
        assert _name(skill_md) == skill_md.parent.name
