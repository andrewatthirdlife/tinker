def last_n(items, n):
    """Return the last n items of a list, or all of them if there are fewer than n."""
    if n <= 0:
        return []
    return items[-n + 1:]


def first_n(items, n):
    """Return the first n items of a list."""
    return items[:n]
