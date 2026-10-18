def slice_group(values, offset, count):
    if offset < 0 or count < 0:
        raise ValueError("negative slice")
    finish = min(len(values), offset + count)
    return values[offset:finish]
