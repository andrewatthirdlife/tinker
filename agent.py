import re
from pathlib import Path
from typing import Callable

from ollama import Client, ResponseError

from config import Config
from session import Session
from tools import SCHEMAS, WRITE_TOOLS, ChangeLog, ConfirmWrite, Tools

SYSTEM_PROMPT = """You are a coding assistant working inside the workspace: {root}

IMPORTANT: Only edit or create files when the user explicitly asks you to make a change.
- A question is not a request to change code. If the user asks a question (e.g. "how would you...",
  "what should...", "can you explain..."), answer with a plan: which files and functions you would change
  and what each change would be. Do NOT call edit_file or write_file.
- If the user has said not to change the code, do not call edit_file or write_file until they ask you to.

{mode_prompt}

You have these tools:
- list_files: see what files exist (optionally filtered by a glob such as '*.py').
- search: find where names, strings or patterns appear across files.
- read_file: read a file's contents with line numbers.
- edit_file: replace an exact piece of text in an existing file (edit mode only).
- write_file: create a new file or completely replace one (edit mode only).
- git_changes: see uncommitted changes (status and diff).

Guidelines:
- Explore before answering: use list_files and search to find relevant code, then read_file to examine it.
- Do not guess about code you have not read. If something cannot be found, say so.
- Paths are relative to the workspace root.
- When referring to code, cite it as path:line.

When asked to make a change:
- First explore the relevant code, then state a short plan (which files and what changes) before making any edits.
- If the request is ambiguous or there are several reasonable approaches, ask the user instead of guessing.

When changing code:
- Always read a file before editing it.
- Prefer small, targeted edit_file changes. Use write_file only for new files or complete rewrites.
- old_text must be copied exactly from the file, including indentation, without the line numbers shown by read_file.
- Make the smallest change that achieves the goal and match the existing code style.
- Changes may need the user's approval. If a change is rejected, follow the user's feedback.
- You cannot run commands, so you cannot run tests or commit. Changes are left uncommitted for the user to review.

Before your final answer after changing code:
- Call git_changes and check the diff against each thing the user asked for.
- Look for mistakes in the diff: missing imports, names that are used but not defined, leftover unused code, and
  anything the user asked you not to do.
- Fix any problems you find before answering.

Your final answer must be accurate, not reassuring:
- Describe what you changed in a few lines. Do not repeat the user's request back as a checklist.
- Never claim code works, is correct or has been tested. You cannot run it, so say that it has not been run.
- Report anything that went wrong: failed edits, rejected changes, requirements you did not meet, and anything you are unsure about.
- Do not use praise or filler such as "Perfect!", "Excellent!" or "All requirements have been met".
- Be concise and direct."""

MODE_PROMPTS = {
    "edit": "Current mode: edit. You can change files with edit_file and write_file when the user asks for a change.",
    "plan": (
        "Current mode: plan. edit_file and write_file are unavailable, so you cannot change files. "
        "Answer with plans only. If the user asks for a change, describe it and tell them to switch to edit mode with /edit."
    ),
}

MAX_CHAT_RETRIES = 2
RETRY_TEMPERATURE = 0.7
MALFORMED_CALL_HINT = (
    "Your previous response could not be parsed because the tool call was malformed. "
    "Try again, making sure every <parameter=...> is closed with </parameter> before </function>."
)

PROGRESS_REMINDER = (
    "[Note from the agent, not the user. So far in this task: {summary}. "
    "Continue if the task is not finished. In your final answer, mention any failed edits and any files "
    "that were rewritten in full, and say that the code has not been run.]"
)

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
    def __init__(
        self,
        config: Config,
        session: Session,
        on_tool_call: Callable[[str, dict], None],
        on_notice: Callable[[str], None],
        confirm_write: ConfirmWrite,
    ):
        self.config = config
        self.client = Client(host=config.host)
        self.tools = Tools(Path(session.workspace), config.max_tool_output_chars, confirm_write)
        self.on_tool_call = on_tool_call
        self.on_notice = on_notice
        self.session = session
        # Always use the current system prompt, including when resuming an older session.
        self.messages: list[dict] = [self._system_message(), *session.messages[1:]]

    @property
    def mode(self) -> str:
        return self.session.mode

    def set_mode(self, mode: str) -> None:
        self.session.mode = mode
        self.messages[0] = self._system_message()
        # Also note the switch in the history, where the model is more likely to notice it.
        self._add({"role": "user", "content": f"[The user switched to {mode} mode. {MODE_PROMPTS[mode]}]"})

    def _system_message(self) -> dict:
        prompt = SYSTEM_PROMPT.format(root=self.session.workspace, mode_prompt=MODE_PROMPTS[self.mode])
        return {"role": "system", "content": prompt}

    def _available_tools(self) -> list[dict]:
        if self.mode == "edit":
            return SCHEMAS
        return [s for s in SCHEMAS if s["function"]["name"] not in WRITE_TOOLS]

    def _add(self, message: dict) -> None:
        self.messages.append(message)
        self.session.save(self.messages)

    def change_summary(self) -> str | None:
        """Facts about file changes during the last request, independent of what the model claims."""
        return self.tools.log.summary()

    def _chat(self, tools: list[dict]):
        """Call the model, retrying when Ollama fails to parse the model's tool call (HTTP 500)."""
        # Reminders are added for this call only and are not kept in the history.
        reminders = []
        if summary := self.change_summary():
            # Placed last so the model sees it right before writing its answer, unlike the system prompt.
            reminders.append({"role": "user", "content": PROGRESS_REMINDER.format(summary=summary)})
        messages, temperature = [*self.messages, *reminders], self.config.temperature
        for attempt in range(MAX_CHAT_RETRIES + 1):
            try:
                return self.client.chat(
                    model=self.config.model,
                    messages=messages,
                    tools=tools,
                    options={"num_ctx": self.config.num_ctx, "temperature": temperature},
                )
            except ResponseError as e:
                if e.status_code != 500 or attempt == MAX_CHAT_RETRIES:
                    raise
                self.on_notice(f"Ollama error, retrying ({attempt + 1}/{MAX_CHAT_RETRIES}): {e.error}")
                # The hint is only for the retry; it is not kept in the history.
                messages = [*self.messages, *reminders, {"role": "user", "content": MALFORMED_CALL_HINT}]
                temperature = max(temperature, RETRY_TEMPERATURE)

    def ask(self, question: str) -> str:
        self._add({"role": "user", "content": question})
        self.tools.log = ChangeLog()

        tools = self._available_tools()
        tool_names = {t["function"]["name"] for t in tools}

        for _ in range(self.config.max_iterations):
            message = self._chat(tools).message
            tool_calls = [c.model_dump() for c in message.tool_calls or []]
            content = message.content or ""
            if not tool_calls:
                content, tool_calls = extract_text_tool_calls(content)
            self._add({"role": "assistant", "content": content, "tool_calls": tool_calls})

            if not tool_calls:
                return content

            for call in tool_calls:
                name, args = call["function"]["name"], call["function"]["arguments"] or {}
                self.on_tool_call(name, args)
                if name in tool_names:
                    result = self.tools.run(name, args)
                else:
                    result = f"Error: {name} is not available in {self.mode} mode."
                self._add({"role": "tool", "tool_name": name, "content": result})

        return f"[Stopped after {self.config.max_iterations} iterations without a final answer]"
