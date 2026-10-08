"""Tinker itself must still start: catches changes that break imports between modules."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_main_imports_and_shows_help():
    result = subprocess.run([sys.executable, str(ROOT / "main.py"), "--help"], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert "Tinker" in result.stdout


def test_config_loads():
    sys.path.insert(0, str(ROOT))
    from config import load_config

    config = load_config(ROOT / "config.json")
    assert config.default_mode in config.modes


def start_repl(tmp_path, *args):
    config = tmp_path / "config.json"
    settings = json.loads((ROOT / "config.json").read_text())
    settings["sessions_dir"] = str(tmp_path / "sessions")
    config.write_text(json.dumps(settings))
    return subprocess.run([sys.executable, str(ROOT / "main.py"), str(tmp_path), "--config", str(config), *args],
                          input="exit\n", capture_output=True, text=True, cwd=ROOT)


def test_interactive_session_never_starts_with_auto_approval(tmp_path):
    for mode in ([], ["--mode", "plan"], ["--mode", "edit"]):
        result = start_repl(tmp_path, *mode)
        assert result.returncode == 0, result.stderr
        assert "Auto-approval of file changes and commands is off." in result.stdout, mode
