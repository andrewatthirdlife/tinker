from calc import negate


def test_negate_more():
    assert negate(0) == 0
    assert negate(1.5) == -1.5
