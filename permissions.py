"""Path pattern matching for permission rules."""

import fnmatch
from dataclasses import dataclass, field


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
