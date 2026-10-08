"""Tinker itself must still start: catches changes that break imports between modules."""

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
