def parsedate(text):
    """Parse "YYYY-MM-DD" into a (year, month, day) tuple of ints."""
    year, month, day = text.split("-")
    return int(year), int(month), int(day)
