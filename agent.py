import re
from pathlib import Path
from typing import Callable

from ollama import Client

from config import Config
from tools import SCHEMAS, Tools

SYSTEM_PROMPT = """You are a code analysis assistant working inside the workspace: {root}

You can explore the code with these tools:
- list_files: see what files exist (optionally filtered by a glob such as '*.py').
- search: find where names, strings or patterns appear across files.
- read_file: read a file's contents with line numbers.

Guidelines:
- Explore before answering: use list_files and search to find relevant code, then read_file to examine it.
- Do not guess about code you have not read. If something cannot be found, say so.
- Paths are relative to the workspace root.
- When referring to code, cite it as path:line.
- You are read-only: you cannot modify files or run commands.
- Be concise and direct in your final answer."""

FUNCTION_RE = re.compile(r"<function=([\w-]+)>(.*?)</function>", re.DOTALL)
PARAMETER_RE = re.compile(r"<parameter=([\w-]+)>\n?(.*?)\n?</parameter>", re.DOTALL)
PARAM_TYPES = {
    s["function"]["name"]: {k: v["type"] for k, v in s["function"]["parameters"]["properties"].items()}
    for s in SCHEMAS
}


def _coerce(tool: str, param: str, value: str):
    if PARAM_TYPES.get(tool, {}).get(param) == "integer":
        try:
            return int(value)
        except ValueError:
            pass
    return value


def extract_text_tool_calls(content: str) -> tuple[str, list[dict]]:
    """Recover qwen3-coder style XML tool calls that Ollama failed to parse (e.g. missing <tool_call> tag)."""
    calls = [
        {"function": {"name": name, "arguments": {p: _coerce(name, p, v) for p, v in PARAMETER_RE.findall(body)}}}
        for name, body in FUNCTION_RE.findall(content)
    ]
    text = content[: content.find("<function=")].replace("<tool_call>", "").strip() if calls else content
    return text, calls


class Agent:
    def __init__(self, config: Config, root: Path, on_tool_call: Callable[[str, dict], None] = lambda n, a: None):
        self.config = config
        self.client = Client(host=config.host)
        self.tools = Tools(root, config.max_tool_output_chars)
        self.on_tool_call = on_tool_call
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT.format(root=root.resolve())}]

    def ask(self, question: str) -> str:
        self.messages.append({"role": "user", "content": question})

        for _ in range(self.config.max_iterations):
            response = self.client.chat(
                model=self.config.model,
                messages=self.messages,
                tools=SCHEMAS,
                options={"num_ctx": self.config.num_ctx, "temperature": self.config.temperature},
            )
            message = response.message
            tool_calls = [c.model_dump() for c in message.tool_calls or []]
            content = message.content or ""
            if not tool_calls:
                content, tool_calls = extract_text_tool_calls(content)
            self.messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            if not tool_calls:
                return content

            for call in tool_calls:
                name, args = call["function"]["name"], call["function"]["arguments"] or {}
                self.on_tool_call(name, args)
                self.messages.append({"role": "tool", "tool_name": name, "content": self.tools.run(name, args)})

        return f"[Stopped after {self.config.max_iterations} iterations without a final answer]"
