"""Tests for the write_file guard against large file rewrites."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from permissions import Mode
from tools import Tools


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def make_tools(root):
    return Tools(root, 20000, lambda path, diff: None, [], Mode(name="edit", description="d", write=["**"]))


def test_new_file_can_be_created(workspace):
    """Test that new files can be created without issues."""
    tools = make_tools(workspace)

    # Create a new file
    result = tools.run("write_file", {"path": "new_file.py", "content": "print('hello')\n"})
    assert "Created new_file.py" in result
    assert "1 lines" in result


def test_existing_small_file_can_be_overwritten(workspace):
    """Test that existing small files can be overwritten."""
    tools = make_tools(workspace)

    # Create a small file first
    tools.run("write_file", {"path": "small_file.py", "content": "print('hello')\n"})

    # Overwrite it - this should work
    result = tools.run("write_file", {"path": "small_file.py", "content": "print('world')\n"})
    assert "Overwrote small_file.py" in result
    assert "1 lines" in result


def test_existing_large_file_cannot_be_overwritten(workspace):
    """Test that existing large files cannot be overwritten with write_file."""
    tools = make_tools(workspace)

    # Create a large file (more than MAX_REWRITE_LINES)
    large_content = "\n".join([f"line {i}" for i in range(150)]) + "\n"
    tools.run("write_file", {"path": "large_file.py", "content": large_content})

    # Try to overwrite it - this should fail
    result = tools.run("write_file", {"path": "large_file.py", "content": "print('new')\n"})

    assert result.startswith("Error:")
    assert "has 150 lines" in result
    assert "rewriting it in full risks losing code" in result
    assert "Use edit_file" in result

    # Verify the file is unchanged
    content = (workspace / "large_file.py").read_text()
    assert content == large_content


def test_edit_file_still_works_on_large_files(workspace):
    """Test that edit_file still works on large files."""
    tools = make_tools(workspace)

    # Create a large file
    large_content = "\n".join([f"line {i}" for i in range(150)]) + "\n"
    tools.run("write_file", {"path": "large_file.py", "content": large_content})

    # Use edit_file to modify part of it - this should work
    result = tools.run("edit_file", {
        "path": "large_file.py",
        "old_text": "line 50",
        "new_text": "line 50 modified"
    })

    assert "Edited large_file.py" in result
    assert "line 50 modified" in result

    # Verify the file was actually changed
    content = (workspace / "large_file.py").read_text()
    assert "line 50 modified" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
