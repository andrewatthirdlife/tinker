import ast
import difflib
import re
import subprocess
from pathlib import Path
from typing import Callable

IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build"}
MAX_LIST_ENTRIES = 500
MAX_SEARCH_MATCHES = 200
EDIT_CONTEXT_LINES = 3

# Receives (path, diff); returns None to approve, or a rejection message for the model.
ConfirmWrite = Callable[[str, str], str | None]


class ToolError(Exception):
    pass


class Tools:
    def __init__(self, root: Path, max_output_chars: int, confirm_write: ConfirmWrite):
        self.root = root.resolve()
        self.max_output_chars = max_output_chars
        self.confirm_write = confirm_write
        self.registry = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search": self.search,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "git_changes": self.git_changes,
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

    def _inside(self, path: str) -> Path:
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ToolError(f"path '{path}' is outside the workspace")
        return resolved

    def _resolve(self, path: str) -> Path:
        resolved = self._inside(path)
        if not resolved.exists():
            raise ToolError(f"path '{path}' does not exist")
        return resolved

    def _resolve_writable(self, path: str) -> Path:
        resolved = self._inside(path)
        if any(part in IGNORED_DIRS for part in resolved.relative_to(self.root).parts):
            raise ToolError(f"writing to '{path}' is not allowed")
        if resolved.is_dir():
            raise ToolError(f"'{path}' is a directory")
        return resolved

    def _read_text(self, target: Path, path: str) -> str:
        if not target.is_file():
            raise ToolError(f"'{path}' is not a file")
        try:
            return target.read_text()
        except UnicodeDecodeError:
            raise ToolError(f"'{path}' is not a text file")

    def _walk(self, base: Path, pattern: str = "*"):
        for p in sorted(base.rglob(pattern)):
            rel = p.relative_to(self.root)
            if any(part in IGNORED_DIRS for part in rel.parts):
                continue
            if p.is_file():
                yield p

    def _apply(self, path: str, target: Path, old: str, new: str) -> str | None:
        """Ask for approval, write the file, and return a syntax warning if the result doesn't parse."""
        if old == new:
            raise ToolError("the change would not modify the file")
        diff = "".join(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{path}", f"b/{path}"
        ))
        rejection = self.confirm_write(path, diff)
        if rejection is not None:
            raise ToolError(rejection)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new)
        if target.suffix == ".py":
            try:
                ast.parse(new)
            except SyntaxError as e:
                return f"WARNING: the file was written but has a syntax error at line {e.lineno}: {e.msg}"
        return None

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
        lines = self._read_text(self._resolve(path), path).splitlines()
        start = max(start_line, 1)
        end = min(end_line or len(lines), len(lines))
        numbered = [f"{i:>5}\t{lines[i - 1]}" for i in range(start, end + 1)]
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

    def write_file(self, path: str, content: str) -> str:
        target = self._resolve_writable(path)
        existed = target.exists()
        old = self._read_text(target, path) if existed else ""
        if content and not content.endswith("\n"):
            content += "\n"
        warning = self._apply(path, target, old, content)
        result = f"{'Overwrote' if existed else 'Created'} {path} ({len(content.splitlines())} lines)."
        return f"{result}\n{warning}" if warning else result

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        target = self._resolve_writable(path)
        if not target.exists():
            raise ToolError(f"'{path}' does not exist; use write_file to create it")
        if not old_text:
            raise ToolError("old_text must not be empty")
        old = self._read_text(target, path)

        count = old.count(old_text)
        if count > 1:
            raise ToolError(f"old_text matches {count} times; include more surrounding lines to make it unique")
        if count == 1:
            start = old.index(old_text)
            end = start + len(old_text)
        else:
            start, end, offset = self._find_loose_match(old, old_text)
            new_text = _reindent(new_text, offset)
            if old[end - 1 : end] == "\n" and not new_text.endswith("\n"):
                new_text += "\n"

        new = old[:start] + new_text + old[end:]
        warning = self._apply(path, target, old, new)

        first = old[:start].count("\n") + 1
        last = first + new_text.count("\n")
        lines = new.splitlines()
        lo, hi = max(first - EDIT_CONTEXT_LINES, 1), min(last + EDIT_CONTEXT_LINES, len(lines))
        snippet = "\n".join(f"{i:>5}\t{lines[i - 1]}" for i in range(lo, hi + 1))
        result = f"Edited {path}. Lines {lo}-{hi} now read:\n{snippet}"
        return f"{result}\n{warning}" if warning else result

    def _find_loose_match(self, text: str, old_text: str) -> tuple[int, int, int]:
        """Find old_text ignoring trailing whitespace and a consistent indentation difference.

        Returns (start, end, offset) where offset is the indentation to add to new_text.
        """
        lines = text.splitlines(keepends=True)
        target = old_text.splitlines()
        n = len(target)
        hits = []
        for i in range(len(lines) - n + 1):
            offset = _indent_offset(lines[i : i + n], target)
            if offset is not None:
                hits.append((i, offset))
        if not hits:
            raise ToolError("old_text not found; re-read the file and copy the exact text, including indentation")
        if len(hits) > 1:
            raise ToolError(f"old_text matches {len(hits)} times; include more surrounding lines to make it unique")
        i, offset = hits[0]
        start = sum(len(line) for line in lines[:i])
        return start, start + sum(len(line) for line in lines[i : i + n]), offset

    def _git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise ToolError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout

    def git_changes(self, path: str = ".") -> str:
        pathspec = str(self._inside(path).relative_to(self.root))
        status = self._git("status", "--short", "--", pathspec)
        try:
            diff = self._git("diff", "HEAD", "--", pathspec)
        except ToolError:  # no commits yet
            diff = self._git("diff", "--", pathspec)
        if not status and not diff:
            return "No uncommitted changes."
        return f"Status (?? = untracked, M = modified, A = added, D = deleted):\n{status}\nDiff:\n{diff or '(none)'}"


def _indent_offset(file_lines: list[str], target: list[str]) -> int | None:
    """Indentation difference that makes target match file_lines, or None if they don't match."""
    offset = None
    for f, t in zip(file_lines, target):
        f, t = f.rstrip(), t.rstrip()
        if not f and not t:
            continue
        if f.lstrip() != t.lstrip():
            return None
        diff = (len(f) - len(f.lstrip())) - (len(t) - len(t.lstrip()))
        if offset is None:
            offset = diff
        elif diff != offset:
            return None
    return offset or 0


def _reindent(text: str, offset: int) -> str:
    if offset == 0:
        return text
    result = []
    for line in text.splitlines(keepends=True):
        if not line.strip():
            result.append(line)
        elif offset > 0:
            result.append(" " * offset + line)
        else:
            result.append(line[min(-offset, len(line) - len(line.lstrip(" "))) :])
    return "".join(result)


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
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file, or completely replace an existing one. Prefer edit_file for changes to existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root."},
                    "content": {"type": "string", "description": "The full content of the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace one exact piece of text in an existing file. old_text must match the file exactly "
                "(including indentation) and appear only once. Do not include line numbers from read_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root."},
                    "old_text": {"type": "string", "description": "The exact text to replace, with enough lines to be unique."},
                    "new_text": {"type": "string", "description": "The text to replace it with."},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_changes",
            "description": "Show uncommitted changes in the git repository: file status and the diff against the last commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Limit to this file or directory. Defaults to '.'."},
                },
            },
        },
    },
]
