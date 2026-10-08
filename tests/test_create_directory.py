"""create_directory makes folders the mode allows writing in, and refused commands point to the matching tool."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from permissions import Mode  # noqa: E402
from tools import Tools  # noqa: E402


def make_tools(root, write=("**",), confirm=lambda path, diff: None):
    return Tools(root, 20000, confirm, [], Mode(name="edit", description="d", write=list(write)))


def test_creates_nested_folders(tmp_path):
    tools = make_tools(tmp_path)
    assert tools.run("create_directory", {"path": "demo/sub"}) == "Created the folder demo/sub."
    assert (tmp_path / "demo" / "sub").is_dir()
    assert tools.log.files == {"demo/sub/": "created"}


def test_existing_folder_is_not_an_error(tmp_path):
    (tmp_path / "demo").mkdir()
    assert make_tools(tmp_path).run("create_directory", {"path": "demo"}) == "demo already exists."


def test_mode_must_allow_writing_inside(tmp_path):
    tools = make_tools(tmp_path, write=["docs/**"])
    assert "Created" in tools.run("create_directory", {"path": "docs/api"})
    assert "read-only in edit mode" in tools.run("create_directory", {"path": "src"})
    assert not (tmp_path / "src").exists()


def test_outside_workspace_and_ignored_folders_are_refused(tmp_path):
    tools = make_tools(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    assert "outside the workspace" in tools.run("create_directory", {"path": "../escape"})
    assert "not allowed" in tools.run("create_directory", {"path": "node_modules/x"})


def test_needs_approval(tmp_path):
    asked = []
    tools = make_tools(tmp_path, confirm=lambda path, diff: asked.append(path) or "no folders please")
    assert tools.run("create_directory", {"path": "demo"}) == "Error: no folders please"
    assert asked == ["demo/"] and not (tmp_path / "demo").exists()


def test_plan_mode_has_no_create_directory(tmp_path):
    tools = Tools(tmp_path, 20000, lambda p, d: None, [], Mode(name="plan", description="d", write=[]))
    assert "read-only in plan mode" in tools.run("create_directory", {"path": "demo"})


def test_refused_mkdir_suggests_the_tool(tmp_path):
    result = make_tools(tmp_path).run("run_command", {"command": "mkdir demo"})
    assert "Use the create_directory tool instead of mkdir." in result
