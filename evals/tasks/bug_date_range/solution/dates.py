from datetime import date, timedelta


def days_between(start, end):
    """All dates from start to end, both included, as "YYYY-MM-DD" strings."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]
