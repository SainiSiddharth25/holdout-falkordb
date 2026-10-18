def cache_name(value):
    key = value.strip()
    if not key:
        raise ValueError("empty cache name")
    return key
