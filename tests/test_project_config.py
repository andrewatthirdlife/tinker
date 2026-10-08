"""A project's .tinker/config.json can change anything inside its checkout, and nothing that reaches outside it."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import Config, apply_project_config  # noqa: E402

GLOBAL = {
    "host": "h", "model": "m", "num_ctx": 1, "temperature": 0, "max_iterations": 1, "max_tool_output_chars": 1,
    "sessions_dir": "/tmp/s", "lint_args": [], "default_mode": "plan",
    "modes": {
        "plan": {"description": "Plan", "write": []},
        "docs": {"description": "Docs", "write": ["docs/**"], "approve": "auto"},
        "feature": {"description": "Feature", "write": ["**"], "deny_write": ["tests/**"], "run": ["pytest*"]},
    },
}


def load(tmp_path, project, allow_auto=False):
    config = Config(**json.loads(json.dumps(GLOBAL)), commands={"allow_project_auto_approve": allow_auto})
    if project is not None:
        (tmp_path / ".tinker").mkdir()
        (tmp_path / ".tinker" / "config.json").write_text(project if isinstance(project, str) else json.dumps(project))
    return config, apply_project_config(config, tmp_path)


def test_no_project_config(tmp_path):
    config, warnings = load(tmp_path, None)
    assert warnings == []
    assert config.modes["docs"].write == ["docs/**"]


def test_project_extends_a_mode(tmp_path):
    config, warnings = load(tmp_path, {"modes": {"docs": {"write": ["documentation/**", "*.md"]}}})
    assert warnings == []  # docs is "auto" globally, and the project didn't set approve at all
    docs = config.modes["docs"]
    assert docs.write == ["documentation/**", "*.md"]
    assert docs.approve == "auto"
    assert docs.description == "Docs"


def test_project_adds_a_mode_and_default(tmp_path):
    config, warnings = load(tmp_path, {
        "modes": {"spec": {"description": "Specs only", "write": ["spec/**"], "run": ["pytest spec*"]}},
        "default_mode": "spec",
    })
    assert warnings == []
    assert config.modes["spec"].can_write("spec/a.md") and not config.modes["spec"].can_write("src/a.py")
    assert config.default_mode == "spec"


@pytest.mark.parametrize("pattern", ["/etc/**", "~/.ssh/**", "../other/**", "docs/../../x"])
def test_patterns_outside_the_checkout_are_dropped(tmp_path, pattern):
    config, warnings = load(tmp_path, {"modes": {"feature": {"write": ["src/**", pattern], "deny_read": [pattern]}}})
    assert config.modes["feature"].write == ["src/**"]
    assert config.modes["feature"].deny_read == []
    assert len(warnings) == 2 and all("reaches outside the checkout" in w for w in warnings)


def test_auto_approval_needs_global_permission(tmp_path):
    project = {"modes": {"feature": {"approve": "auto", "approve_commands": "auto"}, "new": {"description": "n", "approve": "auto"}}}
    config, warnings = load(tmp_path, project)
    assert config.modes["feature"].approve == "ask"
    assert config.modes["feature"].approve_commands == "ask"
    assert config.modes["new"].approve == "ask"
    assert len(warnings) == 3 and all("doesn't allow projects to turn on automatic approval" in w for w in warnings)

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    config, warnings = load(allowed, project, allow_auto=True)
    assert warnings == []
    assert config.modes["feature"].approve == "auto"


def test_other_settings_are_ignored(tmp_path):
    config, warnings = load(tmp_path, {"commands": {"network": True}, "host": "http://evil", "modes": {}})
    assert config.host == "h"
    assert sorted(warnings) == [
        ".tinker/config.json: ignored 'commands'; a project config may only set modes and default_mode",
        ".tinker/config.json: ignored 'host'; a project config may only set modes and default_mode",
    ]


@pytest.mark.parametrize("project, fragment", [
    ("not json", "must contain a JSON object"),
    ("[1, 2]", "must contain a JSON object"),
    ({"modes": {"feature": {"wrte": ["**"]}}}, "ignored, "),
    ({"modes": {"feature": {"write": "**"}}}, "ignored, "),
    ({"modes": {"brand_new": {"write": ["**"]}}}, "ignored, "),
    ({"default_mode": "nope"}, "ignored default_mode 'nope'"),
    ({"modes": ["feature"]}, "modes must be an object"),
    ({"modes": {"feature": "write everything"}}, "ignored, its settings must be an object"),
    ({"modes": {"feature": {"write": ["src/**", 7]}}}, "must be a list of patterns"),
])
def test_invalid_project_config(tmp_path, project, fragment):
    config, warnings = load(tmp_path, project)
    assert len(warnings) == 1 and fragment in warnings[0], warnings
    assert config.modes["feature"].write == ["**"]
    assert config.default_mode == "plan"
