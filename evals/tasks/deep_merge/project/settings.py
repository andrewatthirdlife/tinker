def merge_settings(defaults, overrides):
    """Return defaults updated with overrides. Neither argument is changed."""
    result = dict(defaults)
    result.update(overrides)
    return result
