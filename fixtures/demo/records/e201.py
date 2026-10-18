def clip_window(left, right, point):
    if left > right:
        raise ValueError("reversed window")
    return left <= point < right
