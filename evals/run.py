"""Run Tinker on a set of small tasks and measure how it does.

Each task in evals/tasks/<name>/ has:
  task.json      {"prompt": ..., "mode": ..., "description": ..., "kind": "tests" or "bugfix"}
  project/       the starting code
  hidden_tests/  extra tests, added only for grading (they catch solutions that special-case the visible tests)
  solution/      a reference solution, used by --check-tasks and to check the agent's own tests

There are two kinds of task:
  tests   the project has failing tests; the agent must make them pass without changing them.
  bugfix  the project's tests all pass and say nothing about the bug, which is only described in the prompt.
          The agent must prove the bug with a test before fixing it. A bugfix task passes only if the code is
          fixed (all tests pass, hidden ones included), the agent's new tests fail on the original code (they
          really reproduce the bug), and they pass on the reference solution (they test the right behaviour).

For each task the project is copied into a fresh git repository and Tinker is run in-process with every change
and command approved. Grading runs the tests in the sandbox, since the code was written by the model.

    .venv/bin/python evals/run.py                         # all tasks, model from config.json
    .venv/bin/python evals/run.py --model glm-4.7-flash:latest --repeat 3
    .venv/bin/python evals/run.py --tasks bug_median,rename
    .venv/bin/python evals/run.py --check-tasks           # check the tasks themselves, no model needed
"""

import argparse
import json
import re
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
from tools import WRITE_TOOLS  # noqa: E402

TASKS = Path(__file__).resolve().parent / "tasks"
RESULTS = Path(__file__).resolve().parent / "results"
VENV = ROOT / ".venv"  # has pytest; each task's .venv links to it
EXIT_CODE_RE = re.compile(r"^exit code (\d+)", re.M)


@dataclass
class Outcome:
    task: str
    kind: str
    model: str
    passed: bool
    fixed: bool  # all tests pass afterwards, hidden ones included
    grading: str  # last lines of the grading pytest output
    seconds: float
    tests_changed: bool = False  # "tests" tasks: the agent changed the tests it had to make pass
    agent_tests: list = field(default_factory=list)  # "bugfix" tasks: test files the agent added or changed
    reproduced: bool | None = None  # its tests fail on the original code
    tests_valid: bool | None = None  # its tests pass on the reference solution
    proved_first: bool | None = None  # it ran a failing test before its first change to the code
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


def make_workspace(task: Path, parent: Path, overlay: str | None = None) -> Path:
    """A git repository with the task's project, optionally with another of its folders (e.g. solution) on top."""
    workspace = parent / task.name
    shutil.copytree(task / "project", workspace)
    if overlay:
        shutil.copytree(task / overlay, workspace, dirs_exist_ok=True)
    (workspace / ".venv").symlink_to(VENV)
    (workspace / ".gitignore").write_text(".venv\n__pycache__/\n.pytest_cache/\n")
    git = ["git", "-C", str(workspace), "-c", "user.name=eval", "-c", "user.email=eval@localhost"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "task"], check=True)
    return workspace


def is_test_file(rel: str) -> bool:
    return rel.endswith(".py") and (rel.startswith("tests/") or Path(rel).name.startswith("test_"))


def test_files(workspace: Path) -> dict[str, bytes]:
    files = {}
    for path in workspace.rglob("*.py"):
        rel = path.relative_to(workspace).as_posix()
        if is_test_file(rel) and ".venv" not in path.parts:
            files[rel] = path.read_bytes()
    return files


def run_pytest(workspace: Path, paths: list[str] | None = None) -> tuple[int, str]:
    """Run pytest in the sandbox. Returns its exit code (1: tests failed, 0: all passed) and a short summary."""
    policy = sandbox.Policy(
        rules={str(workspace): "rw", str(VENV.resolve()): "ro"}, cwd=str(workspace),
        env={"PATH": f"{workspace}/.venv/bin:/usr/bin:/bin", "PYTEST_ADDOPTS": "-p no:cacheprovider"},
    )
    argv = [str(workspace / ".venv/bin/python"), "-m", "pytest", "-q", *(paths or [])]
    result = sandbox.run(argv, policy, 120)
    summary = "\n".join((result.stdout + result.stderr).strip().splitlines()[-3:])
    return (-1 if result.timed_out else result.returncode), summary


def add_hidden_tests(task: Path, workspace: Path) -> None:
    if (task / "hidden_tests").is_dir():
        shutil.copytree(task / "hidden_tests", workspace / "tests", dirs_exist_ok=True)


def check_task(task: Path, parent: Path) -> list[str]:
    """Check that a task is fair: it starts broken in the right way, and its reference solution passes."""
    kind = json.loads((task / "task.json").read_text()).get("kind", "tests")
    problems = []
    start = make_workspace(task, parent / "start")
    visible_code, _ = run_pytest(start)
    if kind == "tests" and visible_code == 0:
        problems.append("the tests already pass before any change")
    if kind == "bugfix":
        if visible_code != 0:
            problems.append("the visible tests should pass at the start; the bug is only in the report")
        add_hidden_tests(task, start)
        if run_pytest(start)[0] == 0:
            problems.append("the hidden tests don't catch the bug")
    solved = make_workspace(task, parent / "solved", overlay="solution")
    add_hidden_tests(task, solved)
    code, summary = run_pytest(solved)
    if code != 0:
        problems.append(f"the reference solution fails: {summary}")
    return problems


def check_agent_tests(task: Path, workspace: Path, before: dict[str, bytes], parent: Path) -> tuple[list[str], bool, bool]:
    """Find the agent's new or changed tests, and run them on the original code and on the reference solution."""
    after = test_files(workspace)
    added = sorted(rel for rel, content in after.items() if before.get(rel) != content)
    if not added:
        return [], False, False
    results = []
    for name, overlay in (("original", None), ("reference", "solution")):
        copy = make_workspace(task, parent / name, overlay)
        for rel in added:
            (copy / rel).parent.mkdir(parents=True, exist_ok=True)
            (copy / rel).write_bytes(after[rel])
        results.append(run_pytest(copy, added)[0])
    return added, results[0] == 1, results[1] == 0


def proved_first(timeline: list[tuple[str, dict, str]]) -> bool:
    """Did the agent run a failing command after writing a test, before it first changed any other code?"""
    wrote_test = False
    for name, args, result in timeline:
        if result.startswith("Error"):
            continue
        if name in WRITE_TOOLS:
            if is_test_file(args.get("path", "").lstrip("./")):
                wrote_test = True
            else:
                return False
        if name == "run_command" and wrote_test:
            exit_code = EXIT_CODE_RE.search(result)
            if exit_code and exit_code.group(1) != "0":
                return True
    return False


def run_task(task: Path, parent: Path, model: str | None, num_ctx: int | None) -> Outcome:
    spec = json.loads((task / "task.json").read_text())
    kind = spec.get("kind", "tests")
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

    calls, timeline = 0, []
    chat, run_tool = agent.client.chat, agent.tools.run

    def counted_chat(**kwargs):
        nonlocal calls
        calls += 1
        return chat(**kwargs)

    def recorded_tool(name, args):
        result = run_tool(name, args)
        timeline.append((name, args, result))
        return result

    agent.client.chat, agent.tools.run = counted_chat, recorded_tool
    before = test_files(workspace)
    start = time.monotonic()
    answer, error = "", ""
    try:
        answer = agent.ask(spec["prompt"])
    except ConnectionError as e:
        raise SystemExit(f"Can't reach Ollama ({e}). Is the Ollama machine asleep?")
    except Exception as e:  # a crash in Tinker is a result worth recording, not a reason to stop the run
        error = f"{type(e).__name__}: {e}"
    seconds = time.monotonic() - start

    outcome = Outcome(task=task.name, kind=kind, model=config.model, passed=False, fixed=False, grading="",
                      seconds=round(seconds, 1), model_calls=calls, tool_calls=tool_calls, notices=notices,
                      answer=answer, error=error)
    log = agent.tools.log
    outcome.failed_edits, outcome.reverted, outcome.commands = log.failed, len(log.reverted), log.commands
    if kind == "bugfix":
        outcome.agent_tests, outcome.reproduced, outcome.tests_valid = check_agent_tests(task, workspace, before, parent)
        outcome.proved_first = proved_first(timeline)
    else:
        outcome.tests_changed = test_files(workspace) != before
    add_hidden_tests(task, workspace)
    code, outcome.grading = run_pytest(workspace)
    outcome.fixed = code == 0
    if kind == "bugfix":
        outcome.passed = bool(outcome.fixed and outcome.reproduced and outcome.tests_valid and not error)
    else:
        outcome.passed = outcome.fixed and not outcome.tests_changed and not error
    return outcome


def print_table(outcomes: list[Outcome]) -> None:
    def yes_no(value):
        return "-" if value is None else "yes" if value else "NO"

    print(f"\n{'task':18} {'result':6} {'fixed':5} {'repro':5} {'valid':5} {'first':5} {'calls':>5} {'time':>6}  notes")
    for o in outcomes:
        notes = "; ".join(filter(None, [
            "TESTS CHANGED" if o.tests_changed else "", "no new tests" if o.kind == "bugfix" and not o.agent_tests else "",
            o.error, o.grading.splitlines()[-1] if not o.fixed and o.grading else "",
        ]))
        print(f"{o.task:18} {'pass' if o.passed else 'FAIL':6} {yes_no(o.fixed):5} {yes_no(o.reproduced):5} "
              f"{yes_no(o.tests_valid):5} {yes_no(o.proved_first):5} {o.model_calls:>5} {o.seconds:>5.0f}s  {notes}")
    passed = sum(o.passed for o in outcomes)
    print(f"\n{passed}/{len(outcomes)} passed ({100 * passed / len(outcomes):.0f}%), "
          f"{sum(o.seconds for o in outcomes) / 60:.1f} minutes")
    print("fixed: all tests pass, hidden ones included; repro: the agent's tests fail on the original code;\n"
          "valid: they pass on the reference solution; first: it ran a failing test before changing the code")


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

        RESULTS.mkdir(exist_ok=True)
        model = (args.model or load_config(ROOT / "config.json").model).replace(":", "_").replace("/", "_")
        path = RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}-{model}.json"
        outcomes = []
        for task in tasks(args.tasks):
            for n in range(args.repeat):
                print(f"Running {task.name} ({n + 1}/{args.repeat})...", file=sys.stderr, flush=True)
                outcomes.append(run_task(task, Path(tmp) / f"{task.name}-{n}", args.model, args.num_ctx))
                path.write_text(json.dumps([asdict(o) for o in outcomes], indent=1))  # saved as it goes
        print_table(outcomes)
        print(f"Details: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
