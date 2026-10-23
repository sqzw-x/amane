"""TaskLink 后继关系与 complete_task_with_followups 事务测试."""

import sqlite3
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event

from amane.db.models import TaskStatus, TaskType
from amane.db.repos.base import SQL_IN_CHUNK_SIZE

if TYPE_CHECKING:
    from amane.db.repository import Repository


@pytest.mark.asyncio(loop_scope="function")
async def test_complete_task_with_followups_creates_links(repo: Repository):
    """完成父任务 + 创建子任务 + 写 TaskLink 在同一事务内."""
    parent = await repo.create_task(TaskType.REFRESH, payload={"library_id": 1})
    assert parent.id is not None

    # 模拟 worker claim (status -> RUNNING)
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    child1 = await repo.complete_task_with_followups(
        claimed.id, result={"added": 2}, followups=[("scrape", TaskType.SCRAPE, {"number": "ABC-123"}, 0)]
    )
    assert len(child1) == 1
    assert child1[0].type == TaskType.SCRAPE
    assert child1[0].status == TaskStatus.QUEUED
    # 子任务继承链根 (根任务指向自己)
    assert child1[0].root_task_id == parent.id
    assert child1[0].payload == {"number": "ABC-123"}

    done_parent = await repo.get_task(parent.id)
    assert done_parent is not None
    assert done_parent.status == TaskStatus.DONE
    assert done_parent.result == {"added": 2}
    assert done_parent.finished_at is not None

    links = await repo.list_task_links(parent_task_id=parent.id)
    assert len(links) == 1
    assert links[0].key == "scrape"
    assert links[0].child_task_id == child1[0].id


@pytest.mark.asyncio(loop_scope="function")
async def test_followups_chain_root_task_id(repo: Repository):
    """两层链: 子任务再衍生后继时沿用同一根."""
    parent = await repo.create_task(TaskType.SCRAPE, payload={"number": "X-1"})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id, result={"metadata_id": 1}, followups=[("actor-scrape", TaskType.ACTOR_SCRAPE, {"actor_id": 9}, -1)]
    )
    assert len(children) == 1
    child = children[0]
    assert child.id is not None

    # 子任务 RUNNING 后再次衍生
    claimed_child = await repo.claim_next_task()
    assert claimed_child is not None and claimed_child.id == child.id
    grandchildren = await repo.complete_task_with_followups(
        child.id, result={}, followups=[("scrape", TaskType.SCRAPE, {"number": "X-2"}, 0)]
    )
    assert len(grandchildren) == 1
    assert grandchildren[0].root_task_id == parent.id

    # 整链一次取回 (含根)
    chain = await repo.list_tasks_by_root(parent.id)
    assert {t.id for t in chain} == {parent.id, child.id, grandchildren[0].id}


@pytest.mark.asyncio(loop_scope="function")
async def test_complete_requires_running(repo: Repository):
    """非 RUNNING 父任务不产生后继 (防重复完成, 写库版)."""
    parent = await repo.create_task(TaskType.REFRESH, payload={})
    assert parent.id is not None
    # 已完成后再重复 complete: 必须读库判断 status, 不能只看内存对象.
    await repo.complete_task(parent.id)
    children = await repo.complete_task_with_followups(
        parent.id, result={}, followups=[("scrape", TaskType.SCRAPE, {"number": "A"}, 0)]
    )
    assert children == []
    links = await repo.list_task_links(parent_task_id=parent.id)
    assert links == []
    # 同一 session 里再查, 确认结果一致 (非内存脏读).
    again = await repo.complete_task_with_followups(
        parent.id, result={}, followups=[("scrape", TaskType.SCRAPE, {"number": "A"}, 0)]
    )
    assert again == []


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_task_cleans_links_both_directions(repo: Repository):
    """删除任务清理其相关边, 不删除另一端任务."""
    parent = await repo.create_task(TaskType.REFRESH, payload={})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id, result={}, followups=[("scrape", TaskType.SCRAPE, {"number": "B"}, 0)]
    )
    child = children[0]
    assert child.id is not None

    # 删除子任务: 出向边 (parent -> child) 被清理, 父任务仍在
    assert await repo.delete_task(child.id)
    assert await repo.get_task(parent.id) is not None
    assert await repo.list_task_links(child_task_id=child.id) == []

    # 删除父任务: 已无边, 可删
    assert await repo.delete_task(parent.id)
    assert await repo.get_task(parent.id) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_parent_refuses_remaining_children(repo: Repository):
    """有集合外后裔的父任务拒绝删除; 先删子任务后父才可删."""
    parent = await repo.create_task(TaskType.REFRESH, payload={})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id, result={}, followups=[("scrape", TaskType.SCRAPE, {"number": "B"}, 0)]
    )
    child = children[0]
    assert child.id is not None

    assert not await repo.delete_task(parent.id)
    assert await repo.get_task(parent.id) is not None
    assert await repo.get_task(child.id) is not None
    assert await repo.list_task_links(parent_task_id=parent.id) != []

    await repo.fail_task(child.id, error="boom")
    assert not await repo.delete_task(parent.id)
    assert await repo.get_task(parent.id) is not None
    assert await repo.get_task(child.id) is not None

    assert await repo.delete_task(child.id)
    assert await repo.delete_task(parent.id)
    assert await repo.get_task(parent.id) is None
    assert await repo.get_task(child.id) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_tasks_skips_parent_with_remaining_children(repo: Repository):
    """批量删除: 仅删父且子仍在时跳过; 子与父同批则可整棵删除."""
    parent = await repo.create_task(TaskType.REFRESH, payload={})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id,
        result={},
        followups=[
            ("scrape:1", TaskType.SCRAPE, {"number": "C-1"}, 0),
            ("scrape:2", TaskType.SCRAPE, {"number": "C-2"}, 0),
        ],
    )
    assert all(c.id is not None for c in children)
    child_ids = {c.id for c in children if c.id is not None}

    assert await repo.delete_tasks([parent.id]) == 0
    assert await repo.get_task(parent.id) is not None
    for cid in child_ids:
        assert await repo.get_task(cid) is not None

    for cid in child_ids:
        await repo.fail_task(cid, error="boom")
    assert await repo.delete_tasks([parent.id]) == 0
    assert await repo.get_task(parent.id) is not None
    assert await repo.list_task_links(parent_task_id=parent.id) != []

    assert await repo.delete_tasks([parent.id, *child_ids]) == 1 + len(child_ids)
    assert await repo.get_task(parent.id) is None
    for cid in child_ids:
        assert await repo.get_task(cid) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_tasks_mixed_tree_keeps_root(repo: Repository):
    """DONE 父 + DONE/FAILED 子: 只删除 DONE 叶子; 父与 FAILED 子及边保留, 列表仍能看到链根."""
    parent = await repo.create_task(TaskType.REFRESH, payload={"library_id": 1})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id,
        result={},
        followups=[
            ("scrape:ok", TaskType.SCRAPE, {"number": "OK"}, 0),
            ("scrape:bad", TaskType.SCRAPE, {"number": "BAD"}, 0),
        ],
    )
    done_child, failed_child = children
    assert done_child.id is not None and failed_child.id is not None
    await repo.complete_task(done_child.id)
    await repo.fail_task(failed_child.id, error="boom")

    assert await repo.delete_tasks([parent.id, done_child.id]) == 1
    assert await repo.get_task(done_child.id) is None
    assert await repo.get_task(parent.id) is not None
    assert await repo.get_task(failed_child.id) is not None
    links = await repo.list_task_links(parent_task_id=parent.id)
    assert [link.child_task_id for link in links] == [failed_child.id]
    roots = await repo.list_tasks(roots_only=True)
    assert parent.id in {t.id for t in roots}
    failed_listed = await repo.list_tasks(statuses=[TaskStatus.FAILED], roots_only=True)
    assert parent.id in {t.id for t in failed_listed}

    all_done = await repo.create_task(TaskType.REFRESH, payload={"library_id": 2})
    assert all_done.id is not None
    claimed_done = await repo.claim_next_task()
    assert claimed_done is not None and claimed_done.id == all_done.id
    assert claimed_done.id is not None
    ok_children = await repo.complete_task_with_followups(
        claimed_done.id,
        result={},
        followups=[
            ("scrape:1", TaskType.SCRAPE, {"number": "D-1"}, 0),
            ("scrape:2", TaskType.SCRAPE, {"number": "D-2"}, 0),
        ],
    )
    ok_ids = [c.id for c in ok_children if c.id is not None]
    for cid in ok_ids:
        await repo.complete_task(cid)
    assert await repo.delete_tasks([all_done.id, *ok_ids]) == 1 + len(ok_ids)
    assert await repo.get_task(all_done.id) is None
    for cid in ok_ids:
        assert await repo.get_task(cid) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_retry_clones_as_bare_task(repo: Repository):
    """重试克隆为无根裸任务: 不继承链归属, 顶层列表可见, 完成后自成新链."""
    parent = await repo.create_task(TaskType.SCRAPE, payload={"number": "C-1"})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id, result={}, followups=[("scrape", TaskType.SCRAPE, {"number": "C-2"}, 0)]
    )
    child = children[0]
    assert child.id is not None
    claimed_child = await repo.claim_next_task()
    assert claimed_child is not None and claimed_child.id == child.id
    # 失败后重试
    await repo.fail_task(child.id, error="intentional")

    clones = await repo.retry_tasks([child])
    assert len(clones) == 1
    clone = clones[0]
    assert clone.id is not None
    # 裸任务: 无根归属 → roots_only 列表可见
    assert clone.root_task_id is None
    listed = await repo.list_tasks(roots_only=True)
    assert clone.id in {t.id for t in listed}
    # 原 FAILED 行保留
    assert await repo.get_task(child.id) is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_complete_duplicate_key_keeps_first(repo: Repository):
    """同父同 key 只实例化第一条, 不触发 UNIQUE 失败."""
    parent = await repo.create_task(TaskType.REFRESH, payload={})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id,
        result={},
        followups=[
            ("scrape:1", TaskType.SCRAPE, {"number": "KEEP"}, 0),
            ("scrape:1", TaskType.SCRAPE, {"number": "DROP"}, 0),
        ],
    )
    assert len(children) == 1
    assert children[0].payload == {"number": "KEEP"}
    links = await repo.list_task_links(parent_task_id=parent.id)
    assert len(links) == 1
    assert links[0].key == "scrape:1"


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_task_refuses_remaining_grandchild(repo: Repository):
    """单删看整条后裔: 直接子已终态但孙任务仍在时拒绝."""
    parent = await repo.create_task(TaskType.REFRESH, payload={})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id, result={}, followups=[("scrape:1", TaskType.SCRAPE, {"number": "A"}, 0)]
    )
    child = children[0]
    assert child.id is not None
    claimed_child = await repo.claim_next_task()
    assert claimed_child is not None and claimed_child.id == child.id
    assert claimed_child.id is not None
    grands = await repo.complete_task_with_followups(
        claimed_child.id, result={}, followups=[("actor-scrape:1", TaskType.ACTOR_SCRAPE, {"actor_id": 1}, -1)]
    )
    grand = grands[0]
    assert grand.id is not None
    assert grand.status == TaskStatus.QUEUED

    assert not await repo.delete_task(parent.id)
    assert await repo.get_task(parent.id) is not None
    assert await repo.get_task(grand.id) is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_tasks_skips_protected_deletes_others(repo: Repository):
    """批量删除: 有在跑后裔的行跳过, 其它终态任务照删."""
    protected_parent = await repo.create_task(TaskType.REFRESH, payload={"library_id": 1})
    assert protected_parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == protected_parent.id
    assert claimed.id is not None
    await repo.complete_task_with_followups(
        claimed.id, result={}, followups=[("scrape:1", TaskType.SCRAPE, {"number": "LIVE"}, 0)]
    )

    done = await repo.create_task(TaskType.CLEANUP, payload={})
    assert done.id is not None
    await repo.complete_task(done.id)

    assert await repo.delete_tasks([protected_parent.id, done.id]) == 1
    assert await repo.get_task(protected_parent.id) is not None
    assert await repo.get_task(done.id) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_list_children_key_and_status_counts(repo: Repository):
    """list_children 带出边 key; child_status_counts 按状态拆, 空/未知父返回空."""
    parent = await repo.create_task(TaskType.REFRESH, payload={"library_id": 1})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id,
        result={},
        followups=[
            ("scrape:1", TaskType.SCRAPE, {"number": "A"}, 0),
            ("scrape:2", TaskType.SCRAPE, {"number": "B"}, 0),
        ],
    )
    pairs = await repo.list_children(parent.id)
    assert [key for _, key in pairs] == ["scrape:1", "scrape:2"]

    counts = await repo.child_status_counts([parent.id])
    assert counts[parent.id] == {TaskStatus.QUEUED: 2}
    assert await repo.child_status_counts([]) == {}
    assert await repo.child_status_counts([parent.id + 99_999]) == {}

    first = children[0]
    assert first.id is not None
    running = await repo.claim_next_task()
    assert running is not None and running.id == first.id
    await repo.complete_task(first.id)

    mixed = await repo.child_status_counts([parent.id])
    assert mixed[parent.id][TaskStatus.DONE] == 1
    assert mixed[parent.id][TaskStatus.QUEUED] == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_list_tasks_roots_only_hides_children(repo: Repository):
    parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id, result={}, followups=[("scrape", TaskType.SCRAPE, {"number": "MIDV-123"}, 0)]
    )
    child = children[0]
    assert child.id is not None

    listed = await repo.list_tasks(roots_only=True)
    ids = {t.id for t in listed}
    assert parent.id in ids
    assert child.id not in ids
    assert await repo.count_tasks(roots_only=True) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_list_tasks_roots_only_filter_matches_children(repo: Repository):
    """status/type 命中子任务时, roots_only 仍返回链根 (列表只显示一层)."""
    parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id, result={}, followups=[("scrape", TaskType.SCRAPE, {"number": "MIDV-123"}, 0)]
    )
    child = children[0]
    assert child.id is not None
    standalone = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "STD-1"})
    assert standalone.id is not None

    queued = await repo.list_tasks(statuses=[TaskStatus.QUEUED], roots_only=True)
    queued_ids = {t.id for t in queued}
    assert parent.id in queued_ids
    assert standalone.id in queued_ids
    assert child.id not in queued_ids
    assert await repo.count_tasks(statuses=[TaskStatus.QUEUED], roots_only=True) == 2

    scrape = await repo.list_tasks(task_types=[TaskType.SCRAPE], roots_only=True)
    scrape_ids = {t.id for t in scrape}
    assert parent.id in scrape_ids
    assert standalone.id in scrape_ids
    assert child.id not in scrape_ids


# 一条语句最多绑定两批 (边同时按父与子匹配); 上限压到这里, 整批绑定才会在测试规模下越界.
_LOW_VARIABLE_LIMIT = 2 * SQL_IN_CHUNK_SIZE
_CHILD_COUNT = 2 * SQL_IN_CHUNK_SIZE + 100


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_tasks_binds_ids_in_chunks(repo: Repository) -> None:
    """万级任务组一次删完: 边、行与按 id 查询都分批绑定, 上限较低的 SQLite 上也不报 too many SQL variables."""

    @event.listens_for(repo._engine.sync_engine, "connect")
    def _lower_limit(dbapi_connection: Any, _record: Any) -> None:
        # 上限是连接级的, 只能经 aiosqlite 的底层 sqlite3 连接设置.
        dbapi_connection.driver_connection._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, _LOW_VARIABLE_LIMIT)

    parent = await repo.create_task(TaskType.REFRESH, payload={"library_id": 1})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id,
        result={},
        followups=[(f"scrape:{i}", TaskType.SCRAPE, {"number": f"N-{i}"}, 0) for i in range(_CHILD_COUNT)],
    )
    child_ids = [child.id for child in children if child.id is not None]
    assert len(child_ids) == _CHILD_COUNT

    assert await repo.delete_tasks([parent.id, *child_ids]) == _CHILD_COUNT + 1
    assert await repo.list_task_links(parent_task_id=parent.id) == []
    assert await repo.find_tasks(task_ids=[parent.id, *child_ids]) == []
