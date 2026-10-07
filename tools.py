import re
from pathlib import Path

IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build"}
MAX_LIST_ENTRIES = 500
MAX_SEARCH_MATCHES = 200


class ToolError(Exception):
    pass


class Tools:
    def __init__(self, root: Path, max_output_chars: int):
        self.root = root.resolve()
        self.max_output_chars = max_output_chars
        self.registry = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search": self.search,
        }

    def run(self, name: str, args: dict) -> str:
        func = self.registry.get(name)
        if func is None:
            return f"Error: unknown tool '{name}'"
        try:
            output = func(**args)
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"
        except ToolError as e:
            return f"Error: {e}"
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + f"\n... [truncated, {len(output)} chars total]"
        return output

    def _resolve(self, path: str) -> Path:
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ToolError(f"path '{path}' is outside the workspace")
        if not resolved.exists():
            raise ToolError(f"path '{path}' does not exist")
        return resolved

    def _walk(self, base: Path, pattern: str = "*"):
        for p in sorted(base.rglob(pattern)):
            rel = p.relative_to(self.root)
            if any(part in IGNORED_DIRS for part in rel.parts):
                continue
            if p.is_file():
                yield p

    def list_files(self, path: str = ".", pattern: str = "*") -> str:
        base = self._resolve(path)
        if base.is_file():
            return str(base.relative_to(self.root))
        files = []
        for p in self._walk(base, pattern):
            files.append(str(p.relative_to(self.root)))
            if len(files) >= MAX_LIST_ENTRIES:
                files.append(f"... [stopped after {MAX_LIST_ENTRIES} entries]")
                break
        return "\n".join(files) or "No files found."

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"'{path}' is not a file")
        try:
            lines = target.read_text().splitlines()
        except UnicodeDecodeError:
            raise ToolError(f"'{path}' is not a text file")
        start = max(start_line, 1)
        end = min(end_line or len(lines), len(lines))
        numbered = [f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1)]
        return f"{path} (lines {start}-{end} of {len(lines)})\n" + "\n".join(numbered)

    def search(self, pattern: str, path: str = ".", file_pattern: str = "*") -> str:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        base = self._resolve(path)
        files = [base] if base.is_file() else self._walk(base, file_pattern)
        matches = []
        for p in files:
            try:
                lines = p.read_text().splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            rel = p.relative_to(self.root)
            for n, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append(f"{rel}:{n}: {line.strip()}")
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        matches.append(f"... [stopped after {MAX_SEARCH_MATCHES} matches]")
                        return "\n".join(matches)
        return "\n".join(matches) or "No matches."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the workspace recursively. Common build/cache directories are skipped.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory relative to the workspace root. Defaults to '.'."},
                    "pattern": {"type": "string", "description": "Glob for file names, e.g. '*.py'. Defaults to '*'."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file, returned with line numbers. Use start_line/end_line for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root."},
                    "start_line": {"type": "integer", "description": "First line to read (1-based)."},
                    "end_line": {"type": "integer", "description": "Last line to read (inclusive)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search file contents with a Python regular expression. Returns 'file:line: text' matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to search for."},
                    "path": {"type": "string", "description": "Directory or file to search. Defaults to '.'."},
                    "file_pattern": {"type": "string", "description": "Glob to restrict which files are searched, e.g. '*.py'."},
                },
                "required": ["pattern"],
            },
        },
    },
]
