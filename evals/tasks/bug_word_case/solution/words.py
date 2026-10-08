def count_words(text):
    """Count how often each word appears, ignoring case. Returns a dict of lower-case word -> count."""
    counts = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    return counts
