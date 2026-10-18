def unique_routes(routes):
    seen = set()
    result = []
    for route in routes:
        if route not in seen:
            seen.add(route)
            result.append(route)
    return result
