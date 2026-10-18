def take_batch(items, start, size):
    if start < 0 or size < 0:
        raise ValueError("negative slice")
    stop = min(len(items), start + size)
    return items[start:stop]
