"""create_directory and delete_file follow the file-writing rules; refused commands point to the matching tool."""

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


def test_delete_file(tmp_path):
    (tmp_path / "old.go").write_text("package main\n")
    asked = []
    tools = make_tools(tmp_path, confirm=lambda path, diff: asked.append((path, diff)))
    assert tools.run("delete_file", {"path": "old.go"}) == "Deleted old.go."
    assert not (tmp_path / "old.go").exists()
    assert asked[0][0] == "old.go" and "-package main" in asked[0][1]
    assert tools.log.files == {"old.go": "deleted"} and tools.log.originals == {"old.go": "package main\n"}
    assert "-package main" in tools.run("review_changes", {})


def test_delete_file_refusals(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "keep.txt").write_text("x\n")
    tools = make_tools(tmp_path, write=["docs/**"])
    assert "does not exist" in make_tools(tmp_path).run("delete_file", {"path": "nope.txt"})
    assert "is a directory" in make_tools(tmp_path).run("delete_file", {"path": "src"})
    assert "read-only in edit mode" in tools.run("delete_file", {"path": "keep.txt"})
    assert (tmp_path / "keep.txt").exists()


def test_file_created_then_deleted(tmp_path):
    tools = make_tools(tmp_path)
    tools.run("write_file", {"path": "tmp.txt", "content": "x\n"})
    tools.run("delete_file", {"path": "tmp.txt"})
    assert tools.log.files == {"tmp.txt": "created, then deleted"}


def test_refused_rm_suggests_delete_file(tmp_path):
    assert "Use the delete_file tool instead of rm." in make_tools(tmp_path).run("run_command", {"command": "rm a.go"})


def test_deny_delete_keeps_files_that_can_still_be_written(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_bug.py").write_text("def test_bug(): pass\n")
    mode = Mode(name="bugfix", description="d", write=["**"], deny_delete=["tests/**", "**/test_*.py"])
    tools = Tools(tmp_path, 20000, lambda p, d: None, [], mode)
    result = tools.run("delete_file", {"path": "tests/test_bug.py"})
    assert result.startswith("Error: tests/test_bug.py cannot be deleted in bugfix mode.")
    assert "You cannot delete files matching: tests/**, **/test_*.py." in result
    assert (tmp_path / "tests" / "test_bug.py").exists()
    assert tools.run("write_file", {"path": "tests/test_bug.py", "content": "def test_bug(): assert 1\n"}).startswith("Overwrote")
    (tmp_path / "scratch.txt").write_text("x\n")
    assert tools.run("delete_file", {"path": "scratch.txt"}) == "Deleted scratch.txt."
