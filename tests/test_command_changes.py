"""Running a command through Tools: what it changed is recorded, and changes the mode doesn't allow are undone."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tools as tools_module  # noqa: E402
from permissions import Mode  # noqa: E402
from tools import SNAPSHOT_MAX_FILE_BYTES, Tools  # noqa: E402

# These tests start sandboxes, which can't be done from inside one (e.g. when Tinker runs the tests).
pytestmark = pytest.mark.skipif("TINKER_SANDBOXED" in os.environ, reason="can't start a sandbox inside a sandbox")

PYTHON = "/usr/bin/python3"
MODES = {
    "plan": Mode(name="plan", description="d", write=[]),
    "edit": Mode(name="edit", description="d", write=["**"]),
    "feature": Mode(name="feature", description="d", write=["**"], deny_write=["tests/**", "**/test_*.py"]),
}


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    for name, text in {"src/calc.py": "def add(a, b):\n    return a + b\n", "src/old.py": "OLD = 1\n",
                       "tests/test_calc.py": "def test_add():\n    pass\n", "docs/guide.md": "# Guide\n"}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def make_tools(root, mode):
    return Tools(root, 20000, lambda path, diff: None, [], MODES[mode])


def run(tools, code):
    return tools.run_sandboxed([PYTHON, "-c", code], timeout=30)


def test_allowed_changes_are_recorded(workspace):
    tools = make_tools(workspace, "edit")
    result = run(tools, """
import os
open("src/calc.py", "a").write("\\n\\ndef sub(a, b):\\n    return a - b\\n")
open("docs/new.md", "w").write("new\\n")
os.remove("src/old.py")
print("done")
""")
    assert result.stdout.strip() == "done", result.stderr
    assert tools.log.files == {"docs/new.md": "created by a command", "src/calc.py": "changed by a command",
                               "src/old.py": "deleted by a command"}
    review = tools.run("review_changes", {})
    assert "+def sub(a, b):" in review
    assert "+new" in review
    assert "-OLD = 1" in review
    assert tools.log.reverted == []


def test_plan_mode_command_cannot_write(workspace):
    tools = make_tools(workspace, "plan")
    result = run(tools, "open('src/calc.py', 'a').write('x')")
    assert "PermissionError" in result.stderr
    assert tools.log.files == {}
    assert tools.log.summary() is None


def test_feature_mode_sandbox_blocks_test_edits(workspace):
    tools = make_tools(workspace, "feature")
    result = run(tools, """
for path in ("tests/test_calc.py", "src/calc.py"):
    try:
        open(path, "a").write("# x\\n")
        print(path, "ALLOWED")
    except PermissionError:
        print(path, "blocked")
""")
    assert result.stdout.split("\n")[:2] == ["tests/test_calc.py blocked", "src/calc.py ALLOWED"], result.stderr
    assert (workspace / "tests/test_calc.py").read_text() == "def test_add():\n    pass\n"
    assert list(tools.log.files) == ["src/calc.py"]


def test_backstop_reverts_changes_the_sandbox_missed(workspace, monkeypatch):
    # Simulate a gap in the sandbox: grant the whole workspace even though the mode protects tests.
    monkeypatch.setattr(tools_module, "sandbox_rules", lambda root, mode: {str(root): "rw"})
    tools = make_tools(workspace, "feature")
    run(tools, """
open("tests/test_calc.py", "w").write("def test_add():\\n    assert True\\n")
open("tests/test_new.py", "w").write("x = 1\\n")
open("src/test_sneaky.py", "w").write("x = 1\\n")
open("src/calc.py", "a").write("# allowed\\n")
""")
    assert (workspace / "tests/test_calc.py").read_text() == "def test_add():\n    pass\n"
    assert not (workspace / "tests/test_new.py").exists()
    assert not (workspace / "src/test_sneaky.py").exists()
    assert (workspace / "src/calc.py").read_text().endswith("# allowed\n")
    assert sorted(tools.log.reverted) == ["src/test_sneaky.py", "tests/test_calc.py", "tests/test_new.py"]
    assert "reverted changes the mode doesn't allow" in tools.log.summary()


def test_large_files(workspace, monkeypatch):
    big = "x" * (SNAPSHOT_MAX_FILE_BYTES + 10)
    (workspace / "src/data.txt").write_text(big)
    (workspace / "tests/fixture.txt").write_text(big)
    monkeypatch.setattr(tools_module, "sandbox_rules", lambda root, mode: {str(root): "rw"})
    tools = make_tools(workspace, "feature")
    run(tools, "open('src/data.txt', 'a').write('y'); open('tests/fixture.txt', 'a').write('y')")
    assert tools.log.too_large == {"src/data.txt"}
    assert "src/data.txt: changed by a command (too large to show)" in tools.run("review_changes", {})
    assert tools.log.reverted == ["tests/fixture.txt (could not restore: too large)"]


def test_tool_edit_and_command_change_reviewed_together(workspace):
    tools = make_tools(workspace, "edit")
    tools.run("edit_file", {"path": "src/calc.py", "old_text": "return a + b", "new_text": "return b + a"})
    run(tools, "open('src/calc.py', 'a').write('# by command\\n')")
    review = tools.run("review_changes", {})
    assert "-    return a + b" in review and "+    return b + a" in review and "+# by command" in review


def test_command_environment_uses_workspace_venv(workspace):
    (workspace / ".venv/bin").mkdir(parents=True)
    tools = make_tools(workspace, "plan")
    result = run(tools, "import os; print(os.environ['VIRTUAL_ENV']); print(os.environ['PATH'].split(':')[0])")
    assert result.stdout.split() == [str(workspace / ".venv"), str(workspace / ".venv/bin")], result.stderr
