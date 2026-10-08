"""The same call with the same result, again and again, is not run again and in the end stops the request."""

import sys
from pathlib import Path

from ollama import ChatResponse, Message

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import MAX_REPEATS, NO_ANSWER, RETRY_TEMPERATURE, STOP_AFTER_REPEATS, Agent  # noqa: E402
from config import Config  # noqa: E402
from session import Session  # noqa: E402


def reply(content="", call=None):
    calls = [Message.ToolCall(function=Message.ToolCall.Function(name=call[0], arguments=call[1]))] if call else None
    return ChatResponse(model="m", done=True, done_reason="stop",
                        message=Message(role="assistant", content=content, tool_calls=calls))


MISSING = ("read_file", {"path": "missing.py"})
PRESENT = ("read_file", {"path": "a.py"})


def run(tmp_path, replies, tool_results=None):
    config = Config(
        host="h", model="m", num_ctx=65536, temperature=0, max_iterations=30, max_tool_output_chars=20000,
        sessions_dir=str(tmp_path / "sessions"), lint_args=[], default_mode="plan",
        modes={"plan": {"description": "d", "write": []}},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("x = 1\n")
    notices = []
    agent = Agent(config, Session.create(config.sessions_dir, workspace), lambda n, a: None, notices.append,
                  lambda p, d: None, lambda c: None)
    agent.client = type("Client", (), {"chat": lambda self, **kw: replies.pop(0)})()
    if tool_results is not None:
        agent.tools.run = lambda name, args: tool_results.pop(0)
    answer = agent.ask("what does missing.py do?")
    results = [m["content"] for m in agent.messages if m["role"] == "tool"]
    return answer, results, notices


def test_repeated_failing_call_is_not_run_and_then_stops(tmp_path):
    answer, results, notices = run(tmp_path, [reply(call=MISSING)] * (STOP_AFTER_REPEATS + 1))
    assert answer == "[Stopped: the model kept repeating the same call, read_file(path='missing.py')]"
    assert len(results) == STOP_AFTER_REPEATS
    assert all("does not exist" in r for r in results[:MAX_REPEATS])
    assert all(r.startswith("Error: not run.") for r in results[MAX_REPEATS:])
    assert notices[-1] == f"The model made the same read_file call {STOP_AFTER_REPEATS + 1} times in a row; stopping"


def test_a_different_call_starts_the_count_again(tmp_path):
    replies = ([reply(call=MISSING)] * MAX_REPEATS + [reply(call=PRESENT)]
               + [reply(call=MISSING)] * MAX_REPEATS + [reply("It doesn't exist.")])
    answer, results, _ = run(tmp_path, replies)
    assert answer == "It doesn't exist."
    assert not any(r.startswith("Error: not run.") for r in results)


def test_repeated_successful_calls_stop_too(tmp_path):
    # In a stage4 run the model read the same 11 lines of a file over and over.
    answer, results, _ = run(tmp_path, [reply(call=PRESENT)] * (STOP_AFTER_REPEATS + 1))
    assert answer.startswith("[Stopped: the model kept repeating the same call, read_file")
    assert sum(r.startswith("Error: not run.") for r in results) == STOP_AFTER_REPEATS - MAX_REPEATS


def test_a_repeated_call_with_a_new_result_is_still_run(tmp_path):
    # e.g. a test run while something else changes: the same call, but not stuck.
    count = STOP_AFTER_REPEATS + 2
    answer, results, _ = run(tmp_path, [reply(call=PRESENT)] * count + [reply("Done.")],
                             tool_results=[f"result {i}" for i in range(count)])
    assert answer == "Done."
    assert results == [f"result {i}" for i in range(count)]


def test_empty_final_answer_is_reported(tmp_path):
    answer, _, _ = run(tmp_path, [reply(""), reply("")])
    assert answer == NO_ANSWER


def test_after_a_repeat_the_thinking_is_hidden_and_the_temperature_raised(tmp_path):
    def thinking_reply(call):
        r = reply(call=call)
        r.message.thinking = "The issue is that list_files is called with a bad path."
        return r
    replies = [thinking_reply(PRESENT), thinking_reply(PRESENT), reply("x is 1.")]
    sent = []
    config = Config(
        host="h", model="m", num_ctx=65536, temperature=0.2, max_iterations=10, max_tool_output_chars=20000,
        sessions_dir=str(tmp_path / "sessions"), lint_args=[], default_mode="plan",
        modes={"plan": {"description": "d", "write": []}},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("x = 1\n")
    agent = Agent(config, Session.create(config.sessions_dir, workspace), lambda n, a: None, lambda n: None,
                  lambda p, d: None, lambda c: None)

    def chat(self, **kw):
        sent.append(kw)
        return replies.pop(0)
    agent.client = type("Client", (), {"chat": chat})()
    assert agent.ask("what does a.py do?") == "x is 1."
    assert [kw["options"]["temperature"] for kw in sent] == [0.2, 0.2, RETRY_TEMPERATURE]
    assert sum(bool(m.get("thinking")) for m in sent[1]["messages"]) == 1   # before the repeat, it goes back
    assert not any(m.get("thinking") or m.get("repeated") for m in sent[2]["messages"])
