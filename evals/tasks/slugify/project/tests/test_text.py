from text import slugify


def test_simple():
    assert slugify("Hello World") == "hello-world"


def test_punctuation():
    assert slugify("What's new, Tinker?") == "whats-new-tinker"


def test_spaces():
    assert slugify("  many   spaces  ") == "many-spaces"
