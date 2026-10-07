# Tinker - A Coding Agent

Tinker is a Python-based coding agent that leverages Ollama for language model capabilities. It's designed to work locally with an existing Ollama installation running on a separate machine in the network.

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
- **Error Handling**: Robust error handling for Ollama connection issues

## Installation

1. Ensure you have Ollama installed and running on a separate machine in your network
2. Install the required Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

The agent uses `config.json` for configuration:

```json
{
  "host": "http://192.168.0.174:11434",
  "model": "qwen3-coder:latest",
  "num_ctx": 65536,
  "temperature": 0.2,
  "max_iterations": 40,
  "max_tool_output_chars": 20000,
  "sessions_dir": "~/.tinker/sessions",
  "lint_args": ["--select", "F,E9,PLE,W291,W293,Q000"],
  "default_mode": "plan",
  "modes": {
    "plan": {
      "description": "Explore and plan; no changes",
      "read": ["**"],
      "write": []
    },
    "edit": {
      "description": "Change anything in the workspace",
      "read": ["**"],
      "write": ["**"],
      "approve": "ask"
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
      "instructions": "The tests define the required behaviour. Make the code pass them; if a test looks wrong, say so instead of working around it."
    }
  }
}
```

## Usage

### Starting Tinker

```bash
python main.py [workspace]
```

- `workspace`: Directory the agent works in (default: current directory)
- `--config`: Path to config file (default: config.json)
- `--resume SESSION_ID`: Continue a previous session
- `--sessions`: List previous sessions and exit
- `--mode MODE`: Starting mode (plan, edit, docs, feature)
- `-p MESSAGE` or `--prompt MESSAGE`: Run one request and exit

### Modes

1. **plan**: Explore and plan; no changes allowed
2. **edit**: Change anything in the workspace (requires approval for changes)
3. **docs**: Read everything; write documentation only (auto-approve changes)
4. **feature**: Implement features without changing tests (auto-approve changes, but denies test modifications)

### Interactive Commands

While running interactively, you can use these commands:
- `/plan`: Switch to plan mode
- `/edit`: Switch to edit mode  
- `/docs`: Switch to docs mode
- `/feature`: Switch to feature mode
- `/auto_approve`: Toggle auto-approval of file changes (this run only)

## Key Components

### Core Files
- `main.py`: Main entry point and interactive interface
- `agent.py`: Core agent logic with LLM interaction
- `config.py`: Configuration loading and validation
- `session.py`: Session management for saving/resuming conversations
- `tools.py`: Tool implementations for file operations
- `permissions.py`: Permission system for read/write access control

### Tools Available
The agent can perform various operations:
- File reading (`read_file`, `list_files`, `search`)
- File editing (`edit_file`, `write_file`)
- Code analysis and linting
- Session management

## Network Requirements

Tinker requires an Ollama installation running on a separate machine in the network. If you cannot contact Ollama, it's most likely that the other machine has gone to sleep - wake it up before trying again.

## Code Quality Review

The Tinker agent demonstrates good software engineering practices with a well-structured codebase that follows Python best practices. Here's an assessment of its code quality:

### Strengths

**Modular Design**: The codebase is well-organized into distinct modules:
- `main.py` handles the interactive interface and command-line parsing
- `agent.py` contains the core LLM interaction logic 
- `config.py` manages configuration loading and validation
- `session.py` implements session management
- `tools.py` provides file operation tools with permission control
- `permissions.py` handles read/write access control

**Clear Separation of Concerns**: Each module has a specific responsibility, making the codebase maintainable and testable.

**Error Handling**: Robust error handling for Ollama connection issues, malformed tool calls, and other potential failures.

**Security Considerations**: The permission system prevents unauthorized file modifications through different modes (plan, edit, docs, feature).

**Documentation**: Comprehensive inline documentation and a detailed README that explains the system's functionality.

### Areas for Improvement

**Code Duplication**: Some error handling patterns are repeated across modules. A centralized error handler could reduce redundancy.

**Testing Coverage**: While the code is well-structured, there's no explicit mention of automated tests in the repository, which would be beneficial for maintaining quality.

**Configuration Management**: The configuration system is good but could benefit from more validation and default value handling.

**Tool Call Parsing**: The XML parsing logic for tool calls (in `agent.py`) is somewhat complex and could potentially be simplified or made more robust.

### Overall Assessment

The codebase demonstrates solid Python development practices with clean separation of concerns, good error handling, and a well-thought-out permission system. The modular approach makes it easy to understand and extend. The agent's design philosophy of requiring explicit approval for file changes is a strong security feature that prevents unintended modifications.

## License

This project is licensed under the MIT License.
