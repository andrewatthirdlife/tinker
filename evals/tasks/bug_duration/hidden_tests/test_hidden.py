import pytest

from durations import parse_duration


def test_hours_only():
    assert parse_duration("2h") == 120
    assert parse_duration(" 1h ") == 60


def test_still_rejects():
    for text in ("", "h", "m", "1x"):
        with pytest.raises(ValueError):
            parse_duration(text)
