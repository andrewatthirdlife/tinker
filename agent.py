import json
import re
from pathlib import Path
from typing import Callable

from ollama import Client, ResponseError

from config import Config
from session import Session
from tools import SCHEMAS, WRITE_TOOLS, ChangeLog, ConfirmWrite, ToolError, Tools

SYSTEM_PROMPT = """You are Tinker, a coding assistant working inside the workspace: {root}

IMPORTANT: Only edit or create files when the user explicitly asks you to make a change.
- A question is not a request to change code. If the user asks a question (e.g. "how would you...",
  "what should...", "can you explain..."), answer with a plan: which files and functions you would change
  and what each change would be. Do NOT call edit_file or write_file.
- If the user has said not to change the code, do not call edit_file or write_file until they ask you to.

{mode_prompt}

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
- old_text must be copied exactly from the file, including indentation and blank lines, without the line numbers
  shown by read_file. Copy it from the file as it is now, not from memory: if you have edited the file since you
  last read that part, read it again first.
- Keep old_text short: just enough lines to be unique, usually 2-5. To add code at the end of a file, use only
  the last few lines of the file as old_text, never the whole file.
- If an edit fails, the error shows the current text of the most similar part of the file. Copy from that.
- Changes in different parts of a file need separate edits. For example, a new import goes with the other
  imports at the top of the file, as its own edit.
- Make the smallest change that achieves the goal and match the existing code style.
- Only change what the request needs. If you notice other problems (lint warnings, bugs, style), mention them in your answer instead of fixing them.
- Changes may need the user's approval. If a change is rejected, follow the user's feedback.
- If the current mode lets you run commands, run the relevant tests after changing code and fix any failures you caused.
- You cannot commit. Changes are left uncommitted for the user to review.

Before your final answer after changing code:
- Call review_changes and check the diff against each thing the user asked for.
- Look for mistakes in the diff: missing imports, names that are used but not defined, leftover unused code, and
  anything the user asked you not to do.
- Fix any problems you find before answering.

Your final answer must be accurate, not reassuring:
- Describe what you changed in a few lines. Do not repeat the user's request back as a checklist.
- Never claim code works, is correct or has been tested unless a command you ran during this request showed it. If you didn't run it, say so.
- Report anything that went wrong: failed edits, rejected changes, requirements you did not meet, and anything you are unsure about.
- Do not use praise or filler such as "Perfect!", "Excellent!" or "All requirements have been met".
- Be concise and direct."""

MAX_CHAT_RETRIES = 2
RETRY_TEMPERATURE = 0.7
MALFORMED_CALL_HINT = (
    "Your previous response could not be parsed because the tool call was malformed. "
    "Try again, making sure every <parameter=...> is closed with </parameter> before </function>."
)

PROGRESS_REMINDER = (
    "[Note from the agent, not the user. So far in this task: {summary}. "
    "Continue if the task is not finished. In your final answer, mention any failed edits and any files "
    "that were rewritten in full, and say which commands you ran and what they showed, or that the code has not been run.]"
)

MAX_LINT_ROUNDS = 2
LINT_REMINDER = (
    "[Note from the agent, not the user. Lint found problems in the code you changed:\n{problems}\n"
    "Fix these problems.]"
)

REPEAT_NOTE = (
    "\n[Note from the agent: this call and its result are the same as your previous call, so nothing has "
    "changed. Do not repeat it. Continue with the task, or give your final answer without calling any tools.]"
)
CALL_FORMAT = (
    "Give every required parameter inside the <function=...> block, for example "
    "<parameter=path>\nCLAUDE.md\n</parameter>."
)
MISSING_ARGS_HINT = (
    "[Note from the agent, not the user. Your last reply could not be used because a tool call was missing "
    "required parameters: {problems}. Make the call again. " + CALL_FORMAT + "]"
)
REQUIRED_PARAMS = {s["function"]["name"]: s["function"]["parameters"].get("required", []) for s in SCHEMAS}

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


def _call_key(call: dict) -> tuple[str, str]:
    return call["function"]["name"], json.dumps(call["function"]["arguments"] or {}, sort_keys=True)


def missing_arguments(tool_calls: list[dict]) -> list[str]:
    """Describe tool calls that lack required parameters, e.g. ["read_file is missing path"]."""
    problems = []
    for call in tool_calls:
        name, args = call["function"]["name"], call["function"]["arguments"] or {}
        if missing := [p for p in REQUIRED_PARAMS.get(name, []) if p not in args]:
            problems.append(f"{name} is missing {', '.join(missing)}")
    return problems


def extract_text_tool_calls(content: str) -> tuple[str, list[dict]]:
    """Recover qwen3-coder style XML tool calls that Ollama failed to parse (e.g. missing <tool_call> tag)."""
    calls = [
        {"function": {"name": name, "arguments": {p: _coerce(name, p, v) for p, v in PARAMETER_RE.findall(body)}}}
        for name, body in FUNCTION_RE.findall(content)
    ]
    text = content[: content.find("<function=")] if calls else content
    # A reply can also end with an opening tag the model never completed.
    return text.replace("<tool_call>", "").strip(), calls


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
        if session.mode not in config.modes:
            session.mode = config.default_mode
        self.tools = Tools(Path(session.workspace), config.max_tool_output_chars, confirm_write, config.lint_args, config.modes[session.mode])
        self.on_tool_call = on_tool_call
        self.on_notice = on_notice
        self.session = session
        # Always use the current system prompt, including when resuming an older session.
        self.messages: list[dict] = [self._system_message(), *session.messages[1:]]

    @property
    def mode(self) -> str:
        return self.session.mode

    def _mode_prompt(self) -> str:
        """Build the mode section of the system prompt from the current Mode."""
        mode = self.config.modes[self.mode]
        lines = [
            f"Current mode: {mode.name} ({mode.description}).",
            mode.describe_reads(),
            mode.describe_writes(),
            mode.describe_commands(),
        ]
        if mode.instructions:
            lines.append(mode.instructions)
        if not mode.write:
            lines.append("Answer with plans only. If the user asks for a change, describe it and tell them to switch to a mode that allows it (/mode lists the modes).")
        return "\n".join(lines)

    def set_mode(self, mode: str) -> None:
        self.session.mode = mode
        self.tools.mode = self.config.modes[mode]
        self.messages[0] = self._system_message()
        # Also note the switch in the history, where the model is more likely to notice it.
        self._add({"role": "user", "content": f"[The user switched to {mode} mode. {self._mode_prompt()}]"})

    def _system_message(self) -> dict:
        prompt = SYSTEM_PROMPT.format(root=self.session.workspace, mode_prompt=self._mode_prompt())
        return {"role": "system", "content": prompt}

    def _available_tools(self) -> list[dict]:
        mode = self.config.modes[self.mode]
        tools = SCHEMAS
        if not mode.write:
            tools = [s for s in tools if s["function"]["name"] not in WRITE_TOOLS]
        if not mode.run:
            tools = [s for s in tools if s["function"]["name"] != "run_command"]
        return tools

    def _add(self, message: dict) -> None:
        self.messages.append(message)
        self.session.save(self.messages)

    def change_summary(self) -> str | None:
        """Facts about file changes during the last request, independent of what the model claims."""
        return self.tools.log.summary()

    def _lint_changed_files(self) -> list[str] | None:
        """Lint problems introduced during this request, or None if no Python file changed.

        Problems that were already in the files are only counted for the report: shown to the model, it tends to
        fix them, which is a change nobody asked for.
        """
        try:
            result = self.tools.new_lint_problems()
        except ToolError as e:
            self.on_notice(f"Lint failed: {e}")
            return None
        if result is None:
            return None
        problems, self.tools.log.lint_existing = result
        return problems

    def _next_reply(self, tools: list[dict]) -> tuple[str, list[dict]]:
        """Get the model's next reply, asking again if a tool call is missing required parameters.

        A reply like that is never added to the history: the model copies bad calls it can see.
        """
        hint, temperature = [], self.config.temperature
        for attempt in range(MAX_CHAT_RETRIES + 1):
            message = self._chat(tools, hint, temperature).message
            tool_calls = [c.model_dump() for c in message.tool_calls or []]
            content = message.content or ""
            if not tool_calls:
                content, tool_calls = extract_text_tool_calls(content)
            problems = missing_arguments(tool_calls)
            if not problems or attempt == MAX_CHAT_RETRIES:
                return content, tool_calls
            self.on_notice(f"Tool call without required parameters ({'; '.join(problems)}), asking again ({attempt + 1}/{MAX_CHAT_RETRIES})")
            hint = [{"role": "user", "content": MISSING_ARGS_HINT.format(problems="; ".join(problems))}]
            temperature = max(temperature, RETRY_TEMPERATURE)

    def _chat(self, tools: list[dict], hint: list[dict], temperature: float):
        """Call the model, retrying when Ollama fails to parse the model's tool call (HTTP 500)."""
        # Reminders are added for this call only and are not kept in the history.
        reminders = []
        if summary := self.change_summary():
            # Placed last so the model sees it right before writing its answer, unlike the system prompt.
            reminders.append({"role": "user", "content": PROGRESS_REMINDER.format(summary=summary)})
        messages = [*self.messages, *reminders, *hint]
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
                messages = [*self.messages, *reminders, *hint, {"role": "user", "content": MALFORMED_CALL_HINT}]
                temperature = max(temperature, RETRY_TEMPERATURE)

    def ask(self, question: str) -> str:
        self._add({"role": "user", "content": question})
        self.tools.log = ChangeLog()

        tools = self._available_tools()
        tool_names = {t["function"]["name"] for t in tools}
        lint_rounds = 0

        previous_call = (None, None, None)
        for _ in range(self.config.max_iterations):
            content, tool_calls = self._next_reply(tools)
            if (content and len(tool_calls) == 1 and tool_calls[0]["function"]["name"] not in WRITE_TOOLS
                    and _call_key(tool_calls[0]) == previous_call[:2]):
                # The model keeps re-running the same read-only check alongside a finished answer; its result
                # cannot have changed, so take the text as the final answer instead of looping.
                self.on_notice(f"Repeated {tool_calls[0]['function']['name']} call; taking the reply as the final answer")
                tool_calls = []
            self._add({"role": "assistant", "content": content, "tool_calls": tool_calls})

            if not tool_calls:
                problems = self._lint_changed_files()
                if problems is not None:
                    self.tools.log.lint_problems = len(problems)
                    if problems and lint_rounds < MAX_LINT_ROUNDS:
                        lint_rounds += 1
                        self.on_notice(f"Lint found {len(problems)} problem(s); asking the model to fix them")
                        self._add({"role": "user", "content": LINT_REMINDER.format(problems="\n".join(problems))})
                        continue
                return content

            for call in tool_calls:
                name, args = call["function"]["name"], call["function"]["arguments"] or {}
                self.on_tool_call(name, args)
                if name not in tool_names:
                    result = f"Error: {name} is not available in {self.mode} mode."
                elif problems := missing_arguments([call]):
                    result = f"Error: {problems[0]}. {CALL_FORMAT}"
                else:
                    result = self.tools.run(name, args)
                this_call = (*_call_key(call), result)
                if this_call == previous_call:
                    result += REPEAT_NOTE
                previous_call = this_call
                self._add({"role": "tool", "tool_name": name, "content": result})

        return f"[Stopped after {self.config.max_iterations} iterations without a final answer]"
