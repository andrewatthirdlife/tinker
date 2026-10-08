"""When the last command in a request failed, the model is told before its answer is accepted."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import FAILED_COMMAND_REMINDER, Agent  # noqa: E402
from config import Config  # noqa: E402
from session import Session  # noqa: E402

# run_command starts a sandbox, which can't be done from inside one.
pytestmark = pytest.mark.skipif("TINKER_SANDBOXED" in os.environ, reason="can't start a sandbox inside a sandbox")


def reply(content="", command=None):
    calls = [{"function": {"name": "run_command", "arguments": {"command": command}}}] if command else []
    tool_calls = [type("Call", (), {"model_dump": lambda self, c=c: c})() for c in calls] or None
    return type("Response", (), {"message": type("Message", (), {"content": content, "tool_calls": tool_calls})()})()


def run(tmp_path, script):
    config = Config(
        host="h", model="m", num_ctx=65536, temperature=0, max_iterations=10, max_tool_output_chars=20000,
        sessions_dir=str(tmp_path / "sessions"), lint_args=[], default_mode="edit",
        modes={"edit": {"description": "d", "write": ["**"], "run": ["python3 -c*"]}},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    notices = []
    agent = Agent(config, Session.create(config.sessions_dir, workspace), lambda n, a: None, notices.append,
                  lambda p, d: None, lambda c: None)
    agent.client = type("Client", (), {"chat": lambda self, **kw: script.pop(0)})()
    return agent, agent.ask("run it"), notices


FAIL, PASS = "python3 -c 'raise SystemExit(3)'", "python3 -c 'print(1)'"


def test_failed_last_command_is_pointed_out_once(tmp_path):
    agent, answer, notices = run(tmp_path, [reply(command=FAIL), reply("All done!"), reply("It still fails with exit code 3.")])
    assert answer == "It still fails with exit code 3."
    reminders = [m for m in agent.messages if m["role"] == "user" and m["content"].startswith("[Note from the agent, not the user. The last")]
    assert len(reminders) == 1
    assert reminders[0]["content"] == FAILED_COMMAND_REMINDER.format(command="python3 -c raise SystemExit(3) (exit code 3)")
    assert notices == ["The last command failed (python3 -c raise SystemExit(3) (exit code 3)); asking the model to deal with it"]


def test_success_after_failure_needs_no_reminder(tmp_path):
    agent, answer, notices = run(tmp_path, [reply(command=FAIL), reply(command=PASS), reply("Fixed and passing.")])
    assert answer == "Fixed and passing."
    assert notices == []


def test_no_commands_no_reminder(tmp_path):
    _, answer, notices = run(tmp_path, [reply("Just an answer.")])
    assert answer == "Just an answer." and notices == []
