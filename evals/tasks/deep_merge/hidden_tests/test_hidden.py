from settings import merge_settings


def test_three_levels():
    assert merge_settings({"a": {"b": {"c": 1, "d": 2}}}, {"a": {"b": {"d": 3}}}) == {"a": {"b": {"c": 1, "d": 3}}}


def test_dict_replaced_by_value():
    assert merge_settings({"a": {"b": 1}}, {"a": 5}) == {"a": 5}
