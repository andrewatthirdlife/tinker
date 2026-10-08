from greeting import greet
from words import join_words


def test_default():
    assert join_words(["a", "b"]) == "a b"


def test_separator():
    assert join_words(["a", "b", "c"], separator=", ") == "a, b, c"


def test_greet():
    assert greet("Ann", "Bob") == "Hello Ann Bob"
