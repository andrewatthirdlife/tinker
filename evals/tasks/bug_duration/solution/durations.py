import re


def parse_duration(text):
    """Parse a duration like "1h30m", "2h" or "45m" into minutes."""
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", text.strip())
    if not match or not any(match.groups()):
        raise ValueError(f"not a duration: {text!r}")
    hours, minutes = match.groups()
    return int(hours or 0) * 60 + int(minutes or 0)
