def require_text(value):
    if not isinstance(value, str):
        raise TypeError("expected text")
    return value

def clean_tag(tag):
    return "-".join(require_text(tag).split()).lower()
