from dates import days_between


def test_starts_on_the_first_day():
    assert days_between("2026-03-01", "2026-03-10")[0] == "2026-03-01"
