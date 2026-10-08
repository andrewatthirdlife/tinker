"""Repeated calls: a loop of the same answer and check is ended, but re-reading a file while working is not."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import REPEAT_NOTE, Agent  # noqa: E402
from config import Config  # noqa: E402
from session import Session  # noqa: E402


def reply(content="", call=None):
    calls = [{"function": {"name": call[0], "arguments": call[1]}}] if call else []
    tool_calls = [type("Call", (), {"model_dump": lambda self, c=c: c})() for c in calls] or None
    return type("Response", (), {"message": type("Message", (), {"content": content, "tool_calls": tool_calls})()})()


def run(tmp_path, script):
    config = Config(
        host="h", model="m", num_ctx=65536, temperature=0, max_iterations=10, max_tool_output_chars=20000,
        sessions_dir=str(tmp_path / "sessions"), lint_args=[], default_mode="plan",
        modes={"plan": {"description": "d", "write": []}},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("x = 1\n")
    notices = []
    agent = Agent(config, Session.create(config.sessions_dir, workspace), lambda n, a: None, notices.append,
                  lambda p, d: None, lambda c: None)
    agent.client = type("Client", (), {"chat": lambda self, **kw: script.pop(0)})()
    return agent, agent.ask("look at a.py"), notices


READ = ("read_file", {"path": "a.py"})


def test_same_answer_and_same_check_ends_the_loop(tmp_path):
    _, answer, notices = run(tmp_path, [reply("Done: a.py sets x.", READ), reply("Done: a.py sets x.", READ)])
    assert answer == "Done: a.py sets x."
    assert notices == ["Repeated read_file call; taking the reply as the final answer"]


def test_rereading_with_new_text_continues(tmp_path):
    script = [reply("Let me read a.py.", READ), reply("I see the problem. Let me look again.", READ), reply("x is 1.")]
    agent, answer, notices = run(tmp_path, script)
    assert answer == "x is 1."
    assert notices == []
    results = [m["content"] for m in agent.messages if m["role"] == "tool"]
    assert REPEAT_NOTE not in results[0] and REPEAT_NOTE in results[1]  # the repeat is still pointed out
