import json
import time

import pytest
import verify_plugin_cache as vpc

CITED = (
    "/Users/x/.claude/plugins/cache/sandro-claude-tooling-dev/"
    "cobtask-workflow/0.66.0/bin/cobtask-workflow"
)
LINE_41 = f'Run `python3 "{CITED}" run --phase REVIEW`.'


def _installed(tmp_path, plugins):
    p = tmp_path / "installed_plugins.json"
    p.write_text(json.dumps({"version": 2, "plugins": plugins}))
    return p


def _record(version, install_path="/i", scope="user"):
    return {"scope": scope, "installPath": install_path, "version": version}


def _check(text, installed_json):
    return vpc.check_plugin_cache_refs(
        vpc.extract_plugin_cache_refs(text), installed_json=installed_json
    )


def test_superseded_version_flagged(tmp_path):
    ip = str(tmp_path / "cache/sandro-claude-tooling-dev/cobtask-workflow/0.66.1")
    p = _installed(
        tmp_path,
        {"cobtask-workflow@sandro-claude-tooling-dev": [_record("0.66.1", ip)]},
    )
    out = _check(LINE_41, p)
    assert out["stale"] == [
        {
            "path": CITED,
            "cited_version": "0.66.0",
            "installed_version": "0.66.1",
            "installed_path": ip,
        }
    ]
    assert out["notes"] == []


def test_matching_version_not_flagged(tmp_path):
    p = _installed(
        tmp_path,
        {"cobtask-workflow@sandro-claude-tooling-dev": [_record("0.66.0")]},
    )
    assert _check(LINE_41, p) == {"stale": [], "notes": []}


def test_version_matching_any_scope_is_current(tmp_path):
    p = _installed(
        tmp_path,
        {
            "cobtask-workflow@sandro-claude-tooling-dev": [
                _record("0.66.1", scope="user"),
                _record("0.66.0", scope="project"),
            ]
        },
    )
    assert _check(LINE_41, p)["stale"] == []


def test_tilde_form_detected(tmp_path):
    p = _installed(tmp_path, {"p@m": [_record("1.1.0", "/i/1.1.0")]})
    out = _check("see `~/.claude/plugins/cache/m/p/1.0.0/x`", p)
    assert [s["path"] for s in out["stale"]] == ["~/.claude/plugins/cache/m/p/1.0.0/x"]


def test_extract_finds_path_inside_quoted_command():
    refs = vpc.extract_plugin_cache_refs(LINE_41)
    assert [(r["path"], r["version"]) for r in refs] == [(CITED, "0.66.0")]


def test_extract_dedupes_repeated_path():
    refs = vpc.extract_plugin_cache_refs(f"{LINE_41}\nagain: `{CITED}`")
    assert len(refs) == 1


def test_extract_ignores_bare_cache_dir():
    text = "Check every marketplace under `~/.claude/plugins/cache/`."
    assert vpc.extract_plugin_cache_refs(text) == []


def test_extract_ignores_placeholder_template():
    text = "never cite `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/...`"
    assert vpc.extract_plugin_cache_refs(text) == []


def test_single_digit_version_detected():
    refs = vpc.extract_plugin_cache_refs("`~/.claude/plugins/cache/m/p/1/bin/run`")
    assert [r["version"] for r in refs] == ["1"]


@pytest.mark.parametrize("quote", ['"', "'"])
def test_quoted_absolute_path_with_spaces_detected(tmp_path, quote):
    cited = "/Users/First Last/.claude/plugins/cache/m/p/0.66.0/bin/x"
    p = _installed(tmp_path, {"p@m": [_record("0.66.1", "/i/0.66.1")]})
    out = _check(f"Run `python3 {quote}{cited}{quote} run`.", p)
    assert [s["cited_version"] for s in out["stale"]] == ["0.66.0"]


def test_quoted_path_inside_quoted_command_detected(tmp_path):
    cited = "/Users/First Last/.claude/plugins/cache/m/p/0.66.0/bin/x"
    p = _installed(tmp_path, {"p@m": [_record("0.66.1", "/i/0.66.1")]})
    out = _check(f"""Run `bash -lc 'python3 "{cited}" run'`.""", p)
    assert [s["cited_version"] for s in out["stale"]] == ["0.66.0"]


@pytest.mark.parametrize(
    "cited",
    [
        "/Volumes/External Disk/Users/x/.claude/plugins/cache/m/p/1.0.0/bin/x",
        "/home/First Last/user/.claude/plugins/cache/m/p/1.0.0/bin/x",
    ],
)
def test_quoted_ancestor_with_spaces_still_detected(cited):
    refs = vpc.extract_plugin_cache_refs(f'Run `python3 "{cited}" run`.')
    assert [(r["marketplace"], r["plugin"], r["version"]) for r in refs] == [
        ("m", "p", "1.0.0")
    ]


def test_quoted_path_ends_at_first_space_after_version():
    text = (
        'Run `python3 "/Users/x/.claude/plugins/cache/m/p/1.0.0/bin/my script.py" run`.'
    )
    refs = vpc.extract_plugin_cache_refs(text)
    assert [r["path"] for r in refs] == [
        "/Users/x/.claude/plugins/cache/m/p/1.0.0/bin/my"
    ]


def test_home_variable_prefix_detected():
    refs = vpc.extract_plugin_cache_refs(
        "`$HOME/.claude/plugins/cache/m/p/1.0.0/bin/x`"
    )
    assert [(r["plugin"], r["version"]) for r in refs] == [("p", "1.0.0")]


def test_quoted_command_keeps_arguments_out_of_path():
    text = '"/usr/bin/env ~/.claude/plugins/cache/m/p/1.0.0/x --flag"'
    refs = vpc.extract_plugin_cache_refs(text)
    assert [r["path"] for r in refs] == ["~/.claude/plugins/cache/m/p/1.0.0/x"]


# The quoted command starts with the launcher itself, so only the quoted-read
# trimming keeps its arguments out of the reported path.
@pytest.mark.parametrize(
    "text, cited",
    [
        (
            "Run `bash -lc '/Users/x/.claude/plugins/cache/m/p/1.0.0/bin/x run'`.",
            "/Users/x/.claude/plugins/cache/m/p/1.0.0/bin/x",
        ),
        (
            (
                "Run `bash -lc '/Users/x/.claude/plugins/cache/m/p/1.0.0/bin/x"
                " --phase REVIEW'`."
            ),
            "/Users/x/.claude/plugins/cache/m/p/1.0.0/bin/x",
        ),
        (
            'Run `sh -c "~/.claude/plugins/cache/m/p/1.0.0/bin/x run"`.',
            "~/.claude/plugins/cache/m/p/1.0.0/bin/x",
        ),
        (
            'Run "/Users/x/.claude/plugins/cache/m/p/1.0.0/bin/x --phase REVIEW".',
            "/Users/x/.claude/plugins/cache/m/p/1.0.0/bin/x",
        ),
    ],
)
def test_quoted_launcher_command_keeps_arguments_out_of_path(tmp_path, text, cited):
    p = _installed(tmp_path, {"p@m": [_record("2.0.0", "/i/2.0.0")]})
    assert [r["path"] for r in vpc.extract_plugin_cache_refs(text)] == [cited]
    assert [s["path"] for s in _check(text, p)["stale"]] == [cited]


def test_every_ref_in_joined_commands_checked(tmp_path):
    # The first plugin is current; the second, joined by &&, is stale.
    p = _installed(tmp_path, {"p@m": [_record("1.1.0")], "q@m": [_record("1.1.0")]})
    text = (
        "`~/.claude/plugins/cache/m/p/1.1.0/bin/x"
        "&&~/.claude/plugins/cache/m/q/1.0.0/bin/x`"
    )
    out = _check(text, p)
    assert [s["path"] for s in out["stale"]] == [
        "~/.claude/plugins/cache/m/q/1.0.0/bin/x"
    ]
    assert out["notes"] == []


def test_every_ref_in_path_list_checked(tmp_path):
    p = _installed(tmp_path, {"p@m": [_record("1.1.0")], "q@m": [_record("1.1.0")]})
    text = (
        'export PATH="~/.claude/plugins/cache/m/p/1.1.0/bin'
        ':~/.claude/plugins/cache/m/q/1.0.0/bin:$PATH"'
    )
    assert [r["path"] for r in vpc.extract_plugin_cache_refs(text)] == [
        "~/.claude/plugins/cache/m/p/1.1.0/bin",
        "~/.claude/plugins/cache/m/q/1.0.0/bin",
    ]
    assert [s["cited_version"] for s in _check(text, p)["stale"]] == ["1.0.0"]


def test_every_ref_in_quoted_path_list_with_spaces_checked(tmp_path):
    # The spaces truncate both paths; both versions are still checked.
    p = _installed(tmp_path, {"p@m": [_record("1.1.0")], "q@m": [_record("1.1.0")]})
    first = "/Users/First Last/.claude/plugins/cache/m/p/1.1.0/bin"
    second = "/Users/First Last/.claude/plugins/cache/m/q/1.0.0/bin"
    text = f'export PATH="{first}:{second}:$PATH"'
    refs = vpc.extract_plugin_cache_refs(text)
    assert [r["version"] for r in refs] == ["1.1.0", "1.0.0"]
    assert [s["cited_version"] for s in _check(text, p)["stale"]] == ["1.0.0"]


# CPU time, not wall time: a descheduled or paused worker must not fail a
# scanner that is still linear.
def test_extract_is_linear_on_slash_heavy_text():
    # A crafted commit subject of bare slashes once took quadratic time.
    text = "- commits:\n  - abc1234 " + "/" * 50_000 + "\n"
    start = time.process_time()
    assert vpc.extract_plugin_cache_refs(text) == []
    assert time.process_time() - start < 2.0


def test_extract_is_linear_on_marker_heavy_line():
    # Many markers on one quoted line: each scan stops at its neighbours.
    text = '"' + ("a b" + "/.claude/plugins/cache/") * 20_000 + '"'
    start = time.process_time()
    assert vpc.extract_plugin_cache_refs(text) == []
    assert time.process_time() - start < 2.0


def test_uninstalled_plugin_is_note(tmp_path):
    p = _installed(tmp_path, {"other@m": [_record("1.0.0")]})
    out = _check("`~/.claude/plugins/cache/m/p/1.0.0/x`", p)
    assert out["stale"] == []
    assert len(out["notes"]) == 1
    assert "p@m" in out["notes"][0] and "not installed" in out["notes"][0]


def test_missing_installed_json_is_note(tmp_path):
    out = _check(LINE_41, tmp_path / "nope.json")
    assert out["stale"] == []
    assert len(out["notes"]) == 1


@pytest.mark.parametrize(
    "body", ["not json", '{"plugins": []}', '{"plugins": {"p@m": []}}', "[]"]
)
def test_malformed_installed_json_is_note(tmp_path, body):
    p = tmp_path / "installed_plugins.json"
    p.write_text(body)
    out = _check("`~/.claude/plugins/cache/m/p/1.0.0/x`", p)
    assert out["stale"] == []
    assert len(out["notes"]) == 1


@pytest.mark.parametrize(
    "records",
    [
        ["not a dict"],
        [{}],
        [{"version": [], "installPath": "/i"}],
        [{"version": {}, "installPath": "/i"}],
        [{"version": "1.1.0"}],
        [{"version": "1.1.0", "installPath": ""}],
        [{"version": "1.1.0", "installPath": "/i"}, {"version": 1}],
    ],
)
def test_malformed_install_record_is_note(tmp_path, records):
    p = _installed(tmp_path, {"p@m": records})
    out = _check("`~/.claude/plugins/cache/m/p/1.0.0/x`", p)
    assert out["stale"] == []
    assert len(out["notes"]) == 1
    assert "malformed" in out["notes"][0]


def test_no_refs_reads_nothing(tmp_path):
    out = vpc.check_plugin_cache_refs([], installed_json=tmp_path / "nope.json")
    assert out == {"stale": [], "notes": []}
