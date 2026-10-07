"""Turning a mode's path patterns into sandbox rules, and checking those rules in a real sandbox."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sandbox  # noqa: E402
from permissions import Mode, could_match_under, matches_everything_under, sandbox_rules  # noqa: E402

MODES = {
    "plan": Mode(name="plan", description="d", write=[]),
    "edit": Mode(name="edit", description="d", write=["**"]),
    "docs": Mode(name="docs", description="d", write=["docs/**", "*.md"]),
    "feature": Mode(name="feature", description="d", write=["**"], deny_write=["tests/**", "**/test_*.py"]),
    "guarded": Mode(name="guarded", description="d", write=["**"], deny_read=[".env", "secrets/**"]),
}
FILES = [
    ".git/config", ".venv/bin/python", "calc.py", "README.md", ".env", "docs/guide.md", "docs/img/a.png",
    "tests/test_calc.py", "src/pkg/mod.py", "src/pkg/test_mod.py", "secrets/key.txt",
]


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    for name in FILES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(name)
    (tmp_path / "outside").mkdir()
    (root / "link").symlink_to(tmp_path / "outside")
    return root


def relative_rules(root, mode):
    return {str(Path(path).relative_to(root)): kind for path, kind in sandbox_rules(root, MODES[mode]).items()}


@pytest.mark.parametrize("parts, patterns, everything, could", [
    ([], ["**"], True, True),
    (["docs"], ["docs/**"], True, True),
    (["docs", "img"], ["docs/**"], True, True),
    (["tests"], ["docs/**"], False, False),
    ([], ["*.md"], False, True),
    (["src"], ["*.md"], False, False),
    (["src"], ["**/test_*.py"], False, True),
    (["docs"], ["docs/*.md"], False, True),
])
def test_pattern_coverage(parts, patterns, everything, could):
    assert matches_everything_under(parts, patterns) == everything
    assert could_match_under(parts, patterns) == could


def test_plan_mode_makes_the_workspace_read_only(workspace):
    assert relative_rules(workspace, "plan") == {".": "ro"}


def test_edit_mode_protects_git_and_venv(workspace):
    assert relative_rules(workspace, "edit") == {
        ".": "list", ".env": "rw", ".git": "ro", ".venv": "ro", "README.md": "rw", "calc.py": "rw",
        "docs": "rw", "secrets": "rw", "src": "rw", "tests": "rw",
    }


def test_docs_mode(workspace):
    assert relative_rules(workspace, "docs") == {
        ".": "list", ".env": "ro", ".git": "ro", ".venv": "ro", "README.md": "rw", "calc.py": "ro",
        "docs": "rw", "secrets": "ro", "src": "ro", "tests": "ro",
    }


def test_feature_mode_protects_tests_everywhere(workspace):
    # "**/test_*.py" could match in any directory, so no directory can be granted as a whole.
    assert relative_rules(workspace, "feature") == {
        ".": "list", ".env": "rw", ".git": "ro", ".venv": "ro", "README.md": "rw", "calc.py": "rw",
        "docs": "list", "docs/guide.md": "rw", "docs/img": "list", "docs/img/a.png": "rw",
        "secrets": "list", "secrets/key.txt": "rw",
        "src": "list", "src/pkg": "list", "src/pkg/mod.py": "rw", "src/pkg/test_mod.py": "ro",
        "tests": "ro",
    }


def test_deny_read_hides_files(workspace):
    rules = relative_rules(workspace, "guarded")
    assert ".env" not in rules
    assert "secrets" not in rules and "secrets/key.txt" not in rules
    assert rules["calc.py"] == "rw"


def test_links_get_no_rule(workspace):
    for mode in MODES:
        assert "link" not in relative_rules(workspace, mode)


def run_in(root, mode, code):
    policy = sandbox.Policy(rules=sandbox_rules(root, MODES[mode]), cwd=str(root), env={"PATH": "/usr/bin:/bin"})
    return sandbox.run(["/usr/bin/python3", "-c", code], policy, 30)


ATTEMPTS = """
import os
for label, path in [("edit calc.py", "calc.py"), ("edit tests/test_calc.py", "tests/test_calc.py"),
                    ("edit src/pkg/test_mod.py", "src/pkg/test_mod.py"), ("create docs/new.md", "docs/new.md"),
                    ("create src/pkg/new.py", "src/pkg/new.py"), ("edit .git/config", ".git/config"),
                    ("write through link", "link/planted.txt")]:
    try:
        open(path, "a").write("x")
        print(label, "ALLOWED")
    except OSError:
        print(label, "blocked")
"""


@pytest.mark.parametrize("mode, allowed", [
    ("plan", set()),
    ("edit", {"edit calc.py", "edit tests/test_calc.py", "edit src/pkg/test_mod.py", "create docs/new.md",
              "create src/pkg/new.py"}),
    ("docs", {"create docs/new.md"}),
    ("feature", {"edit calc.py"}),
])
def test_rules_hold_in_the_sandbox(workspace, mode, allowed):
    result = run_in(workspace, mode, ATTEMPTS)
    assert result.returncode == 0, result.stderr
    got = {line.rsplit(" ", 1)[0] for line in result.stdout.splitlines() if line.endswith("ALLOWED")}
    assert got == allowed
    assert not (workspace.parent / "outside" / "planted.txt").exists()
