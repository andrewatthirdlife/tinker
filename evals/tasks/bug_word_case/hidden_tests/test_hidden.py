from words import count_words


def test_case_is_ignored():
    assert count_words("The cat saw the dog") == {"the": 2, "cat": 1, "saw": 1, "dog": 1}
    assert count_words("A a A") == {"a": 3}
