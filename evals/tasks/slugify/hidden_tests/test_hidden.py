from text import slugify


def test_numbers_and_dashes():
    assert slugify("Python 3.12 -- released!") == "python-312-released"


def test_empty():
    assert slugify("!!!") == ""
