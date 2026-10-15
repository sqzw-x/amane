"""序列去重工具."""

from collections.abc import Sequence


def unique_ids(ids: Sequence[int] | None) -> list[int]:
    """保序去重; None 或空 → []."""
    if not ids:
        return []
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out
