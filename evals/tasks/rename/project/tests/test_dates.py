from dates import parse_date
from report import year_of


def test_parse_date():
    assert parse_date("2026-10-08") == (2026, 10, 8)


def test_year_of():
    assert year_of("1999-01-31") == 1999
