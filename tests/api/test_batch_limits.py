"""批量请求体的 id 列表上限: 超过 1000 个在 schema 层拒绝, 不进入处理."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx2 import AsyncClient

_IDS = list(range(1001))


CASES = [
    ("metadata/batch/delete", {"ids": _IDS}, "metadata 删除"),
    ("metadata/batch/scrape", {"ids": _IDS}, "metadata 刮削"),
    (
        "metadata/batch/user-tags",
        {"ids": _IDS, "user_tag_ids": [1], "action": "attach"},
        "metadata 用户标签 (ids)",
    ),
    (
        "metadata/batch/user-tags",
        {"ids": [1], "user_tag_ids": _IDS, "action": "attach"},
        "metadata 用户标签 (user_tag_ids)",
    ),
    ("metadata/merge-entries", {"target_id": 1, "source_ids": _IDS}, "metadata 合并"),
    (
        "actors/batch/user-tags",
        {"ids": _IDS, "user_tag_ids": [1], "action": "attach"},
        "演员用户标签 (ids)",
    ),
    (
        "actors/batch/user-tags",
        {"ids": [1], "user_tag_ids": _IDS, "action": "attach"},
        "演员用户标签 (user_tag_ids)",
    ),
    ("facets/actor/batch", {"facet_ids": _IDS, "action": "favorite"}, "分类批量"),
    ("facets/actor/merge", {"target_id": 1, "source_ids": _IDS}, "分类合并"),
    ("feeds/1/items/batch", {"action": "ignore", "ids": _IDS}, "订阅条目批量"),
    ("tasks/batch", {"action": "cancel", "task_ids": _IDS}, "任务批量"),
    ("libraries/1/cleanup/selection", {"media_file_ids": _IDS}, "清理选择"),
]


@pytest.mark.asyncio(loop_scope="function")
async def test_batch_id_lists_reject_more_than_1000(client: AsyncClient) -> None:
    """同一 lifespan 内逐个端点校验, 不为每个请求重建应用."""
    for path, body, label in CASES:
        response = await client.post(path, json=body)
        assert response.status_code == 422, label
