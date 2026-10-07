import argparse
from pathlib import Path

from ollama import ResponseError

from agent import MODE_PROMPTS, Agent
from config import load_config
from session import Session


def print_tool_call(name: str, args: dict) -> None:
    arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    print(f"  → {name}({arg_str})")


def print_notice(text: str) -> None:
    print(f"  ! {text}")


def colorize_diff(diff: str) -> str:
    colors = {"+": "\033[32m", "-": "\033[31m", "@": "\033[36m"}
    lines = []
    for line in diff.splitlines():
        color = "" if line.startswith(("+++", "---")) else colors.get(line[:1], "")
        lines.append(f"{color}{line}\033[0m" if color else line)
    return "\n".join(lines)


def confirm_write(path: str, diff: str) -> str | None:
    print(f"\n{colorize_diff(diff)}\n")
    answer = input(f"Apply change to {path}? [y = yes, n = no, or type feedback to reject]: ").strip()
    if answer.lower() in ("y", "yes"):
        return None
    if answer.lower() in ("", "n", "no"):
        return "The user rejected this change."
    return f"The user rejected this change with feedback: {answer}"


def list_sessions(sessions_dir: Path) -> None:
    sessions = Session.list_all(sessions_dir)
    if not sessions:
        print(f"No sessions in {sessions_dir}")
    for s in sessions:
        print(f"{s.id:<20} {s.updated}  {s.workspace}\n{'':<20} {s.first_question[:80]!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Coding agent using Ollama")
    parser.add_argument("workspace", nargs="?", help="Directory the agent works in (default: current directory)")
    parser.add_argument("--config", default=Path(__file__).parent / "config.json", type=Path)
    parser.add_argument("--resume", metavar="SESSION_ID", help="Continue a previous session")
    parser.add_argument("--sessions", action="store_true", help="List previous sessions and exit")
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
    agent = Agent(config, session, on_tool_call=print_tool_call, on_notice=print_notice, confirm_write=confirm_write)

    print(f"Session: {session.id}  Model: {config.model}  Workspace: {session.workspace}")
    if args.resume:
        last = next((m["content"] for m in reversed(session.messages) if m["role"] == "assistant" and m["content"]), "")
        print(f"Resumed with {len(session.messages)} messages. Last answer:\n\n{last}\n")
    print("Type a question, or 'exit' to quit.")
    print("/plan: plan mode, the agent cannot change files.  /edit: edit mode, changes need your approval.\n")

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
            mode = question[1:]
            if mode not in MODE_PROMPTS:
                print(f"Unknown command {question}. Commands: {', '.join('/' + m for m in MODE_PROMPTS)}")
            elif mode == agent.mode:
                print(f"Already in {mode} mode.")
            else:
                agent.set_mode(mode)
                print(f"Switched to {mode} mode.")
            continue
        try:
            print(f"\n{agent.ask(question)}\n")
        except ConnectionError as e:
            print(f"\nError: {e}\nIs the Ollama machine asleep? Wake it up and ask again.\n")
        except ResponseError as e:
            print(f"\nOllama error: {e.error}\nThe session is saved; you can ask again or rephrase.\n")

    print(f"Resume with: --resume {session.id}")


if __name__ == "__main__":
    main()
