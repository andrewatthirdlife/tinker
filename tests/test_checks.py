"""A project's checks run before a final answer is accepted, once files have changed."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import MAX_CHECK_ROUNDS, Agent  # noqa: E402
from config import Config, apply_project_config  # noqa: E402
from session import Session  # noqa: E402

CHECK = "python3 -c 'import sys; sys.exit(0 if open(\"status.txt\").read().strip() == \"ok\" else 1)'"


def reply(content="", call=None):
    calls = [{"function": {"name": call[0], "arguments": call[1]}}] if call else []
    tool_calls = [type("Call", (), {"model_dump": lambda self, c=c: c})() for c in calls] or None
    return type("Response", (), {"message": type("Message", (), {"content": content, "tool_calls": tool_calls})()})()


def write(text):
    return reply(call=("write_file", {"path": "status.txt", "content": text}))


def make_agent(tmp_path, checks, run=("python3 -c*",)):
    config = Config(
        host="h", model="m", num_ctx=65536, temperature=0, max_iterations=20, max_tool_output_chars=20000,
        sessions_dir=str(tmp_path / "sessions"), lint_args=[], default_mode="edit",
        modes={"edit": {"description": "d", "write": ["**"], "run": list(run)}}, checks=checks,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    notices, calls = [], []
    agent = Agent(config, Session.create(config.sessions_dir, workspace), lambda n, a: calls.append(a.get("command")),
                  notices.append, lambda p, d: None, lambda c: None)
    return agent, workspace, notices, calls


def script(agent, replies):
    agent.client = type("Client", (), {"chat": lambda self, **kw: replies.pop(0)})()


in_sandbox = pytest.mark.skipif("TINKER_SANDBOXED" in os.environ, reason="checks run commands, which start a sandbox")


@in_sandbox
def test_failing_check_sends_the_work_back(tmp_path):
    agent, workspace, notices, _ = make_agent(tmp_path, [CHECK])
    script(agent, [write("broken"), reply("Done!"), write("ok"), reply("Fixed, the check passes.")])
    answer = agent.ask("make the status ok")
    assert answer == "Fixed, the check passes."
    assert notices == ["1 check(s) failed; asking the model to fix them"]
    assert (workspace / "status.txt").read_text().strip() == "ok"
    assert agent.tools.log.checks_failing == []


@in_sandbox
def test_check_that_never_passes_is_reported(tmp_path):
    agent, _, notices, _ = make_agent(tmp_path, [CHECK])
    script(agent, [write("broken")] + [reply("Done!")] * (MAX_CHECK_ROUNDS + 2))
    answer = agent.ask("make the status ok")
    assert answer.startswith("[Tinker: the project's checks still fail: python3 -c") and answer.endswith("\n\nDone!")
    assert notices.count("1 check(s) failed; asking the model to fix them") == MAX_CHECK_ROUNDS
    assert agent.tools.log.checks_failing == [CHECK]
    assert "checks still failing" in agent.change_summary()


def test_no_checks_when_nothing_changed(tmp_path):
    agent, _, notices, calls = make_agent(tmp_path, [CHECK])
    script(agent, [reply("Just an answer.")])
    assert agent.ask("what is this?") == "Just an answer."
    assert notices == [] and calls == []


def test_checks_the_mode_cannot_run_are_skipped(tmp_path):
    agent, _, notices, calls = make_agent(tmp_path, [CHECK], run=())
    script(agent, [write("broken"), reply("Done!")])
    assert agent.ask("make the status ok") == "Done!"
    assert notices == [] and CHECK not in calls


@pytest.mark.parametrize("checks, expected, warned", [
    (["go vet ./...", "go build -o agentcli ."], ["go vet ./...", "go build -o agentcli ."], False),
    ("go vet ./...", [], True),
    ([1, 2], [], True),
])
def test_project_config_checks(tmp_path, checks, expected, warned):
    config = Config(host="h", model="m", num_ctx=1, temperature=0, max_iterations=1, max_tool_output_chars=1,
                    sessions_dir=str(tmp_path / "s"), lint_args=[], default_mode="plan",
                    modes={"plan": {"description": "d"}})
    (tmp_path / ".tinker").mkdir()
    (tmp_path / ".tinker" / "config.json").write_text(json.dumps({"checks": checks}))
    warnings = apply_project_config(config, tmp_path)
    assert config.checks == expected
    assert bool(warnings) == warned
