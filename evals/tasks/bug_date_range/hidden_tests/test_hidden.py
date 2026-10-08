from dates import days_between


def test_both_ends_included():
    assert days_between("2026-01-30", "2026-02-01") == ["2026-01-30", "2026-01-31", "2026-02-01"]


def test_same_day():
    assert days_between("2026-05-05", "2026-05-05") == ["2026-05-05"]
