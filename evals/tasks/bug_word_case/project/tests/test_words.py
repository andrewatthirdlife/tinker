from words import count_words


def test_counts_repeated_words():
    assert count_words("a b a") == {"a": 2, "b": 1}


def test_empty():
    assert count_words("") == {}
