def merge_settings(defaults, overrides):
    result = dict(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_settings(result[key], value)
        else:
            result[key] = value
    return result
