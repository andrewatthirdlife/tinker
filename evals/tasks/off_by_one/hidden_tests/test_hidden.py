from listutil import last_n


def test_last_one():
    assert last_n(["a", "b", "c"], 1) == ["c"]


def test_negative():
    assert last_n([1, 2, 3], -1) == []
