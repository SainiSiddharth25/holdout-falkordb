# Workshop answer capture

Source bundle: source-83

```python
def release_key(label):
    parts = label.removeprefix("v").split(".")
    if len(parts) != 3:
        raise ValueError("expected three release fields")
    return tuple(int(part) for part in parts)
```

Capture retains the answer exactly; later notes add only explanation.
