"""Streamed replies are put back together, and an empty final answer gets one request for a real one."""

import sys
from pathlib import Path

from ollama import ChatResponse, Message

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import EMPTY_ANSWER_REMINDER, Agent, collect_reply  # noqa: E402
from config import Config  # noqa: E402
from session import Session  # noqa: E402


def chunk(content="", thinking=None, tool_calls=None, done=False):
    return ChatResponse(model="m", done=done, prompt_eval_count=100 if done else None,
                        message=Message(role="assistant", content=content, thinking=thinking, tool_calls=tool_calls))


def test_collect_reply_joins_the_pieces():
    call = Message.ToolCall(function=Message.ToolCall.Function(name="read_file", arguments={"path": "a.py"}))
    stream = [chunk(thinking="Let me "), chunk(thinking="look."), chunk("I'll read "), chunk("a.py."),
              chunk(tool_calls=[call]), chunk(done=True)]
    reply = collect_reply(iter(stream))
    assert reply.message.content == "I'll read a.py."
    assert reply.message.thinking == "Let me look."
    assert [c.function.name for c in reply.message.tool_calls] == ["read_file"]
    assert reply.prompt_eval_count == 100 and reply.done


def test_collect_reply_with_nothing_but_thinking():
    reply = collect_reply(iter([chunk(thinking="Hmm."), chunk(done=True)]))
    assert reply.message.content == "" and reply.message.tool_calls is None


def run(tmp_path, replies):
    config = Config(
        host="h", model="m", num_ctx=65536, temperature=0, max_iterations=10, max_tool_output_chars=20000,
        sessions_dir=str(tmp_path / "sessions"), lint_args=[], default_mode="plan",
        modes={"plan": {"description": "d", "write": []}},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    notices = []
    agent = Agent(config, Session.create(config.sessions_dir, workspace), lambda n, a: None, notices.append,
                  lambda p, d: None, lambda c: None)
    streams = [iter([chunk(thinking="thinking..."), chunk(text), chunk(done=True)]) for text in replies]
    agent.client = type("Client", (), {"chat": lambda self, **kw: streams.pop(0)})()
    return agent, agent.ask("what does a.py do?"), notices


def test_empty_answer_gets_one_request_for_a_real_one(tmp_path):
    agent, answer, notices = run(tmp_path, ["\n", "a.py sets x to 1."])
    assert answer == "a.py sets x to 1."
    assert notices == ["The model gave an empty answer; asking it for one"]
    assert sum(m["content"] == EMPTY_ANSWER_REMINDER for m in agent.messages) == 1


def test_a_second_empty_answer_is_accepted(tmp_path):
    _, answer, notices = run(tmp_path, ["", ""])
    assert answer == ""
    assert len(notices) == 1
