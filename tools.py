import ast
import difflib
import os
import re
import shlex
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import sandbox
from config import CommandSettings
from permissions import Mode, sandbox_rules

IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build"}
MAX_LIST_ENTRIES = 500
MAX_SEARCH_MATCHES = 200
EDIT_CONTEXT_LINES = 3
NOT_FOUND_MIN_SIMILARITY = 0.4
NOT_FOUND_MAX_LINES = 30
SNAPSHOT_MAX_FILE_BYTES = 1_000_000  # larger files are tracked by size and time only, so they can't be restored
WRITE_TOOLS = {"edit_file", "write_file"}
LINT_PROBLEM_RE = re.compile(r"^\S+:\d+:\d+: (.*)$")  # "path:line:col: CODE message"
MAX_REWRITE_LINES = 100

# Receives (path, diff); returns None to approve, or a rejection message for the model.
ConfirmWrite = Callable[[str, str], str | None]

# Receives the command; returns None to approve, or a rejection message for the model.
ConfirmCommand = Callable[[str], str | None]


def refuse_commands(command: str) -> str | None:
    return "Commands can't be approved here."


class ToolError(Exception):
    pass


class Rejected(ToolError):
    pass


@dataclass
class ChangeLog:
    """What happened to files during one request, so it can be reported accurately."""

    files: dict[str, str] = field(default_factory=dict)  # path -> "created", "edited" or "rewritten"
    failed: int = 0
    rejected: int = 0
    lint_problems: int | None = None  # new problems remaining; None means lint was not run
    lint_existing: int = 0  # problems in the changed files that were already there before this request
    originals: dict[str, str | None] = field(default_factory=dict)  # content before this request; None = new file
    too_large: set[str] = field(default_factory=set)  # changed by commands, too large to show or restore
    reverted: list[str] = field(default_factory=list)  # changes by commands that the mode doesn't allow
    commands: list[str] = field(default_factory=list)  # commands run during this request, with their result
    last_failed_command: str | None = None  # the last command run, if it failed

    def record(self, path: str, kind: str) -> None:
        # Keep the most significant change: a new file stays "created"; an edited file can become "rewritten".
        if self.files.get(path) in (None, "edited"):
            self.files[path] = kind

    def summary(self) -> str | None:
        if not (self.files or self.failed or self.rejected or self.reverted or self.commands):
            return None
        parts = []
        if self.files:
            parts.append("changed " + ", ".join(f"{path} ({kind})" for path, kind in self.files.items()))
        else:
            parts.append("no files changed")
        if self.failed:
            parts.append(f"{self.failed} failed edit{'s' if self.failed != 1 else ''}")
        if self.rejected:
            parts.append(f"{self.rejected} change{'s' if self.rejected != 1 else ''} rejected by the user")
        if self.reverted:
            parts.append("reverted changes the mode doesn't allow: " + ", ".join(self.reverted))
        if self.lint_problems is not None:
            if self.lint_problems == 0:
                parts.append("lint: clean")
            else:
                problems = "problem remains" if self.lint_problems == 1 else "problems remain"
                parts.append(f"lint: {self.lint_problems} {problems}")
            if self.lint_existing:
                problems = "problem was" if self.lint_existing == 1 else "problems were"
                parts.append(f"{self.lint_existing} lint {problems} already there before this request")
        if self.commands:
            parts.append("ran " + ", ".join(self.commands))
        else:
            parts.append("the code has not been run or tested")
        return "; ".join(parts)


class Tools:
    def __init__(self, root: Path, max_output_chars: int, confirm_write: ConfirmWrite, lint_args: list[str], mode: Mode, confirm_command: ConfirmCommand = refuse_commands, command_settings: CommandSettings | None = None):
        self.root = root.resolve()
        self.max_output_chars = max_output_chars
        self.confirm_write = confirm_write
        self.lint_args = lint_args
        self.mode = mode
        self.confirm_command = confirm_command
        self.command_settings = command_settings or CommandSettings()
        self.log = ChangeLog()
        self.registry = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search": self.search,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "review_changes": self.review_changes,
            "lint": self.lint,
            "run_command": self.run_command,
        }

    def run(self, name: str, args: dict) -> str:
        func = self.registry.get(name)
        if func is None:
            return f"Error: unknown tool '{name}'"
        try:
            output = func(**args)
        except Rejected as e:
            self.log.rejected += 1
            return f"Error: {e}"
        except (TypeError, ToolError) as e:
            if name in WRITE_TOOLS:
                self.log.failed += 1
            if isinstance(e, TypeError):
                return f"Error: bad arguments for {name}: {e}"
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

        relative = str(resolved.relative_to(self.root))
        if not self.mode.can_write(relative):
            raise ToolError(f"{relative} is read-only in {self.mode.name} mode. {self.mode.describe_writes()}")

        return resolved

    def _check_readable(self, target: Path) -> None:
        relative = str(target.relative_to(self.root))
        if not self.mode.can_read(relative):
            raise ToolError(f"{relative} cannot be read in {self.mode.name} mode.")

    def _read_text(self, target: Path, path: str) -> str:
        if not target.is_file():
            raise ToolError(f"'{path}' is not a file")
        self._check_readable(target)
        try:
            return target.read_text()
        except UnicodeDecodeError:
            raise ToolError(f"'{path}' is not a text file")

    def _walk(self, base: Path, pattern: str = "*"):
        for p in sorted(base.rglob(pattern)):
            rel = p.relative_to(self.root)
            if any(part in IGNORED_DIRS for part in rel.parts):
                continue
            if not self.mode.can_read(str(rel)):
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
            raise Rejected(rejection)
        self.log.originals.setdefault(str(target.relative_to(self.root)), old if target.exists() else None)
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
        if base.is_file():
            self._check_readable(base)
            files = [base]
        else:
            files = self._walk(base, file_pattern)
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
        if target.suffix == ".py":
            content = _strip_trailing_whitespace(content, old)
        if content and not content.endswith("\n"):
            content += "\n"

        # Guard against rewriting large files in full
        if existed and target.exists():
            line_count = len(target.read_text().splitlines())
            if line_count > MAX_REWRITE_LINES:
                raise ToolError(f"{path} has {line_count} lines; rewriting it in full risks losing code. Use edit_file to change the parts that need changing.")

        warning = self._apply(path, target, old, content)
        self.log.record(path, "rewritten" if existed else "created")
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

        if target.suffix == ".py":
            new_text = _strip_trailing_whitespace(new_text, old[start:end])
        new = old[:start] + new_text + old[end:]
        warning = self._apply(path, target, old, new)
        self.log.record(path, "edited")

        first = old[:start].count("\n") + 1
        last = first + new_text.count("\n")
        lines = new.splitlines()
        lo, hi = max(first - EDIT_CONTEXT_LINES, 1), min(last + EDIT_CONTEXT_LINES, len(lines))
        snippet = "\n".join(f"{i:>5}\t{lines[i - 1]}" for i in range(lo, hi + 1))
        result = f"Edited {path}. Lines {lo}-{hi} now read:\n{snippet}"
        return f"{result}\n{warning}" if warning else result

    def _find_loose_match(self, text: str, old_text: str) -> tuple[int, int, int]:
        """Find old_text ignoring trailing whitespace, a consistent indentation difference and,
        failing that, the number of blank lines.

        Returns (start, end, offset) where offset is the indentation to add to new_text.
        """
        lines = text.splitlines(keepends=True)
        target = old_text.splitlines()
        hits = _window_matches(lines, list(range(len(lines))), target)
        if not hits:
            nonblank = [i for i, line in enumerate(lines) if line.strip()]
            hits = _window_matches(lines, nonblank, [t for t in target if t.strip()])
        if not hits:
            raise ToolError(_not_found_message(lines, target))
        if len(hits) > 1:
            raise ToolError(f"old_text matches {len(hits)} times; include more surrounding lines to make it unique")
        first, last, offset = hits[0]
        start = sum(len(line) for line in lines[:first])
        return start, start + sum(len(line) for line in lines[first : last + 1]), offset

    def review_changes(self) -> str:
        if not self.log.originals and not self.log.too_large:
            return "You have not changed any files during this request."
        diffs = []
        for rel, original in self.log.originals.items():
            if not self.mode.can_read(rel):
                diffs.append(f"{rel}: changed (not readable in {self.mode.name} mode)")
                continue
            current = (self.root / rel).read_text(errors="replace") if (self.root / rel).exists() else ""
            diff = "".join(difflib.unified_diff(
                (original or "").splitlines(keepends=True), current.splitlines(keepends=True),
                "/dev/null" if original is None else f"a/{rel}", f"b/{rel}",
            ))
            diffs.append(diff or f"{rel}: changed and then changed back; no difference now")
        diffs += [f"{rel}: changed by a command (too large to show)" for rel in sorted(self.log.too_large)]
        return "\n".join(diffs)

    def run_command(self, command: str) -> str:
        try:
            argv = shlex.split(command)
        except ValueError as e:
            raise ToolError(f"could not parse the command: {e}")
        if not argv:
            raise ToolError("the command is empty")
        line = " ".join(argv)
        if not self.mode.can_run(line):
            raise ToolError(f"{line!r} is not allowed in {self.mode.name} mode. {self.mode.describe_commands()}")
        rejection = self.confirm_command(line)
        if rejection is not None:
            raise Rejected(rejection)
        timeout = self.command_settings.timeout_seconds
        try:
            result = self.run_sandboxed(argv, timeout)
        except sandbox.SandboxError as e:
            raise ToolError(str(e))
        status = f"timed out after {timeout}s" if result.timed_out else f"exit code {result.returncode}"
        self.log.commands.append(f"{line} ({status})")
        if result.timed_out or result.returncode != 0:
            self.log.last_failed_command = f"{line} ({status})"
        else:
            self.log.last_failed_command = None
        half = self.command_settings.max_output_chars // 2
        return (f"$ {line}\n{status}, {result.seconds:.1f}s\n"
                f"--- stdout ---\n{_shorten(result.stdout, half)}\n--- stderr ---\n{_shorten(result.stderr, half)}")

    def run_sandboxed(self, argv: list[str], timeout: float) -> sandbox.Result:
        """Run a command in the sandbox under the current mode, then check and record what it changed."""
        before = self._snapshot()
        extra_read, network = self.command_settings.grants_for(self.root)
        rules = {**{path: "ro" for path in extra_read}, **sandbox_rules(self.root, self.mode)}
        policy = sandbox.Policy(rules=rules, cwd=str(self.root), env=self._command_env(), network=network)
        try:
            return sandbox.run(argv, policy, timeout)
        finally:
            self._check_command_changes(before)

    def _command_env(self) -> dict[str, str]:
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": os.environ.get("LANG", "C.UTF-8")}
        venv = self.root / ".venv"
        if venv.is_dir():
            env["PATH"] = f"{venv}/bin:{env['PATH']}"
            env["VIRTUAL_ENV"] = str(venv)
        return env

    def _snapshot(self) -> dict[str, bytes | tuple[int, int]]:
        """Content of the workspace's files (size and time for large ones), to see what a command changes."""
        files: dict[str, bytes | tuple[int, int]] = {}
        for directory, subdirs, names in os.walk(self.root):
            subdirs[:] = [d for d in subdirs if d not in IGNORED_DIRS and not os.path.islink(os.path.join(directory, d))]
            for name in names:
                path = Path(directory, name)
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                rel = str(path.relative_to(self.root))
                files[rel] = path.read_bytes() if stat.st_size <= SNAPSHOT_MAX_FILE_BYTES else (stat.st_size, stat.st_mtime_ns)
        return files

    def _check_command_changes(self, before: dict[str, bytes | tuple[int, int]]) -> None:
        """Record changes the mode allows; undo the rest. The sandbox should already stop them: this is a backstop."""
        after = self._snapshot()
        for rel in sorted(before.keys() | after.keys()):
            old, new = before.get(rel), after.get(rel)
            if old == new:
                continue
            if self.mode.can_write(rel):
                kind = "created" if old is None else "deleted" if new is None else "changed"
                self.log.record(rel, f"{kind} by a command")
                if isinstance(old, tuple):
                    self.log.too_large.add(rel)
                else:
                    self.log.originals.setdefault(rel, None if old is None else old.decode(errors="replace"))
                continue
            path = self.root / rel
            if isinstance(old, tuple):
                self.log.reverted.append(f"{rel} (could not restore: too large)")
                continue
            if old is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(old)
            self.log.reverted.append(rel)

    def _ruff(self, paths: list[str], text: str | None = None) -> list[str]:
        """Run ruff on files, or on text as the content of paths[0], and return its problem lines."""
        target = ["--stdin-filename", paths[0], "-"] if text is not None else paths
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--output-format=concise", "--no-cache", *self.lint_args, *target],
            cwd=self.root, capture_output=True, text=True, input=text,
        )
        if result.returncode not in (0, 1):
            raise ToolError(result.stderr.strip())
        return [line.replace("[*] ", "") for line in result.stdout.splitlines() if LINT_PROBLEM_RE.match(line)]

    def lint(self, path: str = ".") -> str:
        resolved = self._resolve(path)
        if resolved.is_file():
            self._check_readable(resolved)
            files = [str(resolved.relative_to(self.root))]
        else:
            files = [str(p.relative_to(self.root)) for p in self._walk(resolved, "*.py")]
        problems = self._ruff(files) if files else []
        if not problems:
            return "No problems found."
        return "\n".join(problems) + f"\nFound {len(problems)} problem{'s' if len(problems) != 1 else ''}."

    def new_lint_problems(self) -> tuple[list[str], int] | None:
        """Lint the Python files changed during this request, separating new problems from ones already there.

        Returns (new problem lines, number of problems that were already there), or None if no Python file changed.
        """
        changed = [rel for rel in self.log.originals
                   if rel.endswith(".py") and (self.root / rel).exists() and self.mode.can_read(rel)]
        if not changed:
            return None
        new, existing = [], 0
        for rel in changed:
            original = self.log.originals[rel]
            before = Counter(_lint_key(p) for p in self._ruff([rel], original)) if original is not None else Counter()
            for problem in self._ruff([rel]):
                if before[_lint_key(problem)] > 0:
                    before[_lint_key(problem)] -= 1
                    existing += 1
                else:
                    new.append(problem)
        return new, existing

def _window_matches(lines: list[str], indexes: list[int], target: list[str]) -> list[tuple[int, int, int]]:
    """Match target against consecutive runs of the given line indexes. Returns (first, last, offset) per match."""
    n = len(target)
    if n == 0:
        return []
    hits = []
    for j in range(len(indexes) - n + 1):
        run = indexes[j : j + n]
        offset = _indent_offset([lines[k] for k in run], target)
        if offset is not None:
            hits.append((run[0], run[-1], offset))
    return hits


def _not_found_message(lines: list[str], target: list[str]) -> str:
    """Explain a failed match by showing the most similar part of the file as it is now."""
    n = len(target)
    stripped = [line.strip() for line in lines]
    wanted = [t.strip() for t in target]
    best_ratio, best = 0.0, 0
    for i in range(max(len(lines) - n + 1, 1)):
        ratio = difflib.SequenceMatcher(None, stripped[i : i + n], wanted).ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, i
    if best_ratio < NOT_FOUND_MIN_SIMILARITY:
        return "old_text not found and nothing similar is in the file. Read the file again; it may have changed."

    # Align the window so its first matching line lines up with the same line in old_text.
    a, b, _ = difflib.SequenceMatcher(None, stripped[best : best + n], wanted).get_matching_blocks()[0]
    best = max(best + a - b, 0)
    window = [line.rstrip("\n") for line in lines[best : best + n]]
    first, last = best + 1, best + len(window)
    numbered = "\n".join(f"{first + k:>5}\t{line}" for k, line in enumerate(window[:NOT_FOUND_MAX_LINES]))
    message = f"old_text not found. The most similar part of the file is lines {first}-{last}, which currently read:\n{numbered}"
    if difference := _first_difference(window, target, first):
        message += f"\n{difference}"
    return message + "\nCopy old_text exactly from the current file (without the line numbers), using only a few lines."


def _first_difference(window: list[str], target: list[str], first_line: int) -> str:
    opcodes = difflib.SequenceMatcher(None, [w.strip() for w in window], [t.strip() for t in target]).get_opcodes()
    for tag, i1, _, j1, _ in opcodes:
        if tag != "equal":
            actual = repr(window[i1]) if i1 < len(window) else "nothing more"
            expected = repr(target[j1]) if j1 < len(target) else "nothing more"
            return f"First difference at line {first_line + i1}: the file has {actual} but old_text has {expected}."
    # Same text apart from whitespace, so the indentation must differ.
    for k, (actual, expected) in enumerate(zip(window, target)):
        if actual.rstrip() != expected.rstrip():
            return f"First difference at line {first_line + k} (indentation): the file has {actual!r} but old_text has {expected!r}."
    return ""


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


def _strip_trailing_whitespace(new: str, old: str) -> str:
    """Strip trailing whitespace from the lines of new that are new or changed compared with old.

    The model can't see trailing whitespace, so it can't remove what it adds; lines it kept from old stay exactly
    as they were, so whitespace that was already there is not touched.
    """
    old_lines, new_lines = old.split("\n"), new.split("\n")
    matcher = difflib.SequenceMatcher(None, [line.rstrip() for line in old_lines], [line.rstrip() for line in new_lines], autojunk=False)
    result = [line.rstrip() for line in new_lines]
    for tag, i1, _, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            result[j1:j2] = old_lines[i1 : i1 + (j2 - j1)]
    return "\n".join(result)


def _shorten(text: str, limit: int) -> str:
    """Keep the start and end of long output: errors and test summaries are usually at the end."""
    if len(text) <= limit:
        return text
    return text[: limit // 2] + f"\n... [{len(text) - limit} characters left out] ...\n" + text[-(limit // 2):]


def _lint_key(problem: str) -> str:
    """The rule and message of a lint problem line, without its position, which moves when lines are added."""
    return LINT_PROBLEM_RE.match(problem).group(1)


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
                "(including indentation) and appear only once. Keep old_text short: just enough lines to be unique. "
                "Do not include line numbers from read_file."
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
            "name": "review_changes",
            "description": "Show the changes you have made to files during the current request, as a diff against how they were before it.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lint",
            "description": "Check Python code for errors such as undefined names, unused imports and syntax errors. Run it on the files you changed before you finish. Only fix problems in code you changed for this request; mention any other problems in your answer instead of fixing them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or directory to check, relative to the workspace root. Defaults to '.'."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a command in a sandbox, for example to run the tests. It can read the workspace, can only change files the current mode allows, and has no network access. The command is not run through a shell, so pipes, redirection and && don't work. Only commands the current mode allows can be run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command line, e.g. 'pytest -q tests/test_calc.py'."},
                },
                "required": ["command"],
            },
        },
    },
]
