"""序列去重工具."""

from collections.abc import Sequence


def unique_ids(ids: Sequence[int] | None) -> list[int]:
    """保序去重; None 或空 → []."""
    if not ids:
        return []
    return list(dict.fromkeys(ids))
