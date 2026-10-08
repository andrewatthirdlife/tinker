import json
import re
import shlex
from pathlib import Path
from typing import Callable

from ollama import ChatResponse, Client, Message, ResponseError

import context
from config import Config
from session import Session
from tools import SCHEMAS, WRITE_TOOLS, ChangeLog, ConfirmCommand, ConfirmWrite, ToolError, Tools

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
REPLY_TOKENS = 8192  # room left in the context window for the model's reply, e.g. a whole file for write_file
# Replies are streamed, so this is the longest the model may go without sending anything. It must allow for
# loading the model and reading a long prompt before the first output, but a slow reply that keeps coming is fine.
SILENCE_TIMEOUT_SECONDS = 300
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

MAX_FAILED_COMMAND_ROUNDS = 1
MAX_CHECK_ROUNDS = 3
CHECK_REMINDER = (
    "[Note from the agent, not the user. The project's checks failed after your changes:\n{failures}\n"
    "Fix these problems. The task isn't finished until the checks pass.]"
)
CHECK_OUTPUT_CHARS = 3000
MAX_EMPTY_ANSWER_ROUNDS = 1
EMPTY_ANSWER_REMINDER = (
    "[Note from the agent, not the user. Your last reply was empty. Give your final answer now: what you found or "
    "changed, which commands you ran and what they showed, and anything that is still unfinished.]"
)
MAX_CUT_OFF_ROUNDS = 3  # in a row; a reply with a tool call starts the count again
CUT_OFF_REMINDER = (
    "[Note from the agent, not the user. Your last reply was cut off at the length limit before it finished, so "
    "nothing was done. Thinking is turned off for your next reply: act now with one tool call. If you are unsure "
    "what is wrong, find out by running something or reading the code; otherwise make a small edit_file change.]"
)
NO_ANSWER = "[The model stopped without giving an answer]"
MAX_REPEATS = 3  # after the same call gives the same result this many times in a row, it is not run again
STOP_AFTER_REPEATS = 6  # ... and a call repeated this many times ends the request
NOT_RUN_AGAIN = (
    "Error: not run. You have made this exact call {count} times in a row and got the same result each time, so "
    "it would only give that result again. Do something different: act on what the result showed, look at "
    "other code or files, or use another tool."
)
FAILED_COMMAND_REMINDER = (
    "[Note from the agent, not the user. The last command you ran failed: {command}. "
    "Fix the problem and run it again, or explain in your answer that it still fails.]"
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


def collect_reply(response) -> ChatResponse:
    """Put a streamed reply back together: text and thinking arrive in pieces, each tool call in one piece."""
    if hasattr(response, "message"):  # a whole reply, not a stream
        return response
    content, thinking, tool_calls, last = [], [], [], None
    for chunk in response:
        content.append(chunk.message.content or "")
        thinking.append(chunk.message.thinking or "")
        tool_calls += chunk.message.tool_calls or []
        last = chunk
    message = Message(role="assistant", content="".join(content), thinking="".join(thinking) or None,
                      tool_calls=tool_calls or None)
    return ChatResponse(**{**(last.model_dump() if last else {}), "message": message})


def _for_model(messages: list[dict]) -> list[dict]:
    """The history as the model should see it.

    Thinking goes back for the current request only, so the model keeps its plan between tool calls; earlier
    requests' thinking is dropped, as is the thinking of a reply cut off at the length limit or of a reply that
    repeated a call (shown its own thinking, a stuck model copies it word for word). The reply statistics are
    kept for the session only.
    """
    current = max((i for i, m in enumerate(messages) if context.is_request(m)), default=0)
    result = []
    for i, m in enumerate(messages):
        if m["role"] == "assistant":
            drop = {"done_reason", "eval_count", "repeated"}
            if i < current or m.get("done_reason") == "length" or m.get("repeated"):
                drop.add("thinking")
            m = {k: v for k, v in m.items() if k not in drop}
        result.append(m)
    return result


def _command_passed(result: str) -> bool:
    return bool(re.search(r"^exit code 0,", result, re.M))


def _short_args(args: dict) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return text if len(text) <= 80 else text[:77] + "..."


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
        confirm_command: ConfirmCommand,
    ):
        self.config = config
        self.client = Client(host=config.host, timeout=SILENCE_TIMEOUT_SECONDS)
        if session.mode not in config.modes:
            session.mode = config.default_mode
        self.tools = Tools(Path(session.workspace), config.max_tool_output_chars, confirm_write, config.lint_args, config.modes[session.mode], confirm_command=confirm_command, command_settings=config.commands)
        self.on_tool_call = on_tool_call
        self.on_notice = on_notice
        self._last_trim_notice = ""
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

    def _run_checks(self) -> list[str]:
        """Run the project's checks that the current mode allows; describe each failure with its output.

        They go through run_command, so they are sandboxed and approved like any other command.
        """
        failures, failing = [], []
        for command in self.config.checks:
            if not self.tools.mode.can_run(" ".join(shlex.split(command))):
                continue
            self.on_tool_call("run_command", {"command": command})
            result = self.tools.run("run_command", {"command": command})
            if not _command_passed(result):
                failing.append(command)
                failures.append(result[:CHECK_OUTPUT_CHARS])
        self.tools.log.checks_failing = failing
        return failures

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

    def _next_reply(self, tools: list[dict], think: bool | None = None,
                    temperature: float | None = None) -> tuple[str, list[dict], dict]:
        """Get the model's next reply, asking again if a tool call is missing required parameters.

        A reply like that is never added to the history: the model copies bad calls it can see.
        Also returns the reply's thinking, why it stopped and its length, to keep with it in the history.
        """
        hint = []
        temperature = self.config.temperature if temperature is None else temperature
        for attempt in range(MAX_CHAT_RETRIES + 1):
            response = self._chat(tools, hint, temperature, think)
            message = response.message
            extra = {k: v for k, v in (("thinking", getattr(message, "thinking", None)),
                                       ("done_reason", getattr(response, "done_reason", None)),
                                       ("eval_count", getattr(response, "eval_count", None))) if v}
            tool_calls = [c.model_dump() for c in message.tool_calls or []]
            content = message.content or ""
            if not tool_calls:
                content, tool_calls = extract_text_tool_calls(content)
            problems = missing_arguments(tool_calls)
            if not problems or attempt == MAX_CHAT_RETRIES:
                return content, tool_calls, extra
            self.on_notice(f"Tool call without required parameters ({'; '.join(problems)}), asking again ({attempt + 1}/{MAX_CHAT_RETRIES})")
            hint = [{"role": "user", "content": MISSING_ARGS_HINT.format(problems="; ".join(problems))}]
            temperature = max(temperature, RETRY_TEMPERATURE)

    def _reply_tokens(self) -> int:
        return min(REPLY_TOKENS, self.config.num_ctx // 4)

    def _fit_context(self, messages: list[dict], tools: list[dict]) -> list[dict]:
        """Trim a copy of the messages to the context window; Ollama would otherwise cut them silently."""
        trimmed = context.fit(messages, self.config.num_ctx - self._reply_tokens() - context.estimate_tokens(tools))
        if trimmed.changed or not trimmed.fits:
            notice = trimmed.describe()
            if notice != self._last_trim_notice:
                self.on_notice(notice)
                self._last_trim_notice = notice
        return trimmed.messages

    def _chat(self, tools: list[dict], hint: list[dict], temperature: float, think: bool | None = None):
        """Call the model, retrying when Ollama fails to parse the model's tool call (HTTP 500)."""
        # Reminders are added for this call only and are not kept in the history.
        reminders = []
        if summary := self.change_summary():
            # Placed last so the model sees it right before writing its answer, unlike the system prompt.
            reminders.append({"role": "user", "content": PROGRESS_REMINDER.format(summary=summary)})
        messages = [*_for_model(self.messages), *reminders, *hint]
        for attempt in range(MAX_CHAT_RETRIES + 1):
            try:
                return collect_reply(self.client.chat(
                    model=self.config.model,
                    messages=self._fit_context(messages, tools),
                    tools=tools,
                    # num_predict caps the reply: some models (thinking ones especially) can otherwise generate
                    # without end.
                    options={"num_ctx": self.config.num_ctx, "temperature": temperature,
                             "num_predict": self._reply_tokens()},
                    stream=True,
                    # Only ever set to False, after a reply cut off while thinking; None leaves the model's default.
                    think=think,
                ))
            except ResponseError as e:
                if e.status_code != 500 or attempt == MAX_CHAT_RETRIES:
                    raise
                self.on_notice(f"Ollama error, retrying ({attempt + 1}/{MAX_CHAT_RETRIES}): {e.error}")
                # The hint is only for the retry; it is not kept in the history.
                messages = [*_for_model(self.messages), *reminders, *hint, {"role": "user", "content": MALFORMED_CALL_HINT}]
                temperature = max(temperature, RETRY_TEMPERATURE)

    def ask(self, question: str) -> str:
        self._add({"role": "user", "content": question})
        self.tools.log = ChangeLog()

        tools = self._available_tools()
        tool_names = {t["function"]["name"] for t in tools}
        lint_rounds = 0
        failed_command_rounds = 0
        check_rounds = 0
        empty_answer_rounds = 0
        cut_off_rounds = 0
        think = temperature = None
        previous_reply = None  # the index of the reply that made the previous call
        streak = 0  # how many times in a row the previous call was made with the same result

        previous_call, previous_content = (None, None, None), None
        for _ in range(self.config.max_iterations):
            content, tool_calls, extra = self._next_reply(tools, think, temperature)
            think = temperature = None
            if (content and content == previous_content and len(tool_calls) == 1
                    and tool_calls[0]["function"]["name"] not in WRITE_TOOLS
                    and _call_key(tool_calls[0]) == previous_call[:2]):
                # The model keeps sending the same finished answer with the same read-only check; the check's
                # result can't have changed, so take the text as the final answer instead of looping. Both must
                # repeat: some models re-read a file while still working, with new text each time.
                self.on_notice(f"Repeated {tool_calls[0]['function']['name']} call; taking the reply as the final answer")
                tool_calls = []
            self._add({"role": "assistant", "content": content, "tool_calls": tool_calls, **extra})
            reply = len(self.messages) - 1

            if not tool_calls:
                if extra.get("done_reason") == "length" and cut_off_rounds < MAX_CUT_OFF_ROUNDS:
                    # The reply hit num_predict, usually while thinking in circles, so it isn't an answer. Telling
                    # the model to think less doesn't work; turning thinking off for one reply makes it act.
                    cut_off_rounds += 1
                    if extra.get("thinking"):
                        think = False
                    self.on_notice("The model's reply was cut off at the length limit; asking it to act without thinking")
                    self._add({"role": "user", "content": CUT_OFF_REMINDER})
                    continue
                if not content.strip() and empty_answer_rounds < MAX_EMPTY_ANSWER_ROUNDS:
                    # Thinking models sometimes reason, then end with no visible answer at all.
                    empty_answer_rounds += 1
                    self.on_notice("The model gave an empty answer; asking it for one")
                    self._add({"role": "user", "content": EMPTY_ANSWER_REMINDER})
                    continue
                problems = self._lint_changed_files()
                if problems is not None:
                    self.tools.log.lint_problems = len(problems)
                    if problems and lint_rounds < MAX_LINT_ROUNDS:
                        lint_rounds += 1
                        self.on_notice(f"Lint found {len(problems)} problem(s); asking the model to fix them")
                        self._add({"role": "user", "content": LINT_REMINDER.format(problems="\n".join(problems))})
                        continue
                if self.config.checks and self.tools.log.originals:
                    failures = self._run_checks()
                    if failures and check_rounds < MAX_CHECK_ROUNDS:
                        check_rounds += 1
                        self.on_notice(f"{len(failures)} check(s) failed; asking the model to fix them")
                        self._add({"role": "user", "content": CHECK_REMINDER.format(failures="\n\n".join(failures))})
                        continue
                if self.tools.log.last_failed_command is not None and failed_command_rounds < MAX_FAILED_COMMAND_ROUNDS:
                    failed_command_rounds += 1
                    self.on_notice(f"The last command failed ({self.tools.log.last_failed_command}); asking the model to deal with it")
                    self._add({"role": "user", "content": FAILED_COMMAND_REMINDER.format(command=self.tools.log.last_failed_command)})
                    continue
                return content if content.strip() else NO_ANSWER

            cut_off_rounds = 0
            for call in tool_calls:
                name, args = call["function"]["name"], call["function"]["arguments"] or {}
                self.on_tool_call(name, args)
                # The same call with the same result again and again: the model is stuck. Stop running the call,
                # and in the end stop the request.
                same_call = _call_key(call) == previous_call[:2]
                if same_call and streak >= STOP_AFTER_REPEATS:
                    self.on_notice(f"The model made the same {name} call {streak + 1} times in a row; stopping")
                    return f"[Stopped: the model kept repeating the same call, {name}({_short_args(args)})]"
                if same_call and streak >= MAX_REPEATS:
                    streak += 1
                    self._add({"role": "tool", "tool_name": name, "content": NOT_RUN_AGAIN.format(count=streak - 1)})
                    continue
                if name not in tool_names:
                    result = f"Error: {name} is not available in {self.mode} mode."
                elif problems := missing_arguments([call]):
                    result = f"Error: {problems[0]}. {CALL_FORMAT}"
                else:
                    result = self.tools.run(name, args)
                this_call = (*_call_key(call), result)
                streak = streak + 1 if this_call == previous_call else 1
                if this_call == previous_call:
                    result += REPEAT_NOTE
                    # Don't show the model the thinking that led it here twice, and make its next reply less
                    # predictable: at a low temperature a stuck model gives the same reply again.
                    for i in (previous_reply, reply):
                        if i is not None and self.messages[i]["role"] == "assistant":
                            self.messages[i]["repeated"] = True
                    temperature = max(self.config.temperature, RETRY_TEMPERATURE)
                previous_reply = reply
                previous_call, previous_content = this_call, content
                self._add({"role": "tool", "tool_name": name, "content": result})

        calls = "call" if self.config.max_iterations == 1 else "calls"
        return f"[Stopped after {self.config.max_iterations} model {calls} without a final answer]"
