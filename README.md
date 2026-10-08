# Tinker - A Coding Agent

Tinker is a Python-based coding agent that leverages Ollama for language model capabilities. It runs a local model through Ollama (default `ornith`, a 9B model; see EVALUATION_RESULTS.md) and works on one workspace directory.

## Overview

Tinker allows you to interact with code repositories through natural language prompts, enabling tasks like:
- Code exploration and analysis
- Implementing new features
- Fixing bugs
- Writing documentation
- Refactoring code

The agent operates in different modes that control what actions it can perform, providing safety and control over file modifications.

## Features

- **Multi-mode Operation**: Different modes for various tasks (plan, edit, docs, feature)
- **Permission System**: Granular control over read/write access to files
- **Interactive Session Management**: Save and resume coding sessions
- **Change Approval**: Interactive or automatic approval of file changes
- **Code Linting**: Automatic linting of modified Python files
- **Sandboxed Commands**: Run tests and linters with no network and no access outside the workspace

## Installation

1. Create and activate a Python virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Install the required Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Linux only (x86_64): commands run in a sandbox that needs Landlock and unprivileged user namespaces.

## Configuration

The agent uses `config.json` for configuration:

```json
{
  "host": "http://192.168.0.174:11434",
  "model": "ornith:latest",
  "num_ctx": 65536,
  "temperature": 0.2,
  "max_iterations": 40,
  "max_tool_output_chars": 20000,
  "sessions_dir": "~/.tinker/sessions",
  "lint_args": ["--select", "F,E9,PLE,W291,W293,Q000"],
  "default_mode": "plan",
  "commands": {
    "timeout_seconds": 120,
    "max_output_chars": 20000,
    "allow_project_auto_approve": false
  },
  "modes": {
    "plan": {
      "description": "Explore and plan; no changes",
      "read": ["**"],
      "write": [],
      "run": ["pytest*", "python -m pytest*", "ruff check*"]
    },
    "edit": {
      "description": "Change anything in the workspace",
      "read": ["**"],
      "write": ["**"],
      "approve": "ask",
      "run": ["pytest*", "python -m pytest*", "ruff check*"]
    },
    "docs": {
      "description": "Read everything; write documentation only",
      "read": ["**"],
      "write": ["docs/**", "*.md"],
      "approve": "auto"
    },
    "feature": {
      "description": "Implement features without changing the tests",
      "read": ["**"],
      "write": ["**"],
      "deny_write": ["tests/**", "**/test_*.py"],
      "instructions": "The tests define the required behaviour. Make the code pass them; if a test looks wrong, say so instead of working around it.",
      "run": ["pytest*", "python -m pytest*", "ruff check*"]
    }
  }
}
```

A project can adjust modes in its own .tinker/config.json (see "Project configuration" below).

## Usage

### Starting Tinker

```bash
python main.py [workspace]
```

- `workspace`: Directory the agent works in (default: current directory)
- `--config`: Path to config file (default: config.json)
- `--resume SESSION_ID`: Continue a previous session
- `--sessions`: List previous sessions and exit
- `--mode NAME`: Starting mode (plan, edit, docs, feature)
- `-p MESSAGE` or `--prompt MESSAGE`: Run one request and exit (-p - reads from stdin; answer goes to stdout, progress to stderr)

In a one-shot run without --mode, Tinker uses default_mode; with an explicit --mode, that mode's changes and commands are approved automatically.

### Modes

1. **plan**: Explore and plan; no changes allowed (read only; may run tests)
2. **edit**: Change anything in the workspace (requires approval for changes)
3. **docs**: Read everything; write documentation only (approved automatically, no commands)
4. **feature**: Implement features without changing tests (change anything except tests/** and **/test_*.py; may run tests)

### Interactive Commands

While running interactively, you can use these commands:
- `/mode`: List the modes
- `/mode NAME` or `/NAME`: Switch mode
- `/auto_approve`: Toggle automatic approval of file changes and commands for this run
- `exit` or `quit`: Leave the session

## Key Components

### Core Files
- `main.py`: Command line and interactive loop
- `agent.py`: Talks to the model, runs tools, checks lint and failed commands before accepting an answer
- `context.py`: Keeps the conversation within the model's context window
- `tools.py`: The tools the model can use
- `permissions.py`: Mode path rules, and turning them into sandbox rules
- `sandbox.py`: Runs commands with Landlock, seccomp and namespaces
- `config.py`: Loads config.json and a project's .tinker/config.json
- `session.py`: Saves and resumes sessions

### Tools
- `list_files`, `read_file`, `search`
- `edit_file`, `write_file` (refuses to overwrite files over 100 lines)
- `review_changes` (diff of this request's changes)
- `lint` (ruff)
- `run_command` (only commands the mode allows)

## Running commands

Commands run without a shell, in a sandbox where they can read the workspace, write only what the mode allows, use a private temporary directory, read system directories, and have no network or access to your home directory. Changes a mode doesn't allow are undone afterwards. Extra read access and network access can only be granted in the global config.json (commands.extra_read, commands.network, commands.projects).

## Project configuration

A project's .tinker/config.json may add or change modes and set default_mode, for paths inside the project only; automatic approval from a project config needs "allow_project_auto_approve": true in the global config; anything else is ignored with a warning.

## Development

Run the tests with `.venv/bin/python -m pytest tests`. Tests that start a sandbox skip themselves when run inside Tinker's own sandbox, so run them directly after changing sandbox code.

## License

This project is licensed under the MIT License.
