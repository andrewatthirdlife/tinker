import argparse
import sys
from pathlib import Path

from ollama import ResponseError

from agent import Agent
from config import Config, load_config
from session import Session


def print_tool_call(name: str, args: dict) -> None:
    arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    print(f"  → {name}({arg_str})", file=sys.stderr)


def print_notice(text: str) -> None:
    print(f"  ! {text}", file=sys.stderr)


def colorize_diff(diff: str) -> str:
    colors = {"+": "\033[32m", "-": "\033[31m", "@": "\033[36m"}
    lines = []
    for line in diff.splitlines():
        color = "" if line.startswith(("+++", "---")) else colors.get(line[:1], "")
        lines.append(f"{color}{line}\033[0m" if color else line)
    return "\n".join(lines)


def run_once(agent: Agent, session: Session, prompt: str) -> int:
    print(f"[session: {session.id}]", file=sys.stderr)
    exit_code = 0
    try:
        answer = agent.ask(prompt)
        print(answer)
    except ConnectionError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("Is the Ollama machine asleep? Wake it up and ask again.", file=sys.stderr)
        exit_code = 1
    except ResponseError as e:
        print(f"Ollama error: {e.error}", file=sys.stderr)
        exit_code = 1

    summary = agent.change_summary()
    if summary is not None:
        print(f"[{summary}]", file=sys.stderr)

    return exit_code


COMMANDS = {
    "plan": "plan mode, the agent cannot change files",
    "edit": "edit mode, the agent can change files with your approval",
    "auto_approve": "toggle auto-approval of file changes (this run only)",
}


def help_text() -> str:
    return "\n".join(f"  /{name}: {description}" for name, description in COMMANDS.items())


def handle_command(command: str, agent: Agent, config: Config) -> None:
    if command not in COMMANDS:
        print(f"Unknown command /{command}")
        print(help_text())
        return
    if command == agent.mode:
        print(f"Already in {command} mode.")
        return
    if command == "auto_approve":
        config.auto_approve_writes = not config.auto_approve_writes
        print(f"Auto-approval of file changes is now {'on' if config.auto_approve_writes else 'off'}.")
    else:
        agent.set_mode(command)
        print(f"Switched to {command} mode.")


def list_sessions(sessions_dir: Path) -> None:
    sessions = Session.list_all(sessions_dir)
    if not sessions:
        print(f"No sessions in {sessions_dir}")
    for s in sessions:
        print(f"{s.id:<20} {s.updated}  {s.workspace}\n{'':<20} {s.first_question[:80]!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tinker: a coding agent using Ollama")
    parser.add_argument("workspace", nargs="?", help="Directory the agent works in (default: current directory)")
    parser.add_argument("--config", default=Path(__file__).parent / "config.json", type=Path)
    parser.add_argument("--resume", metavar="SESSION_ID", help="Continue a previous session")
    parser.add_argument("--sessions", action="store_true", help="List previous sessions and exit")
    parser.add_argument("--mode", choices=["plan", "edit"], help="Starting mode (default: edit for new sessions, the saved mode when resuming)")
    parser.add_argument("-p", "--prompt", metavar="MESSAGE", help="Run one request and exit ('-' reads it from stdin). Uses plan mode unless --mode edit is given, which also auto-approves changes.")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.sessions:
        list_sessions(config.sessions_dir)
        return
    if args.resume:
        if args.workspace:
            parser.error("--resume uses the session's workspace; don't pass one")
        session = Session.load(config.sessions_dir, args.resume)
    else:
        session = Session.create(config.sessions_dir, Path(args.workspace or "."))

    if args.prompt is not None:
        if args.mode is None:
            args.mode = "plan"
        if args.mode == "edit":
            config.auto_approve_writes = True

    def confirm_write(path: str, diff: str) -> str | None:
        if config.auto_approve_writes:
            return None
        print(f"\n{colorize_diff(diff)}\n")
        answer = input(f"Apply change to {path}? [y = yes, n = no, or type feedback to reject]: ").strip()
        if answer.lower() in ("y", "yes"):
            return None
        if answer.lower() in ("", "n", "no"):
            return "The user rejected this change."
        return f"The user rejected this change with feedback: {answer}"

    agent = Agent(config, session, on_tool_call=print_tool_call, on_notice=print_notice, confirm_write=confirm_write)

    if args.mode is not None and args.mode != agent.mode:
        agent.set_mode(args.mode)

    if args.prompt is not None:
        prompt = sys.stdin.read().strip() if args.prompt == "-" else args.prompt
        sys.exit(run_once(agent, session, prompt))

    print(f"Tinker Session: {session.id}  Model: {config.model}  Workspace: {session.workspace}")
    if args.resume:
        last = next((m["content"] for m in reversed(session.messages) if m["role"] == "assistant" and m["content"]), "")
        print(f"Resumed with {len(session.messages)} messages. Last answer:\n\n{last}\n")
    print("Type a question, or 'exit' to quit.")
    print(help_text())
    print(f"\nAuto-approval of file changes is {'on' if config.auto_approve_writes else 'off'}.\n")

    while True:
        try:
            question = input(f"[{agent.mode}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question in ("exit", "quit"):
            break
        if not question:
            continue
        if question.startswith("/"):
            handle_command(question[1:], agent, config)
            continue
        try:
            print(f"\n{agent.ask(question)}\n")
        except ConnectionError as e:
            print(f"\nError: {e}\nIs the Ollama machine asleep? Wake it up and ask again.\n")
        except ResponseError as e:
            print(f"\nOllama error: {e.error}\nThe session is saved; you can ask again or rephrase.\n")
        if summary := agent.change_summary():
            print(f"\033[33m[{summary}]\033[0m\n")

    print(f"Resume with: --resume {session.id}")


if __name__ == "__main__":
    main()
