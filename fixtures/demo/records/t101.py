def clip_window(left, right, point):
    if left > right:
        raise ValueError("reversed window")
    return left <= point < right

def take_batch(items, start, size):
    if start < 0 or size < 0:
        raise ValueError("negative slice")
    stop = min(len(items), start + size)
    return items[start:stop]
