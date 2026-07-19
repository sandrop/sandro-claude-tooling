import context_schema as cs


def test_render_then_parse_round_trips_git_values():
    block = cs.render_metadata_block("abc123def", "abc123d", "feat/foo")
    parsed = cs.parse_metadata_block("intro text\n\n" + block + "\ntrailer")
    assert parsed == {
        "git_sha": "abc123def",
        "git_sha_short": "abc123d",
        "git_branch": "feat/foo",
    }


def test_non_git_values_render_na_and_parse_to_none():
    block = cs.render_metadata_block("", "", "")
    assert "n/a" in block
    parsed = cs.parse_metadata_block(block)
    assert parsed == {"git_sha": None, "git_sha_short": None, "git_branch": None}


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
    assert parsed == {
        "git_sha": "realsha1",
        "git_sha_short": "realsh",
        "git_branch": "main",
    }


def test_parse_without_session_metadata_heading_returns_all_none():
    text = "### Key Context\n- git_sha: decoydead\n- git_branch: main\n"
    parsed = cs.parse_metadata_block(text)
    assert parsed == {"git_sha": None, "git_sha_short": None, "git_branch": None}
