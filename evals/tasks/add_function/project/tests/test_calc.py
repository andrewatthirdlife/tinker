from calc import add, negate, sub


def test_add():
    assert add(2, 3) == 5


def test_sub():
    assert sub(5, 3) == 2


def test_negate():
    assert negate(4) == -4
    assert negate(-2) == 2
