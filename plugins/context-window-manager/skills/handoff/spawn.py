#!/usr/bin/env python3
"""Spawn (or reuse) the next cmux workspace after a completed handoff.

CLI:
    spawn.py --resume-command <line> [--stage <suffix>]
             [--rename-timeout <seconds, default 30>]

stdout is one JSON object:
    {"status": "spawned" | "reused" | "skipped" | "needs-choice",
     "name": str | null, "ref": str | null, "reason": str | null,
     "choices": [{"suffix": str, "color": str | null}], "notices": [str]}

The exit code is always 0: a spawn failure never fails the handoff. A failed
cmux call's exit code and stderr (or exception) are added to the reason or
notice it causes.

Optional config at SAVE_ROOT/config.toml switches naming to stage advance:
    [[spawn.stages]]
    suffix = "A-plan"
    color = "blue"        # claude /color; also the sidebar unless `sidebar` is set
    sidebar = "#283593"   # optional cmux sidebar color (#RRGGBB or a cmux name)
Without it, a trailing -N becomes -(N+1) (else -2 is appended) and the
sidebar color is inherited from the caller. SCT_CWM_SPAWN=0 opts out.
Reading config.toml needs Python 3.11+ (tomllib); on an older python3 a
present config skips the spawn with a reason instead of crashing.

The caller's own workspace is never reused or messaged: picking the caller's
current stage creates a new sibling with the same name.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11: load_stages reports it when a config exists
    tomllib = None

COMMON = Path(__file__).resolve().parent.parent.parent / "lib" / "context-window-common"

# Why the most recent cmux call failed (exit code and stderr, or the exception),
# so a skip reason or notice can say more than "failed".
_last_failure = {"detail": ""}


class SpawnConfigError(Exception):
    pass


@dataclass(frozen=True)
class Stage:
    suffix: str
    color: str | None
    sidebar: str | None = None  # cmux sidebar color when its palette lacks `color`


@dataclass(frozen=True)
class Target:
    name: str
    sidebar_color: str | None
    claude_color: str | None


@dataclass(frozen=True)
class NeedsChoice:
    choices: list[Stage]


def load_stages(path: Path) -> list[Stage] | None:
    if not path.exists():
        return None
    if tomllib is None:
        raise SpawnConfigError(f"{path}: reading it needs Python 3.11+ (tomllib)")
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise SpawnConfigError(f"{path}: {e}") from e
    if "spawn" not in data:
        return None
    table = data["spawn"]
    raw = table.get("stages") if isinstance(table, dict) else None
    if not isinstance(raw, list) or not raw:
        raise SpawnConfigError(f"{path}: [[spawn.stages]] must list at least one stage")
    stages = []
    for entry in raw:
        suffix = entry.get("suffix") if isinstance(entry, dict) else None
        if not isinstance(suffix, str) or not suffix:
            raise SpawnConfigError(f"{path}: every stage needs a string suffix")
        colors = [entry.get(k) for k in ("color", "sidebar")]
        if any(c is not None and not isinstance(c, str) for c in colors):
            raise SpawnConfigError(f"{path}: stage {suffix} colors must be strings")
        stages.append(Stage(suffix, *colors))
    return stages


def next_target(
    title: str,
    stages: list[Stage] | None,
    pick: str | None,
    inherited_color: str | None,
) -> Target | NeedsChoice:
    if not stages:
        m = re.search(r"-(\d+)$", title)
        name = f"{title[: m.start()]}-{int(m.group(1)) + 1}" if m else f"{title}-2"
        return Target(name, inherited_color, None)
    matched = next(
        (i for i, s in enumerate(stages) if title.endswith("-" + s.suffix)), None
    )
    base = title[: -len(stages[matched].suffix) - 1] if matched is not None else title
    if pick is not None:
        chosen = next((s for s in stages if s.suffix == pick), None)
        if chosen is None:
            return NeedsChoice(stages)
        return _stage_target(base, chosen)
    if matched is None or matched + 1 == len(stages):
        return NeedsChoice(stages)
    return _stage_target(base, stages[matched + 1])


def _stage_target(base: str, stage: Stage) -> Target:
    return Target(f"{base}-{stage.suffix}", stage.sidebar or stage.color, stage.color)


def build_launch(argv_b64: str | None, name: str) -> str:
    argv = ["claude"]
    try:
        caller = base64.b64decode(argv_b64 or "", validate=True).decode().split("\0")
    except (ValueError, UnicodeDecodeError):
        caller = []
    if caller and Path(caller[0]).name == "claude":
        argv = [caller[0]]
        for flag in ("--model", "--effort"):
            if flag in caller[:-1]:
                argv += [flag, caller[caller.index(flag) + 1]]
    return shlex.join([*argv, f"/rename {name}"])


def _dicts(data: dict, key: str) -> list[dict]:
    items = data.get(key)
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def find_caller(workspaces_json: dict, ws_id: str) -> dict | None:
    for ws in _dicts(workspaces_json, "workspaces"):
        if ws.get("id") == ws_id:
            keys = ("ref", "title", "custom_color", "current_directory")
            return {k: ws.get(k) for k in keys}
    return None


def find_group(groups_json: dict, ws_ref: str) -> dict | None:
    for group in _dicts(groups_json, "groups"):
        members = group.get("member_workspace_refs")
        if isinstance(members, list) and ws_ref in members:
            return group
    return None


def _label(args: tuple[str, ...]) -> str:
    return " ".join(a for a in args[:2] if not a.startswith("-"))


def _why() -> str:
    detail = _last_failure["detail"]
    return f" ({detail})" if detail else ""


def _cmux(*args: str) -> str | None:
    """Run one cmux call; None on any failure so spawn degrades, never raises."""
    _last_failure["detail"] = ""
    try:
        r = subprocess.run(
            ["cmux", *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env={**os.environ, "CMUX_QUIET": "1"},
            check=False,
        )
    except subprocess.TimeoutExpired:
        _last_failure["detail"] = f"cmux {_label(args)} timed out after 10s"
        return None
    except (OSError, subprocess.SubprocessError) as e:
        _last_failure["detail"] = f"cmux {_label(args)}: {type(e).__name__}: {e}"
        return None
    if r.returncode != 0:
        stderr = " ".join((r.stderr or "").split())[:200]
        _last_failure["detail"] = f"cmux {_label(args)} exited {r.returncode}" + (
            f": {stderr}" if stderr else ""
        )
        return None
    return r.stdout


def _cmux_json(*args: str) -> dict | None:
    out = _cmux(*args)
    if out is None:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        _last_failure["detail"] = f"cmux {_label(args)} returned no JSON object"
        return None
    return data


def _send(ref: str, line: str) -> bool:
    # cmux turns the literal two-character `\n` into Enter, as the trio script does.
    return _cmux("send", "--workspace", ref, line + "\\n") is not None


def _wait_for_rename(ref: str, name: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        screen = _cmux("read-screen", "--workspace", ref)
        if screen and f"Session renamed to: {name}" in screen:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _paste(resume_command: str) -> str:
    return f"paste this in the new session by hand: {resume_command}"


def new_result() -> dict:
    return {
        "status": "skipped",
        "name": None,
        "ref": None,
        "reason": None,
        "choices": [],
        "notices": [],
    }


def run(
    resume_command: str,
    stage: str | None,
    rename_timeout: float,
    result: dict,
) -> dict:
    paste = _paste(resume_command)

    def skip(reason: str) -> dict:
        result["reason"] = reason
        return result

    if os.environ.get("SCT_CWM_SPAWN") == "0":
        return skip("SCT_CWM_SPAWN=0")
    ws_id = os.environ.get("CMUX_WORKSPACE_ID")
    if not ws_id:
        return skip("not running inside cmux (no CMUX_WORKSPACE_ID)")
    if shutil.which("cmux") is None:
        return skip("cmux not found on PATH")
    sys.path.insert(0, str(COMMON))
    import context_schema

    try:
        stages = load_stages(context_schema.SAVE_ROOT / "config.toml")
    except SpawnConfigError as e:
        return skip(f"bad spawn config: {e}")

    workspaces = _cmux_json("workspace", "list", "--json")
    if workspaces is None:
        return skip(f"cmux workspace lookup failed{_why()}")
    groups = _cmux_json("workspace-group", "list", "--json")
    if groups is None:
        return skip(f"cmux workspace group lookup failed{_why()}")
    caller = find_caller(workspaces, ws_id)
    if caller is None or not isinstance(caller["ref"], str):
        return skip(f"caller workspace {ws_id} not found in cmux")
    group = find_group(groups, caller["ref"])

    title = caller["title"] if isinstance(caller["title"], str) else ""
    color = caller["custom_color"] if isinstance(caller["custom_color"], str) else None
    target = next_target(title, stages, stage, color)
    if isinstance(target, NeedsChoice):
        result["status"] = "needs-choice"
        result["choices"] = [
            {"suffix": s.suffix, "color": s.color} for s in target.choices
        ]
        return result
    result["name"] = target.name

    # Never reuse, and so never message, the caller itself: picking its own
    # stage from the menu creates a new sibling with the same name instead.
    members = set(group["member_workspace_refs"]) - {caller["ref"]} if group else set()
    existing = next(
        (
            w["ref"]
            for w in _dicts(workspaces, "workspaces")
            if w.get("title") == target.name and w.get("ref") in members
        ),
        None,
    )
    if existing:
        result["ref"] = existing
        if not _send(existing, resume_command):
            result["notices"].append(paste)
            return skip(
                f"could not send to existing {target.name} (prompt busy?){_why()}"
            )
        result["status"] = "reused"
        return result

    create = [
        "workspace",
        "create",
        "--name",
        target.name,
        "--cwd",
        str(Path.cwd()),
        "--command",
        build_launch(os.environ.get("CMUX_AGENT_LAUNCH_ARGV_B64"), target.name),
    ]
    if group:
        create += [
            "--group",
            group["ref"],
            "--group-placement",
            "afterCurrent",
            "--group-reference",
            caller["ref"],
        ]
    created = _cmux_json(*create, "--json")
    ref = created.get("workspace_ref") if created else None
    if not isinstance(ref, str) or not ref:
        return skip(f"cmux workspace create failed{_why()}")
    result["status"], result["ref"] = "spawned", ref

    if target.sidebar_color:
        color_args = ("--action", "set-color", "--color", target.sidebar_color)
        if _cmux("workspace-action", "--workspace", ref, *color_args) is None:
            result["notices"].append(
                f"could not set sidebar color {target.sidebar_color}{_why()}"
            )
    if not _wait_for_rename(ref, target.name, rename_timeout):
        result["notices"].append(
            f"rename not confirmed in {rename_timeout:g}s{_why()}; {paste}"
        )
        return result
    if target.claude_color and not _send(ref, f"/color {target.claude_color}"):
        result["notices"].append(f"could not send /color {target.claude_color}{_why()}")
    if not _send(ref, resume_command):
        result["notices"].append(f"could not send the resume line{_why()}; {paste}")
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--resume-command", required=True)
    p.add_argument("--stage")
    p.add_argument("--rename-timeout", type=float, default=30)
    a = p.parse_args()
    result = new_result()
    try:
        run(a.resume_command, a.stage, a.rename_timeout, result)
    except Exception as e:  # noqa: BLE001 - the exit-0 JSON verdict is the contract
        why = f"unexpected {type(e).__name__}: {e}"
        if result["status"] in ("spawned", "reused"):
            # Keep the created ref so the user can find the new workspace.
            result["notices"].append(f"{why}; {_paste(a.resume_command)}")
        else:
            result["status"], result["reason"] = "skipped", why
    print(json.dumps(result))


if __name__ == "__main__":
    main()
