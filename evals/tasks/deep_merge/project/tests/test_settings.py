from settings import merge_settings


def test_top_level():
    assert merge_settings({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


def test_nested():
    defaults = {"db": {"host": "localhost", "port": 5432}, "debug": False}
    assert merge_settings(defaults, {"db": {"port": 6543}}) == {"db": {"host": "localhost", "port": 6543}, "debug": False}


def test_arguments_unchanged():
    defaults, overrides = {"db": {"port": 1}}, {"db": {"port": 2}}
    merge_settings(defaults, overrides)
    assert defaults == {"db": {"port": 1}} and overrides == {"db": {"port": 2}}
