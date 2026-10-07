import argparse
from pathlib import Path

from agent import Agent
from config import load_config


def print_tool_call(name: str, args: dict) -> None:
    arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    print(f"  → {name}({arg_str})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Code analysis agent using Ollama")
    parser.add_argument("workspace", nargs="?", default=".", help="Directory the agent may read")
    parser.add_argument("--config", default=Path(__file__).parent / "config.json", type=Path)
    args = parser.parse_args()

    config = load_config(args.config)
    root = Path(args.workspace)
    agent = Agent(config, root, on_tool_call=print_tool_call)

    print(f"Model: {config.model}  Workspace: {root.resolve()}")
    print("Type a question, or 'exit' to quit.\n")

    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question in ("exit", "quit"):
            break
        if not question:
            continue
        print(f"\n{agent.ask(question)}\n")


if __name__ == "__main__":
    main()
