"""Thinking is kept with each reply, goes back to the model within a request, and a cut-off reply isn't an answer."""

import json
import sys
from pathlib import Path

from ollama import ChatResponse, Message

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context  # noqa: E402
from agent import CUT_OFF_REMINDER, MAX_CUT_OFF_ROUNDS, Agent, _for_model  # noqa: E402
from config import Config  # noqa: E402
from session import Session  # noqa: E402


def reply(content="", thinking=None, done_reason="stop", call=None):
    calls = [Message.ToolCall(function=Message.ToolCall.Function(name=call[0], arguments=call[1]))] if call else None
    return ChatResponse(model="m", done=True, done_reason=done_reason, eval_count=42,
                        message=Message(role="assistant", content=content, thinking=thinking, tool_calls=calls))


def make_agent(tmp_path, replies):
    config = Config(
        host="h", model="m", num_ctx=65536, temperature=0, max_iterations=10, max_tool_output_chars=20000,
        sessions_dir=str(tmp_path / "sessions"), lint_args=[], default_mode="plan",
        modes={"plan": {"description": "d", "write": []}},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("x = 1\n")
    notices, sent = [], []

    def chat(self, **kw):
        sent.append(kw["messages"])
        return replies.pop(0)
    agent = Agent(config, Session.create(config.sessions_dir, workspace), lambda n, a: None, notices.append,
                  lambda p, d: None, lambda c: None)
    agent.client = type("Client", (), {"chat": chat})()
    return agent, notices, sent


def test_thinking_is_saved_and_sent_back_within_the_request(tmp_path):
    agent, _, sent = make_agent(tmp_path, [
        reply(thinking="Plan: read a.py, then explain.", call=("read_file", {"path": "a.py"})),
        reply("a.py sets x to 1."),
        reply("Still x = 1."),
    ])
    agent.ask("what does a.py do?")
    second_call = [m for m in sent[1] if m["role"] == "assistant"]
    assert second_call[-1]["thinking"] == "Plan: read a.py, then explain."
    assert "done_reason" not in second_call[-1] and "eval_count" not in second_call[-1]

    saved = json.loads((agent.session.dir / "messages.json").read_text())
    first = next(m for m in saved if m["role"] == "assistant")
    assert first["thinking"] == "Plan: read a.py, then explain." and first["done_reason"] == "stop"
    assert first["eval_count"] == 42

    agent.ask("and now?")
    assert not any(m.get("thinking") for m in sent[2])


def test_cut_off_reply_is_sent_back(tmp_path):
    agent, notices, sent = make_agent(tmp_path, [
        reply("I need to fix the agent. Let me", thinking="long " * 50, done_reason="length"),
        reply("a.py sets x to 1."),
    ])
    assert agent.ask("what does a.py do?") == "a.py sets x to 1."
    assert notices == ["The model's reply was cut off at the length limit; asking it to take a smaller step"]
    assert any(m.get("content") == CUT_OFF_REMINDER for m in sent[1])
    assert not any(m.get("thinking") for m in sent[1])   # the cut-off thinking doesn't go back


def test_cut_off_replies_are_accepted_after_the_limit(tmp_path):
    replies = [reply("Let me", done_reason="length") for _ in range(MAX_CUT_OFF_ROUNDS + 1)]
    agent, notices, _ = make_agent(tmp_path, replies)
    assert agent.ask("what does a.py do?") == "Let me"
    assert len(notices) == MAX_CUT_OFF_ROUNDS


def test_for_model_drops_earlier_thinking_and_stats():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "", "thinking": "old", "done_reason": "stop", "eval_count": 5},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "", "thinking": "new", "done_reason": "stop"},
    ]
    result = _for_model(messages)
    assert result[2] == {"role": "assistant", "content": ""}
    assert result[4] == {"role": "assistant", "content": "", "thinking": "new"}
    assert messages[2]["thinking"] == "old"   # the history itself is unchanged


def test_context_drops_oldest_thinking_first():
    big = "t" * 3000
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
    messages += [{"role": "assistant", "content": "", "thinking": big} for _ in range(3)]
    budget = context.total_tokens(messages) - 500
    trimmed = context.fit(messages, budget)
    assert [bool(m.get("thinking")) for m in trimmed.messages[2:]] == [False, True, True]
    assert trimmed.removed_thinking == 1 and "thinking from 1 reply" in trimmed.describe()
