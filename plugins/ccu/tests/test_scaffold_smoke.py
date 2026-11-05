"""Subprocess-boundary smoke test for the .sct config-dir scaffold.

A fresh subprocess with a clean ``$CLAUDE_CONFIG_DIR`` must actually create the
default config dir on a real run (it seeds nothing: ccu runs on built-in
defaults). Asserting the side effect across the process boundary (not just an
in-process call) follows the subprocess-boundary-smoke pattern: the CLI's own
bootstrap and env handling get exercised end to end.
"""

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


def test_cli_subprocess_creates_config_dir(tmp_path):
    cli = Path(__file__).resolve().parent.parent / "cli" / "ccu_forecast.py"
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path)}
    reset = (dt.datetime.now(ZoneInfo("UTC")) + dt.timedelta(days=2)).isoformat()
    r = subprocess.run(
        [sys.executable, str(cli), "--used", "50", "--reset", reset],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    cfg_dir = tmp_path / ".sct" / "ccu" / "config"
    assert cfg_dir.is_dir()
    # Seeds nothing: no example config is written.
    assert not (cfg_dir / "ccu.example.yaml").exists()
