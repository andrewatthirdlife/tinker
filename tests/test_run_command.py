"""The run_command tool: which commands may run, approval, output, and the sandbox around them."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import CommandSettings  # noqa: E402
from permissions import Mode  # noqa: E402
from tools import Tools  # noqa: E402

# These tests start sandboxes, which can't be done from inside one (e.g. when Tinker runs the tests).
pytestmark = pytest.mark.skipif("TINKER_SANDBOXED" in os.environ, reason="can't start a sandbox inside a sandbox")

RUN = ["python3 -c*"]


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    return tmp_path


def make_tools(root, write=("**",), run=RUN, approve=None, **settings):
    asked = []

    def confirm(command):
        asked.append(command)
        return approve

    mode = Mode(name="test", description="d", write=list(write), run=list(run))
    return Tools(root, 100_000, lambda p, d: None, [], mode, confirm, CommandSettings(**settings)), asked


def test_runs_an_allowed_command(workspace):
    tools, asked = make_tools(workspace)
    out = tools.run("run_command", {"command": "python3 -c 'print(6 * 7)'"})
    assert out.startswith("$ python3 -c print(6 * 7)\nexit code 0")
    assert "--- stdout ---\n42\n" in out
    assert asked == ["python3 -c print(6 * 7)"]
    assert tools.log.commands == ["python3 -c print(6 * 7) (exit code 0)"]
    assert "ran python3 -c print(6 * 7) (exit code 0)" in tools.log.summary()


def test_refuses_commands_the_mode_does_not_allow(workspace):
    tools, asked = make_tools(workspace)
    out = tools.run("run_command", {"command": "rm -rf ."})
    assert out == "Error: 'rm -rf .' is not allowed in test mode. You can run commands matching: python3 -c*."
    assert asked == []
    assert (workspace / "calc.py").exists()


def test_mode_without_commands(workspace):
    tools, _ = make_tools(workspace, run=())
    assert "You cannot run commands." in tools.run("run_command", {"command": "python3 -c 'print(1)'"})


def test_rejected_command_does_not_run(workspace):
    tools, _ = make_tools(workspace, approve="The user rejected this command.")
    out = tools.run("run_command", {"command": "python3 -c 'open(\"marker\", \"w\")'"})
    assert out == "Error: The user rejected this command."
    assert not (workspace / "marker").exists()
    assert tools.log.rejected == 1 and tools.log.commands == []


def test_unparseable_and_empty_commands(workspace):
    tools, _ = make_tools(workspace)
    assert tools.run("run_command", {"command": "python3 -c 'unclosed"}).startswith("Error: could not parse the command")
    assert tools.run("run_command", {"command": "   "}) == "Error: the command is empty"


def test_no_shell(workspace):
    # Without a shell, "&&" and ";" are just arguments: they can't start a second command.
    tools, _ = make_tools(workspace)
    out = tools.run("run_command", {"command": "python3 -c 'import sys; print(sys.argv[1:])' && touch marker"})
    assert "['&&', 'touch', 'marker']" in out
    assert not (workspace / "marker").exists()


def test_failing_command_reports_exit_code_and_stderr(workspace):
    tools, _ = make_tools(workspace)
    out = tools.run("run_command", {"command": "python3 -c 'raise SystemExit(\"boom\")'"})
    assert "exit code 1" in out
    assert "--- stderr ---\nboom" in out


def test_timeout(workspace):
    tools, _ = make_tools(workspace, timeout_seconds=1)
    out = tools.run("run_command", {"command": "python3 -c 'import time; time.sleep(30)'"})
    assert "timed out after 1s" in out
    assert tools.log.commands == ["python3 -c import time; time.sleep(30) (timed out after 1s)"]


def test_long_output_keeps_start_and_end(workspace):
    tools, _ = make_tools(workspace, max_output_chars=200)
    out = tools.run("run_command", {"command": "python3 -c 'print(\"START\" + \"x\" * 5000 + \"END\")'"})
    assert "START" in out and "END" in out
    assert "characters left out" in out
    assert len(out) < 600


def test_sandbox_applies_to_the_tool(workspace):
    tools, _ = make_tools(workspace, write=())
    out = tools.run("run_command", {"command": "python3 -c 'open(\"calc.py\", \"a\").write(\"x\")'"})
    assert "PermissionError" in out
    assert (workspace / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
