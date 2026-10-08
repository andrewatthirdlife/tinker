"""Trimming the conversation to fit the model's context window."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import DROPPED_NOTE, KEEP_RECENT_TOOL_RESULTS, fit, total_tokens  # noqa: E402

SYSTEM = {"role": "system", "content": "You are Tinker."}
BIG = "x" * 8000  # about 2300 tokens


def request(text):
    return {"role": "user", "content": text}


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def result(name, content=BIG):
    return {"role": "tool", "tool_name": name, "content": content}


def conversation(requests=3, reads=3):
    msgs = [SYSTEM]
    for r in range(requests):
        msgs.append(request(f"request {r}"))
        for k in range(reads):
            msgs += [call("read_file", path=f"f{r}{k}.py"), result("read_file")]
        msgs.append({"role": "assistant", "content": f"answer {r}"})
    return msgs


def test_fits_already():
    msgs = conversation(1, 1)
    trimmed = fit(msgs, 100_000)
    assert trimmed.messages == msgs and not trimmed.changed


def test_old_tool_outputs_go_first():
    msgs = conversation(2, 4)  # 8 large outputs
    budget = total_tokens(msgs) - 5000
    trimmed = fit(msgs, budget)
    assert total_tokens(trimmed.messages) <= budget
    assert trimmed.removed_outputs >= 1 and trimmed.removed_requests == 0
    outputs = [m["content"] for m in trimmed.messages if m["role"] == "tool"]
    assert outputs[0].startswith("[Output of read_file removed")  # the oldest went first
    assert all(o == BIG for o in outputs[-KEEP_RECENT_TOOL_RESULTS:])  # the most recent are kept
    assert [m for m in trimmed.messages if m["role"] == "user"] == [request("request 0"), request("request 1")]


def test_original_messages_are_not_changed():
    msgs = conversation(2, 4)
    fit(msgs, 3000)
    assert all(m["content"] == BIG for m in msgs if m["role"] == "tool")


def test_large_arguments_of_old_calls_are_removed():
    msgs = [SYSTEM, request("write it"), call("write_file", path="a.py", content=BIG), result("write_file", "Created a.py"),
            *[m for k in range(KEEP_RECENT_TOOL_RESULTS) for m in (call("read_file", path=f"{k}"), result("read_file"))]]
    trimmed = fit(msgs, total_tokens(msgs) - 1000)
    args = trimmed.messages[2]["tool_calls"][0]["function"]["arguments"]
    assert args["path"] == "a.py" and args["content"] == "[8000 characters removed to save space]"


def test_earlier_requests_are_dropped_but_never_the_current_one():
    # Long answers to earlier requests: removing tool outputs alone can't make this fit.
    msgs = [SYSTEM]
    for r in range(4):
        msgs += [request(f"request {r}"), call("read_file", path="a.py"), result("read_file", "short"),
                 {"role": "assistant", "content": BIG}]
    trimmed = fit(msgs, 6000)
    assert trimmed.fits and total_tokens(trimmed.messages) <= 6000
    assert trimmed.removed_requests >= 1
    assert trimmed.messages[0] == SYSTEM and trimmed.messages[1] == DROPPED_NOTE
    users = [m["content"] for m in trimmed.messages if m["role"] == "user"]
    assert users[-1] == "request 3"
    assert "request 0" not in users


def test_agent_notes_are_not_mistaken_for_requests():
    msgs = conversation(1, 6)
    msgs.insert(3, {"role": "user", "content": "[Note from the agent, not the user. Lint found problems]"})
    trimmed = fit(msgs, 9000)
    assert any(m["content"] == "request 0" for m in trimmed.messages)


def test_last_resort_shortens_the_largest_texts():
    msgs = [SYSTEM, request("summarise this"), call("read_file", path="huge.txt"), result("read_file", "A" * 100_000 + "THE END")]
    trimmed = fit(msgs, 5000)
    assert trimmed.fits and trimmed.shortened >= 1
    text = trimmed.messages[-1]["content"]
    assert text.startswith("AAAA") and text.endswith("THE END") and "characters removed to save space" in text
    assert trimmed.messages[1] == request("summarise this")


def test_reports_when_it_cannot_fit():
    trimmed = fit([SYSTEM, request("hi")], 1)
    assert not trimmed.fits
    assert "still too long" in trimmed.describe()
