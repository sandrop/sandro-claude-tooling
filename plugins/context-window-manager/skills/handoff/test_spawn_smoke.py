"""Fresh-subprocess smoke for the handoff spawn seam.

Runs the real spawn.py as a subprocess against a fake `cmux` on PATH that logs
every argv it receives, then asserts the exact call sequence the fake saw (the
subprocess-boundary-smoke pattern). The fake serves canned JSON for the two
reads and a canned rename confirmation for read-screen.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

SPAWN = str(pathlib.Path(__file__).resolve().parent / "spawn.py")
CALLER_ID = "513AD23D-2E08-4649-8E4C-41905E219D52"
RESUME = "/context-window-manager:resume --context-file /x/CONTEXT-a.md and continue"
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
READS = (["workspace", "list", "--json"], ["workspace-group", "list", "--json"])

FAKE_CMUX = """#!{python}
import json, os, pathlib, sys
d = pathlib.Path(os.environ["FAKE_CMUX_DIR"])
args = sys.argv[1:]
log = d / "calls.jsonl"
with open(log, "a") as f:
    f.write(json.dumps(args) + "\\n")


def rc(name):
    p = d / name
    return int(p.read_text()) if p.exists() else 0


if args[:2] == ["workspace", "list"]:
    print((d / "workspaces.json").read_text())
elif args[:2] == ["workspace-group", "list"]:
    print((d / "groups.json").read_text())
elif args[:2] == ["workspace", "create"]:
    print(json.dumps({{"workspace_ref": "workspace:9"}}))
elif args[0] == "workspace-action":
    sys.exit(rc("action_rc"))
elif args[0] == "read-screen":
    # The confirmation shows only after `screen_delay` polls have come back empty.
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    polls = sum(c[0] == "read-screen" for c in calls)
    screen = d / "screen.txt"
    shown = screen.exists() and polls > rc("screen_delay")
    print(screen.read_text() if shown else "")
elif args[0] == "send":
    sys.exit(rc("send_rc"))
"""


def _raising(target, when="True"):
    """`python -c` code that makes `module.attr` raise RuntimeError while `when`
    holds, then runs spawn.py as __main__, so main()'s last-resort handler is
    exercised across the real process boundary."""
    module, attr = target.rsplit(".", 1)
    return (
        f"import runpy, sys, {module}\n"
        f"_real = {module}.{attr}\n"
        "def _raise(*args, **kwargs):\n"
        f"    if {when}:\n"
        "        raise RuntimeError('injected')\n"
        "    return _real(*args, **kwargs)\n"
        f"{module}.{attr} = _raise\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )


def _workspaces(*extra, title="task42-A-plan", color="#283593"):
    caller = {
        "id": CALLER_ID,
        "ref": "workspace:5",
        "title": title,
        "custom_color": color,
        "current_directory": "/elsewhere",
    }
    return {"window_ref": "window:1", "workspaces": [caller, *extra]}


def _groups(members=("workspace:5",)):
    group = {
        "ref": "workspace_group:7",
        "name": "task42",
        "member_workspace_refs": list(members),
    }
    return {"window_ref": "window:1", "groups": [group]}


@pytest.fixture
def fake(tmp_path):
    """A fake cmux on PATH plus a save root; returns a runner."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cmux = bin_dir / "cmux"
    cmux.write_text(FAKE_CMUX.format(python=sys.executable))
    cmux.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    save_root = tmp_path / "save"
    save_root.mkdir()
    workdir = tmp_path / "work dir"
    workdir.mkdir()

    class Fake:
        dir = state
        root = save_root
        cwd = workdir

        def setup(self, workspaces, groups, config=STAGES_TOML, screen=None):
            (state / "workspaces.json").write_text(json.dumps(workspaces))
            (state / "groups.json").write_text(json.dumps(groups))
            if config is not None:
                (save_root / "config.toml").write_text(config)
            if screen is not None:
                (state / "screen.txt").write_text(screen)

        def run(self, *args, env=None, path=None, code=None):
            base = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith("CMUX_") and k != "SCT_CWM_SPAWN"
            }
            full_env = {
                **base,
                "PATH": path or f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "FAKE_CMUX_DIR": str(state),
                "CWM_SAVE_ROOT": str(save_root),
                "CMUX_WORKSPACE_ID": CALLER_ID,
                **(env or {}),
            }
            full_env = {k: v for k, v in full_env.items() if v is not None}
            prefix = ["-c", code] if code else []
            r = subprocess.run(
                [sys.executable, *prefix, SPAWN, "--resume-command", RESUME, *args],
                cwd=workdir,
                capture_output=True,
                text=True,
                env=full_env,
                check=False,
            )
            assert r.returncode == 0, r.stderr
            return json.loads(r.stdout)

        def calls(self):
            log = state / "calls.jsonl"
            if not log.exists():
                return []
            return [json.loads(line) for line in log.read_text().splitlines()]

        def mutating(self):
            return [
                c for c in self.calls() if c[:3] not in READS and c[0] != "read-screen"
            ]

    return Fake()


def _create(name, cwd, group=True):
    call = [
        "workspace",
        "create",
        "--name",
        name,
        "--cwd",
        str(cwd),
        "--command",
        f"claude '/rename {name}'",
    ]
    if group:
        call += [
            "--group",
            "workspace_group:7",
            "--group-placement",
            "afterCurrent",
            "--group-reference",
            "workspace:5",
        ]
    return [*call, "--json"]


def test_create_path_exact_call_sequence(fake):
    fake.setup(
        _workspaces(), _groups(), screen="Session renamed to: task42-B-implement"
    )
    out = fake.run()
    assert out["status"] == "spawned"
    assert (out["name"], out["ref"]) == ("task42-B-implement", "workspace:9")
    assert fake.calls() == [
        *READS,
        _create("task42-B-implement", fake.cwd),
        [
            "workspace-action",
            "--workspace",
            "workspace:9",
            "--action",
            "set-color",
            "--color",
            "yellow",
        ],
        ["read-screen", "--workspace", "workspace:9"],
        ["send", "--workspace", "workspace:9", "/color yellow\\n"],
        ["send", "--workspace", "workspace:9", RESUME + "\\n"],
    ]


def test_no_config_increments_name_inherits_color_and_skips_slash_color(fake):
    fake.setup(
        _workspaces(),
        _groups(),
        config=None,
        screen="Session renamed to: task42-A-plan-2",
    )
    out = fake.run()
    assert out["status"] == "spawned"
    assert fake.mutating() == [
        _create("task42-A-plan-2", fake.cwd),
        [
            "workspace-action",
            "--workspace",
            "workspace:9",
            "--action",
            "set-color",
            "--color",
            "#283593",
        ],
        ["send", "--workspace", "workspace:9", RESUME + "\\n"],
    ]


def test_reuse_path_only_sends_resume_line(fake):
    existing = {
        "id": "X",
        "ref": "workspace:6",
        "title": "task42-B-implement",
        "custom_color": "#7D6608",
        "current_directory": "/elsewhere",
    }
    fake.setup(_workspaces(existing), _groups(("workspace:5", "workspace:6")))
    out = fake.run()
    assert (out["status"], out["ref"]) == ("reused", "workspace:6")
    assert fake.mutating() == [["send", "--workspace", "workspace:6", RESUME + "\\n"]]


def test_same_name_outside_callers_group_is_not_reused(fake):
    outsider = {
        "id": "X",
        "ref": "workspace:6",
        "title": "task42-B-implement",
        "custom_color": None,
        "current_directory": "/elsewhere",
    }
    fake.setup(
        _workspaces(outsider),
        _groups(),
        screen="Session renamed to: task42-B-implement",
    )
    out = fake.run()
    assert (out["status"], out["ref"]) == ("spawned", "workspace:9")
    assert fake.mutating()[0] == _create("task42-B-implement", fake.cwd)
    assert all("workspace:6" not in c for c in fake.calls())


def test_caller_in_no_group_creates_without_group_flags(fake):
    fake.setup(
        _workspaces(),
        _groups(members=()),
        screen="Session renamed to: task42-B-implement",
    )
    assert fake.run()["status"] == "spawned"
    assert fake.mutating()[0] == _create("task42-B-implement", fake.cwd, group=False)


def test_rename_never_confirmed_skips_sends_with_notice(fake):
    fake.setup(_workspaces(), _groups())
    out = fake.run("--rename-timeout", "1")
    assert out["status"] == "spawned"
    assert not [c for c in fake.calls() if c[0] == "send"]
    assert any(RESUME in n for n in out["notices"])


@pytest.mark.parametrize(
    "case", ["no-workspace-id", "opt-out", "no-cmux", "bad-config", "send-fails"]
)
def test_skips_make_no_mutating_call(fake, tmp_path, case):
    existing = {
        "id": "X",
        "ref": "workspace:6",
        "title": "task42-B-implement",
        "custom_color": None,
        "current_directory": "/elsewhere",
    }
    fake.setup(
        _workspaces(existing),
        _groups(("workspace:5", "workspace:6")),
        config="[[spawn.stages]\n" if case == "bad-config" else STAGES_TOML,
    )
    env, path = {}, None
    if case == "no-workspace-id":
        env = {"CMUX_WORKSPACE_ID": None}
    elif case == "opt-out":
        env = {"SCT_CWM_SPAWN": "0"}
    elif case == "no-cmux":
        empty = tmp_path / "empty"
        empty.mkdir()
        path = str(empty)
    elif case == "send-fails":
        (fake.dir / "send_rc").write_text("1")
    out = fake.run(env=env, path=path)
    assert out["status"] == "skipped"
    assert out["reason"]
    if case == "bad-config":
        assert str(fake.root / "config.toml") in out["reason"]
    if case == "send-fails":
        assert any(RESUME in n for n in out["notices"])
    else:
        assert fake.mutating() == []


def test_needs_choice_then_stage_pick_creates(fake):
    fake.setup(
        _workspaces(title="task42-C-review"),
        _groups(),
        screen="Session renamed to: task42-A-plan",
    )
    out = fake.run()
    assert out["status"] == "needs-choice"
    assert [c["suffix"] for c in out["choices"]] == [
        "A-plan",
        "B-implement",
        "C-review",
    ]
    assert out["choices"][0] == {"suffix": "A-plan", "color": "blue"}
    assert fake.mutating() == []
    out = fake.run("--stage", "A-plan")
    assert (out["status"], out["name"]) == ("spawned", "task42-A-plan")
    assert fake.mutating()[0] == _create("task42-A-plan", fake.cwd)


def test_picking_callers_own_stage_creates_sibling_never_messages_caller(fake):
    fake.setup(
        _workspaces(title="task42-C-review"),
        _groups(),
        screen="Session renamed to: task42-C-review",
    )
    out = fake.run("--stage", "C-review")
    assert (out["status"], out["ref"]) == ("spawned", "workspace:9")
    assert fake.mutating()[0] == _create("task42-C-review", fake.cwd)
    assert all(c[1:3] != ["--workspace", "workspace:5"] for c in fake.calls())


def test_rename_confirmed_after_empty_polls_then_sends(fake):
    fake.setup(
        _workspaces(), _groups(), screen="Session renamed to: task42-B-implement"
    )
    (fake.dir / "screen_delay").write_text("2")
    out = fake.run()
    assert (out["status"], out["notices"]) == ("spawned", [])
    poll = ["read-screen", "--workspace", "workspace:9"]
    assert fake.calls()[4:] == [
        poll,
        poll,
        poll,
        ["send", "--workspace", "workspace:9", "/color yellow\\n"],
        ["send", "--workspace", "workspace:9", RESUME + "\\n"],
    ]


def test_rejected_sidebar_color_is_a_notice_and_spawn_continues(fake):
    fake.setup(
        _workspaces(), _groups(), screen="Session renamed to: task42-B-implement"
    )
    (fake.dir / "action_rc").write_text("1")
    out = fake.run()
    assert (out["status"], out["ref"]) == ("spawned", "workspace:9")
    assert any("could not set sidebar color yellow" in n for n in out["notices"])
    assert fake.mutating()[-2:] == [
        ["send", "--workspace", "workspace:9", "/color yellow\\n"],
        ["send", "--workspace", "workspace:9", RESUME + "\\n"],
    ]


def test_failed_sends_after_create_keep_ref_and_give_paste_line(fake):
    fake.setup(
        _workspaces(), _groups(), screen="Session renamed to: task42-B-implement"
    )
    (fake.dir / "send_rc").write_text("1")
    out = fake.run()
    assert (out["status"], out["ref"]) == ("spawned", "workspace:9")
    assert [c for c in fake.calls() if c[0] == "send"] == [
        ["send", "--workspace", "workspace:9", "/color yellow\\n"],
        ["send", "--workspace", "workspace:9", RESUME + "\\n"],
    ]
    assert any("could not send /color yellow" in n for n in out["notices"])
    assert any("resume line" in n and RESUME in n for n in out["notices"])


def test_unexpected_error_before_create_is_skipped_json(fake):
    fake.setup(_workspaces(), _groups())
    out = fake.run(code=_raising("shutil.which"))
    assert (out["status"], out["ref"]) == ("skipped", None)
    assert out["reason"] == "unexpected RuntimeError: injected"
    assert fake.calls() == []


def test_unexpected_error_after_create_keeps_ref_and_paste_line(fake):
    fake.setup(_workspaces(), _groups())
    # Only the rename poll sleeps 0.5s; subprocess's own waits are shorter.
    out = fake.run(code=_raising("time.sleep", "args == (0.5,)"))
    assert (out["status"], out["ref"]) == ("spawned", "workspace:9")
    assert out["notices"] == [
        (
            "unexpected RuntimeError: injected; "
            f"paste this in the new session by hand: {RESUME}"
        )
    ]
    assert not [c for c in fake.calls() if c[0] == "send"]
