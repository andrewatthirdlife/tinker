"""Grants only the global config can make: extra read access outside the checkout, and network access."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import CommandSettings  # noqa: E402
from permissions import Mode  # noqa: E402
from tools import Tools  # noqa: E402


@pytest.fixture
def dirs(tmp_path):
    project, other, toolchain = tmp_path / "project", tmp_path / "other", tmp_path / "toolchain"
    for d in (project, other, toolchain):
        d.mkdir()
    (toolchain / "tool.txt").write_text("toolchain")
    return project, other, toolchain


def test_grants_for_all_projects_and_one(dirs):
    project, other, toolchain = dirs
    settings = CommandSettings(extra_read=[str(toolchain)], projects={
        str(project): {"extra_read": ["/srv/data"], "network": True},
    })
    assert settings.grants_for(project) == ([str(toolchain), "/srv/data"], True)
    assert settings.grants_for(other) == ([str(toolchain)], False)


def test_project_paths_are_matched_after_resolving(dirs, monkeypatch):
    project, _, _ = dirs
    monkeypatch.setenv("HOME", str(project.parent))
    settings = CommandSettings(projects={"~/project/../project": {"network": True}})
    assert settings.grants_for(project) == ([], True)


@pytest.mark.parametrize("grants", [{"network": "yes"}, {"extra_read": "/srv"}, {"write": ["/"]}, "everything"])
def test_invalid_project_grants(grants):
    with pytest.raises(ValueError, match="commands.projects"):
        CommandSettings(projects={"/some/project": grants})


skip_in_sandbox = pytest.mark.skipif("TINKER_SANDBOXED" in os.environ, reason="can't start a sandbox inside a sandbox")


def run(project, settings, code):
    mode = Mode(name="m", description="d", run=["python3 -c*"])
    tools = Tools(project, 100_000, lambda p, d: None, [], mode, lambda c: None, settings)
    return tools.run("run_command", {"command": f"python3 -c '{code}'"})


@skip_in_sandbox
def test_extra_read_is_readable_in_the_sandbox(dirs):
    project, _, toolchain = dirs
    code = f"print(open(\"{toolchain}/tool.txt\").read())"
    assert "PermissionError" in run(project, CommandSettings(), code)
    assert "--- stdout ---\ntoolchain" in run(project, CommandSettings(extra_read=[str(toolchain)]), code)


@skip_in_sandbox
def test_extra_read_is_not_writable(dirs):
    project, _, toolchain = dirs
    out = run(project, CommandSettings(extra_read=[str(toolchain)]), f"open(\"{toolchain}/tool.txt\", \"w\")")
    assert "PermissionError" in out


@skip_in_sandbox
def test_network_only_for_the_granted_project(dirs):
    project, other, _ = dirs
    settings = CommandSettings(projects={str(project): {"network": True}})
    code = "import socket; print(len(socket.if_nameindex()) > 1)"
    assert "--- stdout ---\nTrue" in run(project, settings, code)
    assert "--- stdout ---\nFalse" in run(other, settings, code)
