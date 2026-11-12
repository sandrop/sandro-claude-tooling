import base64
import importlib.util
import json
import pathlib
import shlex
import sys
from datetime import datetime

import context_schema
import pytest
import retention

# Load spawn.py by explicit path: the conftest keeps this skill dir off
# sys.path so the two skills' validator.py files never collide.
_SPAWN_PATH = pathlib.Path(__file__).parent / "spawn.py"
_spec = importlib.util.spec_from_file_location("handoff_spawn", _SPAWN_PATH)
spawn = importlib.util.module_from_spec(_spec)
sys.modules["handoff_spawn"] = spawn  # dataclasses resolve their module by name
_spec.loader.exec_module(spawn)

STAGES = [
    spawn.Stage("A-plan", "blue"),
    spawn.Stage("B-implement", "yellow"),
    spawn.Stage("C-review", "green"),
]


def test_stage_advance_swaps_suffix_and_takes_stage_color():
    assert spawn.next_target("task42-A-plan", STAGES, None, "#283593") == spawn.Target(
        "task42-B-implement", "yellow", "yellow"
    )


def test_last_stage_needs_choice_with_all_stages():
    assert spawn.next_target("task42-C-review", STAGES, None, None) == (
        spawn.NeedsChoice(STAGES)
    )


def test_stage_miss_needs_choice():
    assert spawn.next_target("Group 5", STAGES, None, None) == spawn.NeedsChoice(STAGES)


def test_pick_on_miss_appends_stage_suffix():
    assert spawn.next_target("Group 5", STAGES, "B-implement", None) == spawn.Target(
        "Group 5-B-implement", "yellow", "yellow"
    )


def test_pick_on_last_stage_swaps_suffix():
    assert spawn.next_target("task42-C-review", STAGES, "A-plan", None) == (
        spawn.Target("task42-A-plan", "blue", "blue")
    )


def test_no_stages_appends_2_and_inherits_sidebar_color():
    assert spawn.next_target("task42-A-plan", None, None, "#283593") == (
        spawn.Target("task42-A-plan-2", "#283593", None)
    )


def test_no_stages_increments_trailing_number():
    assert spawn.next_target("x-2", None, None, None) == spawn.Target("x-3", None, None)


STAGES_TOML = """
[[spawn.stages]]
suffix = "A-plan"
color = "blue"
[[spawn.stages]]
suffix = "B-implement"
color = "yellow"
[[spawn.stages]]
suffix = "C-review"
color = "green"
"""


def test_load_stages_absent_file_is_none(tmp_path):
    assert spawn.load_stages(tmp_path / "config.toml") is None


def test_load_stages_reads_stages_in_order(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(STAGES_TOML)
    assert spawn.load_stages(cfg) == STAGES


def test_load_stages_invalid_toml_names_path(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[[spawn.stages]\nsuffix = ")
    with pytest.raises(spawn.SpawnConfigError, match=str(cfg)):
        spawn.load_stages(cfg)


def test_load_stages_stage_without_suffix_names_path(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[[spawn.stages]]\ncolor = "blue"\n')
    with pytest.raises(spawn.SpawnConfigError, match=str(cfg)):
        spawn.load_stages(cfg)


def test_config_survives_retention_prune(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(STAGES_TOML)
    stale = tmp_path / "repo-deadbeef" / "CONTEXT-2026-07-01-00-00-00-aaaaaa.md"
    stale.parent.mkdir()
    stale.write_text("x")
    retention.prune(tmp_path, now=datetime(2026, 11, 11), days=1)  # noqa: DTZ001 - naive local clock under test
    assert not stale.exists()
    assert cfg.read_text() == STAGES_TOML


def test_load_stages_without_spawn_table_is_none(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('other = "x"\n')
    assert spawn.load_stages(cfg) is None


def test_load_stages_non_table_spawn_names_path(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("spawn = 1\n")
    with pytest.raises(spawn.SpawnConfigError, match=str(cfg)):
        spawn.load_stages(cfg)


def _b64(argv):
    return base64.b64encode("\0".join(argv).encode()).decode()


CALLER_ARGV = [
    "/opt/homebrew/bin/claude",
    "--model",
    "claude-opus-5-5[1m]",
    "--effort",
    "high",
    "/rename task42-A-plan",
]


def test_build_launch_keeps_model_and_effort():
    assert spawn.build_launch(_b64(CALLER_ARGV), "task42-B-implement") == (
        "/opt/homebrew/bin/claude --model 'claude-opus-5-5[1m]' --effort high "
        "'/rename task42-B-implement'"
    )


def test_build_launch_drops_other_caller_flags():
    argv = ["claude", "--resume", "abc", "--model", "m", "--verbose"]
    assert spawn.build_launch(_b64(argv), "n") == "claude --model m '/rename n'"


def test_build_launch_quotes_hostile_name():
    name = "it's $HOME now"
    launch = spawn.build_launch(_b64(CALLER_ARGV), name)
    assert shlex.split(launch)[-1] == f"/rename {name}"


def test_build_launch_falls_back_without_argv():
    assert spawn.build_launch(None, "n") == "claude '/rename n'"


def test_build_launch_falls_back_on_bad_base64():
    assert spawn.build_launch("!!not base64!!", "n") == "claude '/rename n'"


def test_build_launch_falls_back_on_non_claude_executable():
    assert spawn.build_launch(_b64(["codex", "--model", "x"]), "n") == (
        "claude '/rename n'"
    )


# Trimmed from real `cmux workspace list --json` / `workspace-group list --json`.
WORKSPACES = {
    "window_ref": "window:1",
    "workspaces": [
        {
            "id": "741DF5C3-496E-4847-99EF-F849CDCCB0EA",
            "ref": "workspace:1000130000",
            "title": "Group 5",
            "custom_color": None,
            "current_directory": "/home/u/aoc",
        },
        {
            "id": "513AD23D-2E08-4649-8E4C-41905E219D52",
            "ref": "workspace:1000130213",
            "title": "task42-A-plan",
            "custom_color": "#283593",
            "current_directory": "/home/u/sct",
            "pinned": False,
        },
    ],
}
GROUPS = {
    "window_ref": "window:1",
    "groups": [
        {
            "ref": "workspace_group:1000130000",
            "name": "ADHOC",
            "member_workspace_refs": ["workspace:1000130000"],
        },
        {
            "ref": "workspace_group:1000130051",
            "name": "task42",
            "member_workspace_refs": ["workspace:1000130214", "workspace:1000130213"],
        },
    ],
}


def test_find_caller_matches_id():
    assert spawn.find_caller(WORKSPACES, "513AD23D-2E08-4649-8E4C-41905E219D52") == {
        "ref": "workspace:1000130213",
        "title": "task42-A-plan",
        "custom_color": "#283593",
        "current_directory": "/home/u/sct",
    }


def test_find_caller_unknown_id_is_none():
    assert spawn.find_caller(WORKSPACES, "nope") is None


def test_find_group_holds_ref():
    assert spawn.find_group(GROUPS, "workspace:1000130213")["name"] == "task42"


def test_find_group_ungrouped_is_none():
    assert spawn.find_group(GROUPS, "workspace:42") is None


def test_load_stages_reads_optional_sidebar(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[spawn.stages]]\nsuffix = "B"\ncolor = "yellow"\nsidebar = "#7D6608"\n'
    )
    assert spawn.load_stages(cfg) == [spawn.Stage("B", "yellow", "#7D6608")]


def test_load_stages_non_string_sidebar_names_path(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[[spawn.stages]]\nsuffix = "B"\nsidebar = 7\n')
    with pytest.raises(spawn.SpawnConfigError, match=str(cfg)):
        spawn.load_stages(cfg)


def test_stage_sidebar_overrides_color_for_cmux_only():
    stages = [
        spawn.Stage("A-plan", "blue"),
        spawn.Stage("B-implement", "yellow", "#7D6608"),
    ]
    assert spawn.next_target("t-A-plan", stages, None, None) == spawn.Target(
        "t-B-implement", "#7D6608", "yellow"
    )


def _main(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["spawn.py", "--resume-command", "RESUME"])
    spawn.main()
    return json.loads(capsys.readouterr().out)


def test_main_turns_an_exception_before_create_into_skipped_json(monkeypatch, capsys):
    def boom(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(spawn, "run", boom)
    out = _main(monkeypatch, capsys)
    assert (out["status"], out["ref"]) == ("skipped", None)
    assert out["reason"] == "unexpected RuntimeError: boom"


def test_main_keeps_the_created_ref_when_an_exception_follows(monkeypatch, capsys):
    def boom(_resume, _stage, _timeout, result):
        result["status"], result["ref"] = "spawned", "workspace:9"
        raise RuntimeError("boom")

    monkeypatch.setattr(spawn, "run", boom)
    out = _main(monkeypatch, capsys)
    assert (out["status"], out["ref"], out["reason"]) == (
        "spawned",
        "workspace:9",
        None,
    )
    assert out["notices"] == [
        "unexpected RuntimeError: boom; paste this in the new session by hand: RESUME"
    ]


@pytest.fixture
def no_tomllib(monkeypatch, tmp_path):
    """spawn.py loaded fresh with `import tomllib` failing, as on Python < 3.11,
    and set up to run inside a fake cmux session with SAVE_ROOT at tmp_path."""
    monkeypatch.setitem(sys.modules, "tomllib", None)
    spec = importlib.util.spec_from_file_location("handoff_spawn_no_toml", _SPAWN_PATH)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "handoff_spawn_no_toml", mod)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(sys, "path", [*sys.path])  # run() prepends the lib dir
    monkeypatch.setattr(context_schema, "SAVE_ROOT", tmp_path)
    monkeypatch.delenv("SCT_CWM_SPAWN", raising=False)
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws")
    monkeypatch.setattr(mod.shutil, "which", lambda _name: "/fake/cmux")
    return mod


def test_without_tomllib_absent_config_keeps_default_naming(no_tomllib, monkeypatch):
    calls = []

    def fake_json(*args):
        calls.append(args)
        if args[:2] == ("workspace", "list"):
            caller = {"id": "ws", "ref": "workspace:5", "title": "t-A-plan"}
            return {"workspaces": [caller]}
        if args[:2] == ("workspace-group", "list"):
            return {"groups": []}
        return None

    monkeypatch.setattr(no_tomllib, "_cmux_json", fake_json)
    assert no_tomllib.tomllib is None
    result = no_tomllib.run("RESUME", None, 1, no_tomllib.new_result())
    assert result["name"] == "t-A-plan-2"
    assert calls[-1][:2] == ("workspace", "create")


def test_without_tomllib_present_config_skips_naming_path(no_tomllib, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(STAGES_TOML)
    result = no_tomllib.run("RESUME", None, 1, no_tomllib.new_result())
    assert result["status"] == "skipped"
    assert result["reason"] == (
        f"bad spawn config: {cfg}: reading it needs Python 3.11+ (tomllib)"
    )
