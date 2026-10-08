"""Run Tinker on a set of small tasks and measure how it does.

Each task in evals/tasks/<name>/ has:
  task.json      {"prompt": ..., "mode": ..., "description": ...}
  project/       the starting code, with failing tests in tests/
  hidden_tests/  optional extra tests, added only for grading (catches solutions that special-case the tests)
  solution/      a reference solution, used by --check-tasks

For each task the project is copied into a fresh git repository, Tinker is run in-process with every change and
command approved, and the result is graded by running all the tests in the sandbox (the code was written by the
model, so it isn't trusted). Changes to the tests count as a failure.

    .venv/bin/python evals/run.py                         # all tasks, model from config.json
    .venv/bin/python evals/run.py --model glm-4.7-flash:latest --repeat 3
    .venv/bin/python evals/run.py --tasks fizzbuzz,rename
    .venv/bin/python evals/run.py --check-tasks           # check the tasks themselves, no model needed
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import sandbox  # noqa: E402
from agent import Agent  # noqa: E402
from config import load_config  # noqa: E402
from session import Session  # noqa: E402

TASKS = Path(__file__).resolve().parent / "tasks"
RESULTS = Path(__file__).resolve().parent / "results"
VENV = ROOT / ".venv"  # has pytest; each task's .venv links to it


@dataclass
class Outcome:
    task: str
    model: str
    passed: bool
    tests_changed: bool
    grading: str  # last lines of the grading pytest output
    seconds: float
    model_calls: int = 0
    tool_calls: dict = field(default_factory=dict)
    failed_edits: int = 0
    reverted: int = 0
    commands: list = field(default_factory=list)
    notices: list = field(default_factory=list)
    answer: str = ""
    error: str = ""


def tasks(names: str | None) -> list[Path]:
    found = sorted(p for p in TASKS.iterdir() if (p / "task.json").exists())
    if names:
        wanted = names.split(",")
        missing = set(wanted) - {p.name for p in found}
        if missing:
            sys.exit(f"Unknown task(s): {', '.join(sorted(missing))}")
        found = [p for p in found if p.name in wanted]
    return found


def make_workspace(task: Path, parent: Path) -> Path:
    workspace = parent / task.name
    shutil.copytree(task / "project", workspace)
    (workspace / ".venv").symlink_to(VENV)
    (workspace / ".gitignore").write_text(".venv\n__pycache__/\n.pytest_cache/\n")
    git = ["git", "-C", str(workspace), "-c", "user.name=eval", "-c", "user.email=eval@localhost"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "task"], check=True)
    return workspace


def tests_digest(workspace: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((workspace / "tests").rglob("*.py")):
        digest.update(path.relative_to(workspace).as_posix().encode() + path.read_bytes())
    return digest.hexdigest()


def grade(task: Path, workspace: Path) -> tuple[bool, str]:
    """Add the hidden tests and run every test in the sandbox."""
    if (task / "hidden_tests").is_dir():
        shutil.copytree(task / "hidden_tests", workspace / "tests", dirs_exist_ok=True)
    policy = sandbox.Policy(
        rules={str(workspace): "rw", str(VENV.resolve()): "ro"}, cwd=str(workspace),
        env={"PATH": f"{workspace}/.venv/bin:/usr/bin:/bin"},
    )
    result = sandbox.run([str(workspace / ".venv/bin/python"), "-m", "pytest", "-q", "-p", "no:cacheprovider"], policy, 120)
    summary = "\n".join((result.stdout + result.stderr).strip().splitlines()[-3:])
    return result.returncode == 0 and not result.timed_out, summary


def check_task(task: Path, parent: Path) -> list[str]:
    """A task is valid if its visible tests fail at the start and its reference solution passes everything."""
    problems = []
    start = make_workspace(task, parent / "start")
    if grade(task, start)[0]:
        problems.append("the tests already pass before any change")
    solved = make_workspace(task, parent / "solved")
    shutil.copytree(task / "solution", solved, dirs_exist_ok=True)
    passed, summary = grade(task, solved)
    if not passed:
        problems.append(f"the reference solution fails: {summary}")
    return problems


def run_task(task: Path, parent: Path, model: str | None, num_ctx: int | None) -> Outcome:
    spec = json.loads((task / "task.json").read_text())
    workspace = make_workspace(task, parent)
    config = load_config(ROOT / "config.json")
    config.model = model or config.model
    config.num_ctx = num_ctx or config.num_ctx
    config.sessions_dir = parent / "sessions"
    config.commands.extra_read = [*config.commands.extra_read, str(VENV.resolve())]
    session = Session.create(config.sessions_dir, workspace)

    tool_calls, notices = {}, []
    agent = Agent(config, session, on_tool_call=lambda name, args: tool_calls.__setitem__(name, tool_calls.get(name, 0) + 1),
                  on_notice=notices.append, confirm_write=lambda path, diff: None, confirm_command=lambda command: None)
    if agent.mode != spec.get("mode", "feature"):
        agent.set_mode(spec.get("mode", "feature"))
    calls = 0
    chat = agent.client.chat

    def counted_chat(**kwargs):
        nonlocal calls
        calls += 1
        return chat(**kwargs)

    agent.client.chat = counted_chat
    before = tests_digest(workspace)
    start = time.monotonic()
    answer, error = "", ""
    try:
        answer = agent.ask(spec["prompt"])
    except ConnectionError as e:
        raise SystemExit(f"Can't reach Ollama ({e}). Is the Ollama machine asleep?")
    except Exception as e:  # a crash in Tinker is a result worth recording, not a reason to stop the run
        error = f"{type(e).__name__}: {e}"
    seconds = time.monotonic() - start
    tests_changed = tests_digest(workspace) != before
    passed, summary = grade(task, workspace)
    log = agent.tools.log
    return Outcome(
        task=task.name, model=config.model, passed=passed and not tests_changed and not error,
        tests_changed=tests_changed, grading=summary, seconds=round(seconds, 1), model_calls=calls,
        tool_calls=tool_calls, failed_edits=log.failed, reverted=len(log.reverted), commands=log.commands,
        notices=notices, answer=answer, error=error,
    )


def print_table(outcomes: list[Outcome]) -> None:
    print(f"\n{'task':18} {'result':8} {'calls':>5} {'edits!':>6} {'time':>7}  notes")
    for o in outcomes:
        notes = "; ".join(filter(None, [
            "TESTS CHANGED" if o.tests_changed else "", o.error, f"{len(o.commands)} commands" if o.commands else "",
            o.grading.splitlines()[-1] if not o.passed and o.grading else "",
        ]))
        print(f"{o.task:18} {'pass' if o.passed else 'FAIL':8} {o.model_calls:>5} {o.failed_edits:>6} {o.seconds:>6.0f}s  {notes}")
    passed = sum(o.passed for o in outcomes)
    print(f"\n{passed}/{len(outcomes)} passed ({100 * passed / len(outcomes):.0f}%), "
          f"{sum(o.seconds for o in outcomes) / 60:.1f} minutes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Tinker on the evaluation tasks")
    parser.add_argument("--model", help="Ollama model (default: the one in config.json)")
    parser.add_argument("--num-ctx", type=int, help="context window (default: the one in config.json)")
    parser.add_argument("--tasks", help="comma-separated task names (default: all)")
    parser.add_argument("--repeat", type=int, default=1, help="run each task this many times")
    parser.add_argument("--check-tasks", action="store_true", help="check the tasks themselves and exit")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="tinker-eval-") as tmp:
        if args.check_tasks:
            bad = 0
            for task in tasks(args.tasks):
                problems = check_task(task, Path(tmp) / task.name)
                bad += bool(problems)
                print(f"{task.name:18} {'ok' if not problems else '; '.join(problems)}")
            sys.exit(1 if bad else 0)

        outcomes = []
        for task in tasks(args.tasks):
            for n in range(args.repeat):
                print(f"Running {task.name} ({n + 1}/{args.repeat})...", file=sys.stderr, flush=True)
                outcomes.append(run_task(task, Path(tmp) / f"{task.name}-{n}", args.model, args.num_ctx))
        print_table(outcomes)
        RESULTS.mkdir(exist_ok=True)
        model = outcomes[0].model.replace(":", "_").replace("/", "_")
        path = RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}-{model}.json"
        path.write_text(json.dumps([asdict(o) for o in outcomes], indent=1))
        print(f"Details: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
