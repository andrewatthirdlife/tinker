import pytest

from durations import parse_duration


def test_hours_and_minutes():
    assert parse_duration("1h30m") == 90


def test_minutes():
    assert parse_duration("45m") == 45


def test_rubbish():
    with pytest.raises(ValueError):
        parse_duration("soon")
