def require_text(value):
    if not isinstance(value, str):
        raise TypeError("expected text")
    return value
