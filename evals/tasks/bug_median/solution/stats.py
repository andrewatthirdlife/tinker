def mean(values):
    return sum(values) / len(values)


def median(values):
    """The middle value of the sorted values, or the mean of the two middle values."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
