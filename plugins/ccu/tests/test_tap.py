"""Statusline tap tests for the ccu plugin."""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from ccu_tap import main

PAYLOAD = {
    "rate_limits": {"seven_day": {"used_percentage": 17.0, "resets_at": 1763000000}}
}
FROZEN_NOW = 1763000100.0


def test_tap_records_rate_limits_and_prints_nothing(monkeypatch, tmp_path, capsys):
    import ccu_tap

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(ccu_tap, "time", SimpleNamespace(time=lambda: FROZEN_NOW))
    rc = main([], stdin=io.BytesIO(json.dumps(PAYLOAD).encode()))
    assert rc == 0
    assert capsys.readouterr().out == ""
    data = json.loads((tmp_path / ".sct" / "ccu" / "usage.json").read_text())
    assert data["rate_limits"] == PAYLOAD["rate_limits"]
    assert data["captured_at"] == FROZEN_NOW


def test_tap_write_is_atomic_temp_then_replace(monkeypatch, tmp_path):
    import os

    import ccu_tap

    calls = []
    real = os.replace

    def spy(src, dst):
        calls.append((Path(src), Path(dst)))
        real(src, dst)

    monkeypatch.setattr(ccu_tap.os, "replace", spy)
    tap = tmp_path / "usage.json"
    assert ccu_tap.record(json.dumps(PAYLOAD).encode(), tap)
    [(src, dst)] = calls
    assert src.parent == dst.parent
    assert src != dst
    assert dst == tap


_ECHO = [
    sys.executable,
    "-c",
    "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
]


def test_tap_forwards_stdin_bytes_and_exit_code(monkeypatch, tmp_path, capfd):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    data = json.dumps(PAYLOAD).encode()
    cmd = [
        sys.executable,
        "-c",
        "import sys; d=sys.stdin.buffer.read(); sys.stdout.buffer.write(d[::-1]); sys.exit(3)",
    ]
    rc = main(["--", *cmd], stdin=io.BytesIO(data))
    assert rc == 3
    assert capfd.readouterr().out == data[::-1].decode()
    assert (tmp_path / ".sct" / "ccu" / "usage.json").exists()


def test_tap_records_before_wrapped_command_runs(monkeypatch, tmp_path, capfd):
    # The wrapped command reads the tap file as soon as it starts, so it only
    # sees the current render if the tap recorded before forwarding.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    tap = tmp_path / ".sct" / "ccu" / "usage.json"
    tap.parent.mkdir(parents=True)
    previous = {"seven_day": {"used_percentage": 1.0, "resets_at": 1}}
    tap.write_text(json.dumps({"captured_at": 0, "rate_limits": previous}))
    child = (
        "import json, sys; sys.stdin.buffer.read(); "
        "print(json.load(open(sys.argv[1]))"
        "['rate_limits']['seven_day']['used_percentage'])"
    )
    rc = main(
        ["--", sys.executable, "-c", child, str(tap)],
        stdin=io.BytesIO(json.dumps(PAYLOAD).encode()),
    )
    assert rc == 0
    assert capfd.readouterr().out.strip() == "17.0"


def test_tap_wrapped_command_missing_returns_127(monkeypatch, tmp_path, capfd):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    rc = main(["--", "/nonexistent/ccu-x"], stdin=io.BytesIO(b"{}"))
    err = capfd.readouterr().err
    assert rc == 127
    assert "/nonexistent/ccu-x" in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not json",
        b"[]",
        json.dumps({"model": {}}).encode(),
        json.dumps({"rate_limits": {"five_hour": {"used_percentage": 1}}}).encode(),
        json.dumps({"rate_limits": None}).encode(),
    ],
)
def test_tap_bad_input_keeps_previous_file_and_forwards(
    monkeypatch, tmp_path, capfd, data
):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    tap = tmp_path / ".sct" / "ccu" / "usage.json"
    tap.parent.mkdir(parents=True)
    tap.write_bytes(b'{"previous": true}')
    rc = main(["--", *_ECHO], stdin=io.BytesIO(data))
    assert rc == 0
    assert capfd.readouterr().out == data.decode()
    assert tap.read_bytes() == b'{"previous": true}'
    assert list(tap.parent.iterdir()) == [tap]  # no temp file left behind


def test_tap_unwritable_dir_still_forwards(monkeypatch, tmp_path, capfd):
    (tmp_path / ".sct").write_text("a file, not a dir")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    data = json.dumps(PAYLOAD).encode()
    rc = main(["--", *_ECHO], stdin=io.BytesIO(data))
    assert rc == 0
    assert capfd.readouterr().out == data.decode()


def test_tap_bad_args_print_usage_and_exit_0(monkeypatch, tmp_path, capfd):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    rc = main(["npx"], stdin=io.BytesIO(json.dumps(PAYLOAD).encode()))
    out, err = capfd.readouterr()
    assert rc == 0
    assert out == ""
    assert "usage:" in err


def test_tap_failed_replace_leaves_no_temp_file(monkeypatch, tmp_path):
    import ccu_tap

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(ccu_tap.os, "replace", boom)
    assert not ccu_tap.record(json.dumps(PAYLOAD).encode(), tmp_path / "usage.json")
    assert list(tmp_path.iterdir()) == []


def test_tap_script_smoke_in_fresh_subprocess(tmp_path):
    import subprocess

    script = Path(__file__).resolve().parent.parent / "cli" / "ccu_tap.py"
    data = json.dumps(PAYLOAD).encode()
    proc = subprocess.run(
        [sys.executable, str(script), "--", *_ECHO],
        input=data,
        capture_output=True,
        env={"CLAUDE_CONFIG_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == data
    tap = json.loads((tmp_path / ".sct" / "ccu" / "usage.json").read_text())
    assert tap["rate_limits"] == PAYLOAD["rate_limits"]
