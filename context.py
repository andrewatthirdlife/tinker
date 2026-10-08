"""Keep the conversation sent to the model inside its context window.

If a prompt is longer than num_ctx, Ollama silently cuts it, and in practice the model loses most of the
conversation, including the request it is working on. So the agent trims a copy of the history before each call
(the saved session keeps everything):

1. old tool outputs, and large arguments of old tool calls (file contents), are replaced by a short note;
2. if that isn't enough, whole earlier requests are dropped, oldest first; the current request is always kept;
3. as a last resort, the largest remaining texts are shortened, keeping their start and end.
"""

import json
from dataclasses import dataclass

CHARS_PER_TOKEN = 2.8  # measured 3.0-4.6 across qwen3-coder, ornith and laguna on code, prose and JSON; lower is safer
MESSAGE_OVERHEAD = 10  # tokens for the role markers around each message
KEEP_RECENT_TOOL_RESULTS = 4
LARGE_ARGUMENT_CHARS = 500
MIN_SHORTENED_CHARS = 2000
DROPPED_NOTE = {"role": "user", "content": "[Note from the agent: earlier parts of this conversation were removed to save space.]"}


def estimate_tokens(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value)
    return int(len(text) / CHARS_PER_TOKEN) + 1


def message_tokens(message: dict) -> int:
    calls = message.get("tool_calls") or []
    return MESSAGE_OVERHEAD + estimate_tokens(message.get("content") or "") + (estimate_tokens(calls) if calls else 0)


def total_tokens(messages: list[dict]) -> int:
    return sum(message_tokens(m) for m in messages)


@dataclass
class Trimmed:
    messages: list[dict]
    removed_outputs: int = 0
    removed_requests: int = 0
    shortened: int = 0
    fits: bool = True

    @property
    def changed(self) -> bool:
        return bool(self.removed_outputs or self.removed_requests or self.shortened)

    def describe(self) -> str:
        parts = []
        if self.removed_outputs:
            parts.append(f"removed {self.removed_outputs} old tool output(s)")
        if self.removed_requests:
            parts.append(f"dropped {self.removed_requests} earlier request(s)")
        if self.shortened:
            parts.append(f"shortened {self.shortened} long text(s)")
        if not self.fits:
            parts.append("still too long for the context window")
        return "Conversation too long for the model: " + ", ".join(parts)


def is_request(message: dict) -> bool:
    """A message from the user starting a request; the agent's own notes to the model start with "["."""
    return message["role"] == "user" and not (message.get("content") or "").startswith("[")


def fit(messages: list[dict], budget: int) -> Trimmed:
    """Return messages (the first being the system prompt) trimmed to an estimated budget of tokens."""
    result = Trimmed(messages=list(messages))
    if total_tokens(messages) <= budget:
        return result
    msgs = [dict(m) for m in messages]
    result.messages = msgs

    # 1. Old tool outputs and large tool-call arguments, oldest first.
    tool_results = [i for i, m in enumerate(msgs) if m["role"] == "tool"]
    recent = set(tool_results[-KEEP_RECENT_TOOL_RESULTS:])
    first_recent = min(recent) if recent else len(msgs)
    for i, m in enumerate(msgs):
        if total_tokens(msgs) <= budget or i >= first_recent:
            break
        if m["role"] == "tool" and i not in recent:
            note = f"[Output of {m.get('tool_name', 'the tool')} removed to save space. Call it again if you need it.]"
            if len(m.get("content") or "") > len(note):
                msgs[i] = {**m, "content": note}
                result.removed_outputs += 1
        elif m["role"] == "assistant" and m.get("tool_calls"):
            msgs[i] = {**m, "tool_calls": [_without_large_arguments(call) for call in m["tool_calls"]]}

    # 2. Whole earlier requests, oldest first. A request runs from one user request to the next.
    current = max((i for i, m in enumerate(msgs) if is_request(m)), default=1)
    starts = [i for i, m in enumerate(msgs) if is_request(m) and 0 < i < current]
    if total_tokens(msgs) > budget and starts:
        keep_from = current
        for start in starts:
            remaining = [msgs[0], DROPPED_NOTE, *msgs[start:]]
            if total_tokens(remaining) <= budget:
                keep_from = start
                break
        result.removed_requests = sum(1 for s in starts if s < keep_from)
        msgs = [msgs[0], DROPPED_NOTE, *msgs[keep_from:]]
        result.messages = msgs

    # 3. Shorten the largest remaining texts until it fits (or nothing large is left).
    while total_tokens(msgs) > budget:
        if len(msgs) < 2:
            result.fits = False
            break
        i = max(range(1, len(msgs)), key=lambda k: len(msgs[k].get("content") or ""))
        text = msgs[i].get("content") or ""
        if len(text) <= MIN_SHORTENED_CHARS:
            result.fits = False
            break
        msgs[i] = {**msgs[i], "content": _shorten(text, len(text) // 2)}
        result.shortened += 1
    return result


def _without_large_arguments(call: dict) -> dict:
    args = call["function"].get("arguments") or {}
    smaller = {k: (f"[{len(v)} characters removed to save space]" if isinstance(v, str) and len(v) > LARGE_ARGUMENT_CHARS else v)
               for k, v in args.items()}
    return {**call, "function": {**call["function"], "arguments": smaller}}


def _shorten(text: str, limit: int) -> str:
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - 2 * half} characters removed to save space] ...\n{text[-half:]}"
