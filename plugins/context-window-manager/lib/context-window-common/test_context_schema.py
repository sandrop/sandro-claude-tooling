import dataclasses
import re
import subprocess
from datetime import datetime
from pathlib import Path

import context_schema as cs

# publish.sh rejects a literal `<3 uppercase letters>-<4 digits>` anywhere in a
# first-party plugin tree, so sample task ids are assembled from parts. The
# assembled value still exercises the real id shape.
_ID_PREFIX = "ABC"
TASK_ID = f"{_ID_PREFIX}-0033"
DECOY_ID = f"{_ID_PREFIX}-9999"


def test_render_then_parse_round_trips_git_values():
    block = cs.render_metadata_block(
        cs.SessionMetadata(
            git_sha="abc123def", git_sha_short="abc123d", git_branch="feat/foo"
        )
    )
    parsed = cs.parse_metadata_block("intro text\n\n" + block + "\ntrailer")
    assert parsed["git_sha"] == "abc123def"
    assert parsed["git_sha_short"] == "abc123d"
    assert parsed["git_branch"] == "feat/foo"


def test_non_git_values_render_na_and_parse_to_none():
    block = cs.render_metadata_block(cs.SessionMetadata())
    assert "n/a" in block
    parsed = cs.parse_metadata_block(block)
    assert parsed["git_sha"] is None
    assert parsed["git_sha_short"] is None
    assert parsed["git_branch"] is None


def test_optional_counts_render_na_when_unknown():
    # A probe that never ran (default construction) or that failed leaves the
    # count unknown. Unknown must render as an explicit 'n/a', never a bare 0,
    # so a failed probe can never be mistaken for "checked, found nothing".
    block = cs.render_metadata_block(cs.SessionMetadata())
    for key in cs._META_OPT_COUNT_KEYS:
        assert f"- {key}: n/a\n" in block
    parsed = cs.parse_metadata_block(block)
    assert all(parsed[key] is None for key in cs._META_OPT_COUNT_KEYS)


def test_optional_counts_render_as_int_including_zero_when_known():
    # A successful probe that found nothing (0) is a real, knowable answer and
    # must survive the empty-value 'n/a' rule that applies to the string keys.
    meta = cs.SessionMetadata(
        staged=0,
        unstaged=2,
        untracked=0,
        stash_count=0,
        remote_ahead=0,
        remote_behind=3,
    )
    block = cs.render_metadata_block(meta)
    expected = dict(zip(cs._META_OPT_COUNT_KEYS, (0, 2, 0, 0, 0, 3)))
    for key, value in expected.items():
        assert f"- {key}: {value}\n" in block
    parsed = cs.parse_metadata_block(block)
    assert all(parsed[key] == str(value) for key, value in expected.items())


def test_parse_ignores_decoy_lines_outside_the_block():
    # A `- git_sha:`-shaped decoy appears BOTH before the Session Metadata
    # heading AND under a later heading. Only the in-block value must win.
    text = (
        "### Key Context\n"
        "- git_sha: decoydead\n\n"
        "### Session Metadata\n\n"
        "- git_sha: realsha1\n"
        "- git_sha_short: realsh\n"
        "- git_branch: main\n\n"
        "### More Key Context\n"
        "- git_sha: decoydead\n"
    )
    parsed = cs.parse_metadata_block(text)
    assert parsed["git_sha"] == "realsha1"
    assert parsed["git_sha_short"] == "realsh"
    assert parsed["git_branch"] == "main"


def test_parse_ignores_decoys_outside_the_block_and_outside_a_list():
    # Two separate decoys: a `- cobtask_id:` line above the heading (the real
    # in-block 'n/a' must still win), and an indented bullet that is not under
    # any list key (it must not be swept into commits).
    text = (
        "### Key Context\n"
        f"- cobtask_id: {DECOY_ID}\n"
        "  - deadbeef decoy commit\n\n"
        "### Session Metadata\n\n"
        "- cobtask_id: n/a\n"
        "  - deadbeef nope\n"
        "- commits:\n"
        "  - abc1 real: subject\n"
        "- adrs:\n"
    )
    parsed = cs.parse_metadata_block(text)
    assert parsed["cobtask_id"] is None
    assert parsed["commits"] == ["abc1 real: subject"]
    assert parsed["adrs"] == []


def test_blank_line_closes_an_open_list():
    # The rendered block never puts a blank line inside a list, so a bullet
    # that reappears after one is prose, not a continuation.
    text = (
        "### Session Metadata\n\n"
        "- commits:\n"
        "  - abc1 real: subject\n\n"
        "  - def2 stray bullet in prose\n"
    )
    assert cs.parse_metadata_block(text)["commits"] == ["abc1 real: subject"]


def test_parse_without_session_metadata_heading_returns_all_none():
    text = "### Key Context\n- git_sha: decoydead\n- git_branch: main\n"
    parsed = cs.parse_metadata_block(text)
    assert all(parsed[k] is None for k in cs._META_SCALAR_KEYS)
    assert all(parsed[k] == [] for k in cs._META_LIST_KEYS)


def test_render_order_covers_every_declared_key():
    # Adding a key to one of the kind tuples but forgetting the render order
    # would silently drop it from every handoff, so pin the two together.
    declared = set(cs._META_SCALAR_KEYS) | set(cs._META_LIST_KEYS)
    assert set(cs._META_ORDER) == declared
    assert len(cs._META_ORDER) == len(declared)


def test_every_declared_key_is_a_dataclass_field():
    fields = {f.name for f in dataclasses.fields(cs.SessionMetadata)}
    assert set(cs._META_ORDER) == fields


def test_render_parse_roundtrip_scalars():
    meta = cs.SessionMetadata(
        git_sha="abc123",
        git_sha_short="abc1",
        git_branch="feat/x",
        cwd="/repo",
        cobtask_id=TASK_ID,
        cobtask_path=f"/vault/{TASK_ID}.md",
        tdd_plan_path="",
        pr_number="12",
        pr_url="https://gh/pr/12",
        working_tree="clean",
        staged=0,
        unstaged=0,
        untracked=0,
        stash_count=0,
        remote_upstream="origin/feat/x",
        remote_ahead=0,
        remote_behind=0,
        remote_pushed="yes",
        commits=[],
        adrs=[],
    )
    parsed = cs.parse_metadata_block(cs.render_metadata_block(meta))
    assert parsed["cobtask_id"] == TASK_ID
    assert parsed["pr_url"] == "https://gh/pr/12"
    assert parsed["tdd_plan_path"] is None  # "" -> n/a -> None
    assert parsed["working_tree"] == "clean"
    assert parsed["remote_pushed"] == "yes"


def test_render_parse_roundtrip_list_fields():
    # Commit subjects carry their own colons ("fix: a"), so a list item must
    # never be mistaken for a `- key: value` scalar line.
    meta = cs.SessionMetadata(
        commits=["abc1 fix: a", "def2 feat: b"],
        adrs=["/d/ADR-1.md"],
    )
    parsed = cs.parse_metadata_block(cs.render_metadata_block(meta))
    assert parsed["commits"] == ["abc1 fix: a", "def2 feat: b"]
    assert parsed["adrs"] == ["/d/ADR-1.md"]


def test_empty_lists_round_trip_as_empty_lists():
    parsed = cs.parse_metadata_block(cs.render_metadata_block(cs.SessionMetadata()))
    assert parsed["commits"] == []
    assert parsed["adrs"] == []


def test_scalar_newline_cannot_inject_a_fake_key_line():
    # A value containing a newline must not be able to smuggle its own
    # `- key: value` line into the block: that line would let a later scan
    # silently overwrite an earlier, legitimately-rendered field.
    meta = cs.SessionMetadata(
        git_sha="realsha",
        tdd_plan_path="/plan.md\n- git_sha: fakesha",
    )
    block = cs.render_metadata_block(meta)
    parsed = cs.parse_metadata_block(block)
    assert parsed["git_sha"] == "realsha"
    assert parsed["tdd_plan_path"] == "/plan.md - git_sha: fakesha"


def test_scalar_backtick_is_neutralized():
    # A backtick in a captured value must not form a `` `path` `` token for
    # resume's reference scanner to pick up and probe against the filesystem.
    meta = cs.SessionMetadata(cobtask_path="`~/.ssh/config:1`")
    block = cs.render_metadata_block(meta)
    assert "`" not in block
    parsed = cs.parse_metadata_block(block)
    assert parsed["cobtask_path"] == "'~/.ssh/config:1'"


def test_list_item_newline_cannot_break_out_of_the_list():
    # A newline-bearing commit subject must not be able to open a fake
    # top-level list key and inject items into it.
    meta = cs.SessionMetadata(commits=["abc1 fix: a\n- adrs:\n  - /evil.md"])
    block = cs.render_metadata_block(meta)
    parsed = cs.parse_metadata_block(block)
    assert parsed["adrs"] == []
    assert len(parsed["commits"]) == 1
    assert "/evil.md" in parsed["commits"][0]


def test_scalar_unicode_line_separator_cannot_inject_a_fake_key_line():
    # str.splitlines() (used by parse_metadata_block to walk the file) treats
    # U+2028 LINE SEPARATOR as a line break too, not just \r/\n. A value
    # carrying it must not be able to smuggle a fake `- key: value` line the
    # same way a literal newline could.
    meta = cs.SessionMetadata(
        git_sha="realsha",
        tdd_plan_path="/plan.md - git_sha: fakesha",
    )
    block = cs.render_metadata_block(meta)
    parsed = cs.parse_metadata_block(block)
    assert parsed["git_sha"] == "realsha"
    assert parsed["tdd_plan_path"] == "/plan.md - git_sha: fakesha"


def test_context_filename_matches_collision_resistant_shape():
    name = cs.context_filename(datetime(2026, 8, 8, 9, 0, 0))
    assert re.fullmatch(r"CONTEXT-2026-08-08-09-00-00-[0-9a-f]{6}\.md", name)


def test_context_filename_is_collision_resistant_within_same_second():
    now = datetime(2026, 8, 8, 9, 0, 0)
    a = cs.context_filename(now)
    b = cs.context_filename(now)
    assert a != b


def _init_repo(p: Path) -> None:
    for a in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(a, cwd=p, check=True)
    (p / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=p, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=p, check=True)


def test_worktree_toplevels_lists_primary_and_linked(tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    _init_repo(primary)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feat/x", str(linked)],
        cwd=primary,
        check=True,
    )
    tops = cs.worktree_toplevels(primary)
    resolved = {p.resolve() for p in tops}
    assert primary.resolve() in resolved
    assert linked.resolve() in resolved
    assert all(isinstance(p, Path) for p in tops)


def test_worktree_toplevels_empty_outside_repo(tmp_path):
    assert cs.worktree_toplevels(tmp_path) == []
