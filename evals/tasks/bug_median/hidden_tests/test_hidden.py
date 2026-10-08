from stats import median


def test_median_even():
    assert median([4, 1, 3, 2]) == 2.5
    assert median([10, 20]) == 15


def test_median_odd_still_works():
    assert median([5]) == 5
    assert median([9, 1, 5, 3, 7]) == 5
