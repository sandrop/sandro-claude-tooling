"""Version-free launcher tests for the ccu plugin."""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from ccu_launch import main, resolve

DEV = "ccu@sandro-claude-tooling-dev"
PUB = "ccu@sandro-claude-tooling"
PLUGIN = Path(__file__).resolve().parent.parent
PAYLOAD = {
    "rate_limits": {"seven_day": {"used_percentage": 17.0, "resets_at": 1763000000}}
}


def _install(root, version, name="dev"):
    """Create a fake cache install with cli/ccu_tap.py; return its installPath."""
    path = root / "cache" / name / "ccu" / version
    (path / "cli").mkdir(parents=True)
    (path / "cli" / "ccu_tap.py").write_text("")
    return path


def _record(root, plugins):
    f = root / "plugins" / "installed_plugins.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"version": 2, "plugins": plugins}))


def _entry(path, version):
    return [{"installPath": str(path), "version": version, "scope": "user"}]


def _copy_launcher(root):
    """Copy the launcher outside the plugin, as the README setup step does."""
    launcher = root / ".sct" / "ccu" / "ccu_launch.py"
    launcher.parent.mkdir(parents=True)
    shutil.copy(PLUGIN / "cli" / "ccu_launch.py", launcher)
    return launcher


class _UnreadableStdin:
    """A stdin stand-in that fails the test if the launcher reads it."""

    def read(self, *args):
        raise AssertionError("stdin must not be read without a wrapped command")


def test_resolve_picks_highest_version_across_installs(tmp_path):
    _install(tmp_path, "0.5.0")
    pub = _install(tmp_path, "0.6.0", "pub")
    dev = _install(tmp_path, "0.7.0")
    _record(tmp_path, {PUB: _entry(pub, "0.6.0"), DEV: _entry(dev, "0.7.0")})
    assert resolve("ccu_tap.py", tmp_path) == dev / "cli" / "ccu_tap.py"


def test_resolve_newer_public_beats_live_dev(tmp_path):
    # Both installs live; 0.10.0 > 0.9.0 numerically (not lexically), so the
    # newer public install wins over the dev one.
    pub = _install(tmp_path, "0.10.0", "pub")
    dev = _install(tmp_path, "0.9.0")
    _record(tmp_path, {PUB: _entry(pub, "0.10.0"), DEV: _entry(dev, "0.9.0")})
    assert resolve("ccu_tap.py", tmp_path) == pub / "cli" / "ccu_tap.py"


def test_resolve_dev_wins_version_tie(tmp_path):
    pub = _install(tmp_path, "0.7.0", "pub")
    dev = _install(tmp_path, "0.7.0")
    _record(tmp_path, {PUB: _entry(pub, "0.7.0"), DEV: _entry(dev, "0.7.0")})
    assert resolve("ccu_tap.py", tmp_path) == dev / "cli" / "ccu_tap.py"


def test_resolve_skips_pruned_install_path(tmp_path):
    pub = _install(tmp_path, "0.7.0", "pub")
    pruned = tmp_path / "cache" / "dev" / "ccu" / "0.8.0"
    _record(tmp_path, {DEV: _entry(pruned, "0.8.0"), PUB: _entry(pub, "0.7.0")})
    assert resolve("ccu_tap.py", tmp_path) == pub / "cli" / "ccu_tap.py"


def test_resolve_skips_unreadable_install(monkeypatch, tmp_path):
    # The newer dev install raises PermissionError on the target check; the
    # resolver must skip it and still pick the readable public install.
    pub = _install(tmp_path, "0.7.0", "pub")
    dev = _install(tmp_path, "0.8.0")
    blocked = dev / "cli" / "ccu_tap.py"
    real_is_file = Path.is_file

    def _is_file(self):
        if self == blocked:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", _is_file)
    _record(tmp_path, {PUB: _entry(pub, "0.7.0"), DEV: _entry(dev, "0.8.0")})
    assert resolve("ccu_tap.py", tmp_path) == pub / "cli" / "ccu_tap.py"


@pytest.mark.parametrize("version", ["1234567", "1.0", "1.0.0.99"])
def test_resolve_skips_malformed_numeric_version(tmp_path, version):
    # Each malformed version would outrank 0.7.0 under plain dot-split int
    # parsing; only numeric x.y.z is accepted, so the valid release wins.
    pub = _install(tmp_path, "0.7.0", "pub")
    dev = _install(tmp_path, version)
    _record(tmp_path, {PUB: _entry(pub, "0.7.0"), DEV: _entry(dev, version)})
    assert resolve("ccu_tap.py", tmp_path) == pub / "cli" / "ccu_tap.py"


@pytest.mark.parametrize(
    ("case", "script"),
    [
        ("no-json", "ccu_tap.py"),
        ("invalid-json", "ccu_tap.py"),
        ("no-plugins", "ccu_tap.py"),
        ("dict-entry", "ccu_tap.py"),
        ("version-unknown", "ccu_tap.py"),
        ("version-rc", "ccu_tap.py"),
        ("no-ccu-keys", "ccu_tap.py"),
        ("path-separator", "../ccu_tap.py"),
    ],
)
def test_resolve_returns_none_when_unresolvable(tmp_path, case, script):
    dev = _install(tmp_path, "0.7.0")
    f = tmp_path / "plugins" / "installed_plugins.json"
    if case == "invalid-json":
        f.parent.mkdir(parents=True)
        f.write_text("{not json")
    elif case == "no-plugins":
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"version": 2}))
    elif case == "dict-entry":
        _record(tmp_path, {DEV: {"installPath": str(dev), "version": "0.7.0"}})
    elif case == "version-unknown":
        _record(tmp_path, {DEV: _entry(dev, "unknown")})
    elif case == "version-rc":
        _record(tmp_path, {DEV: _entry(dev, "1.0.0-rc1")})
    elif case == "no-ccu-keys":
        _record(tmp_path, {"other@sandro-claude-tooling": _entry(dev, "0.7.0")})
    elif case == "path-separator":
        # A real file one level above cli/, which the launcher must refuse.
        (dev / "ccu_tap.py").write_text("")
        _record(tmp_path, {DEV: _entry(dev, "0.7.0")})
    assert resolve(script, tmp_path) is None


def test_main_unresolved_forwards_stdin_to_wrapped_command(
    monkeypatch, tmp_path, capfd
):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    upper = "import sys; sys.stdout.write(sys.stdin.read().upper()); sys.exit(3)"
    rc = main(
        ["ccu_tap.py", "--", sys.executable, "-c", upper], stdin=io.BytesIO(b"abc")
    )
    assert rc == 3
    out = capfd.readouterr()
    assert out.out == "ABC"
    assert "Traceback" not in out.err


def test_main_unresolved_reports_missing_wrapped_command(monkeypatch, tmp_path, capfd):
    # A wrapped command that cannot start exits 127 with a one-line diagnostic
    # naming the executable, not a traceback or a silent blank status line.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    missing = str(tmp_path / "no-such-command")
    rc = main(["ccu_tap.py", "--", missing], stdin=io.BytesIO(b"abc"))
    assert rc == 127
    out = capfd.readouterr()
    assert out.out == ""
    assert f"ccu_launch: cannot run {missing}" in out.err
    assert "Traceback" not in out.err


@pytest.mark.parametrize("argv", [["ccu_forecast.py"], [], ["ccu_tap.py", "--"]])
def test_main_unresolved_without_wrapper_prints_nothing(
    monkeypatch, tmp_path, capfd, argv
):
    # With no wrapped command the launcher exits at once without touching
    # stdin, so an open or unreadable stdin can neither hang nor crash it.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert main(argv, stdin=_UnreadableStdin()) == 0
    out = capfd.readouterr()
    assert out.out == ""
    assert out.err == ""


def test_launcher_copy_execs_installed_tap(tmp_path):
    launcher = _copy_launcher(tmp_path)
    _record(tmp_path, {DEV: _entry(PLUGIN, "9.9.9")})
    r = subprocess.run(
        [sys.executable, str(launcher), "ccu_tap.py"],
        input=json.dumps(PAYLOAD).encode(),
        capture_output=True,
        check=False,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path)},
    )
    assert r.returncode == 0, r.stderr
    data = json.loads((tmp_path / ".sct" / "ccu" / "usage.json").read_text())
    assert data["rate_limits"] == PAYLOAD["rate_limits"]


def test_launcher_copy_forwards_args_to_resolved_tap(tmp_path):
    # Resolved path: the launcher must exec the tap with every argument after
    # the script name, so the tap records usage and runs the wrapped command
    # with the original stdin, passing its output and exit code through.
    launcher = _copy_launcher(tmp_path)
    _record(tmp_path, {DEV: _entry(PLUGIN, "9.9.9")})
    echo = "import sys; sys.stdout.write(sys.argv[1] + ':' + sys.stdin.read()); sys.exit(5)"
    stdin = json.dumps(PAYLOAD)
    r = subprocess.run(
        [
            sys.executable,
            str(launcher),
            "ccu_tap.py",
            "--",
            sys.executable,
            "-c",
            echo,
            "marker",
        ],
        input=stdin.encode(),
        capture_output=True,
        check=False,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path)},
    )
    assert r.returncode == 5, r.stderr
    assert r.stdout.decode() == "marker:" + stdin
    data = json.loads((tmp_path / ".sct" / "ccu" / "usage.json").read_text())
    assert data["rate_limits"] == PAYLOAD["rate_limits"]
