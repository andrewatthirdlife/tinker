"""Path pattern matching for permission rules, and turning those rules into sandbox rules for commands."""

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path


def matches(path: str, patterns: list[str]) -> bool:
    """Check if a path matches any of the given patterns."""
    parts = path.split("/")
    for pattern in patterns:
        pattern_parts = pattern.split("/")
        if _match_parts(parts, pattern_parts):
            return True
    return False


def _match_parts(parts: list[str], pattern_parts: list[str]) -> bool:
    """Recursively match path parts against pattern parts."""
    if not pattern_parts:
        return not parts

    if pattern_parts[0] == "**":
        for i in range(len(parts) + 1):
            if _match_parts(parts[i:], pattern_parts[1:]):
                return True
        return False

    if not parts:
        return False

    if fnmatch.fnmatchcase(parts[0], pattern_parts[0]):
        return _match_parts(parts[1:], pattern_parts[1:])

    return False


@dataclass
class Mode:
    """A permission mode with read/write rules."""
    name: str
    description: str
    read: list[str] = field(default_factory=lambda: ["**"])
    deny_read: list[str] = field(default_factory=list)
    write: list[str] = field(default_factory=list)
    deny_write: list[str] = field(default_factory=list)
    approve: str = "ask"
    instructions: str = ""

    def can_read(self, path: str) -> bool:
        """Check if a path is readable under this mode."""
        return matches(path, self.read) and not matches(path, self.deny_read)

    def can_write(self, path: str) -> bool:
        """Check if a path is writable under this mode."""
        return matches(path, self.write) and not matches(path, self.deny_write)

    def describe_writes(self) -> str:
        """Describe what files this mode may write to."""
        if not self.write:
            return "You cannot create or edit any files."

        if self.write == ["**"] and not self.deny_write:
            return "You can create and edit any file."

        if self.write == ["**"] and self.deny_write:
            return "You can create and edit files anywhere except: " + ", ".join(self.deny_write) + "."

        write_str = ", ".join(self.write)
        if self.deny_write:
            return f"You can create and edit files matching: {write_str} except: {', '.join(self.deny_write)}."
        else:
            return f"You can create and edit files matching: {write_str}."

    def describe_reads(self) -> str:
        """Describe what files this mode may read."""
        if self.read == ["**"] and not self.deny_read:
            return "You can read any file."

        if self.read == ["**"] and self.deny_read:
            return "You can read any file except: " + ", ".join(self.deny_read) + "."

        read_str = ", ".join(self.read)
        if self.deny_read:
            return f"You can read files matching: {read_str} except: {', '.join(self.deny_read)}."
        else:
            return f"You can read files matching: {read_str}."

    def __post_init__(self):
        if self.approve not in ("ask", "auto"):
            raise ValueError(f"Mode {self.name!r}: approve must be 'ask' or 'auto'")


# Commands run in a sandbox (sandbox.py) whose file rules can only grant whole directories or single files.
# The functions below turn a mode's patterns into such rules.

READ_ONLY_FOR_COMMANDS = {".git", ".venv", "venv"}  # never writable by commands: hooks and interpreters run later


def _remainders(parts: list[str], pattern_parts: list[str]) -> set[tuple[str, ...]]:
    """What is left of a pattern after it has matched the whole of a directory path."""
    if not parts:
        return {tuple(pattern_parts)}
    if not pattern_parts:
        return set()
    if pattern_parts[0] == "**":
        return _remainders(parts, pattern_parts[1:]) | _remainders(parts[1:], pattern_parts)
    if fnmatch.fnmatchcase(parts[0], pattern_parts[0]):
        return _remainders(parts[1:], pattern_parts[1:])
    return set()


def matches_everything_under(parts: list[str], patterns: list[str]) -> bool:
    """True if the patterns match every path inside the directory (e.g. "docs/**" for docs)."""
    return any(rest == ("**",) for p in patterns for rest in _remainders(parts, p.split("/")))


def could_match_under(parts: list[str], patterns: list[str]) -> bool:
    """True if the patterns might match some path inside the directory. Errs towards True."""
    return any(rest for p in patterns for rest in _remainders(parts, p.split("/")))


def sandbox_rules(root: Path, mode: Mode) -> dict[str, str]:
    """Landlock rules for commands run in root under mode: absolute path -> "rw", "ro" or "list".

    A directory is granted as a whole when the mode treats everything in it the same way; otherwise its
    entries are decided one by one. Files can only be created in directories granted "rw" as a whole.
    Symbolic links get no rule of their own, so they only work if their target is allowed anyway.
    """
    rules: dict[str, str] = {}

    def visit(directory: Path, parts: list[str]) -> None:
        protected = any(part in READ_ONLY_FOR_COMMANDS for part in parts)
        read_all = matches_everything_under(parts, mode.read) and not could_match_under(parts, mode.deny_read)
        read_none = not could_match_under(parts, mode.read) or matches_everything_under(parts, mode.deny_read)
        write_none = (protected or not could_match_under(parts, mode.write)
                      or matches_everything_under(parts, mode.deny_write))
        write_all = (not protected and matches_everything_under(parts, mode.write)
                     and not could_match_under(parts, mode.deny_write)
                     and not any((directory / name).exists() for name in READ_ONLY_FOR_COMMANDS))
        if read_none:
            return
        if read_all and write_all:
            rules[str(directory)] = "rw"
            return
        if read_all and write_none:
            rules[str(directory)] = "ro"
            return
        rules[str(directory)] = "list"
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink():
                continue
            entry_parts = [*parts, entry.name]
            if entry.is_dir():
                visit(entry, entry_parts)
            elif entry.is_file():
                rel = "/".join(entry_parts)
                if mode.can_write(rel) and mode.can_read(rel) and not protected:
                    rules[str(entry)] = "rw"
                elif mode.can_read(rel):
                    rules[str(entry)] = "ro"

    visit(root.resolve(), [])
    return rules
