def order_events(events):
    indexed = list(enumerate(events))
    indexed.sort(key=lambda pair: (pair[1]["time"], pair[0]))
    return [event for index, event in indexed]
