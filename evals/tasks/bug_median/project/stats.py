def mean(values):
    return sum(values) / len(values)


def median(values):
    """The middle value of the sorted values."""
    ordered = sorted(values)
    return ordered[len(ordered) // 2]
