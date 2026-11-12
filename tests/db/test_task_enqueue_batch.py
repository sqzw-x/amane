"""批量入队: 互斥复用、后继的数量 / 顺序 / 边, 以及逐条 flush 的回归预算."""

import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import aiosqlite
import pytest
from sqlalchemy import event

from amane.db.models import TaskStatus, TaskType
from amane.db.repository import Repository

# 万级后继的规模; 用语句数而不是墙钟时间判断是否退回逐条路径.
_SCALE_ROWS = 10_000


class _Statements:
    """收集本次调用执行的 SQL, 用于断言语句数与后继数量不成正比."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def __call__(
        self, _conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _executemany: bool
    ) -> None:
        self.items.append(statement)


@contextmanager
def _capture_statements(repo: Repository) -> Iterator[_Statements]:
    captured = _Statements()
    engine = repo._engine.sync_engine
    event.listen(engine, "before_cursor_execute", captured)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", captured)


@pytest.fixture
def shuffled_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """逆转驱动返回顺序, 验证任务与后继键的对应不依赖 RETURNING 顺序."""
    original = aiosqlite.Cursor.fetchall

    async def reversed_fetchall(cursor: aiosqlite.Cursor) -> list[sqlite3.Row]:
        return list(await original(cursor))[::-1]

    monkeypatch.setattr(aiosqlite.Cursor, "fetchall", reversed_fetchall)


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.usefixtures("shuffled_rows")
async def test_followup_keys_survive_shuffled_returning(repo: Repository) -> None:
    parent = await repo.create_task(TaskType.REFRESH, {})
    assert parent.id is not None
    assert await repo.claim_next_task() is not None
    existing = await repo.create_task(TaskType.ACTOR_SCRAPE, {"actor_id": -1})
    followups: list[tuple[str, TaskType, dict[str, object], int]] = [
        ("existing", TaskType.ACTOR_SCRAPE, {"actor_id": -1}, 0),
        *[(f"actor:{i}", TaskType.ACTOR_SCRAPE, {"actor_id": i}, -i) for i in range(101)],
        ("alias", TaskType.ACTOR_SCRAPE, {"actor_id": 100}, 0),
        ("duplicate-key", TaskType.SCRAPE, {"number": "first"}, 0),
        ("identical-a", TaskType.SCRAPE, {"number": "same"}, 0),
        ("identical-b", TaskType.SCRAPE, {"number": "same"}, 0),
    ]
    children = await repo.complete_task_with_followups(
        parent.id, {}, [*followups, ("duplicate-key", TaskType.SCRAPE, {"number": "discarded"}, 0)]
    )
    expected = {key: (task_type, payload) for key, task_type, payload, _ in followups}
    assert [(child.type, child.payload) for child in children] == list(expected.values())
    assert children[0].id == existing.id
    assert children[101].id == children[102].id
    assert children[-2].id != children[-1].id
    links = await repo.list_children(parent.id)
    assert {key: (child.type, child.payload) for child, key in links} == expected
    assert {key: child.id for child, key in links} == {
        key: child.id for key, child in zip(expected, children, strict=True)
    }
    assert all(child.root_task_id == parent.id for child in children)


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.usefixtures("shuffled_rows")
async def test_create_and_retry_survive_shuffled_returning(repo: Repository) -> None:
    assert await repo.create_tasks(TaskType.SCRAPE, []) == []
    assert await repo.retry_tasks([]) == []
    payloads: list[dict[str, object]] = [{"number": f"N-{i}"} for i in range(101)]
    originals = await repo.create_tasks(TaskType.SCRAPE, payloads)
    assert [task.payload for task in originals] == payloads
    assert await repo.fail_queued_tasks(error="retry") == len(payloads)
    clones = await repo.retry_tasks(originals)
    assert [task.payload for task in clones] == payloads
    assert {task.id for task in clones}.isdisjoint(task.id for task in originals)
    assert all(task.root_task_id is None for task in clones)


@pytest.mark.asyncio(loop_scope="function")
async def test_independent_repositories_share_enqueue_write_lock(repo: Repository) -> None:
    other = Repository(repo._engine)
    payloads: list[dict[str, object]] = [{"actor_id": i} for i in range(101)]
    first, second = await asyncio.gather(
        repo.create_tasks(TaskType.ACTOR_SCRAPE, payloads),
        other.create_tasks(TaskType.ACTOR_SCRAPE, payloads),
    )
    assert [task.id for task in first] == [task.id for task in second]
    assert await repo.count_tasks() == len(payloads)


@pytest.mark.asyncio(loop_scope="function")
async def test_followup_batch_scale(repo: Repository) -> None:
    """一次完成事务派生大量后继: 数量 / 顺序 / 边都对, 且语句数不随后继数量增长."""
    count = _SCALE_ROWS
    parent = await repo.create_task(TaskType.REFRESH, {"library_id": 1})
    assert parent.id is not None
    parent_id = parent.id
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent_id
    followups: list[tuple[str, TaskType, dict[str, object], int]] = [
        (f"actor-scrape:{i}", TaskType.ACTOR_SCRAPE, {"actor_id": i}, -1) for i in range(count)
    ]

    with _capture_statements(repo) as captured:
        children = await repo.complete_task_with_followups(parent_id, {}, followups)

    assert len(children) == count
    assert [child.payload["actor_id"] for child in children] == list(range(count))
    ids = [child.id for child in children if child.id is not None]
    assert len(ids) == count
    assert len(set(ids)) == count
    # 插入顺序即出边顺序: TaskLink 列表按边 id 排序时须回到同一批子任务.
    assert ids == sorted(ids)
    assert {child.root_task_id for child in children} == {parent_id}
    assert {child.status for child in children} == {TaskStatus.QUEUED}

    links = await repo.list_task_links(parent_task_id=parent_id)
    assert [link.key for link in links] == [key for key, _task_type, _payload, _priority in followups]
    assert [link.child_task_id for link in links] == ids
    assert await repo.count_tasks(task_types=[TaskType.ACTOR_SCRAPE]) == count

    # 旧路径每个后继约 4 条语句; 批量化后语句数与后继数量无关.
    assert len(captured.items) < count // 2


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("payloads", "rows"),
    [
        ([{"actor_id": 5}] * 3, 1),
        ([{"actor_id": 5}, {"actor_id": 6}, {"actor_id": 5}], 2),
        ([{"actor_id": "5"}, {"actor_id": 5}], 2),
        ([{"actor_id": True}, {"actor_id": 1}], 2),
        ([{"actor_id": 1.0}, {"actor_id": 1}], 2),
        ([{}, {"actor_id": None}], 2),
    ],
    ids=["同键一条", "两个键", "字符串不互斥", "布尔不互斥", "浮点不互斥", "无键不互斥"],
)
async def test_create_tasks_exclusive_reuse(repo: Repository, payloads: list[dict[str, object]], rows: int) -> None:
    """批内互斥: 同一个 actor_id 只建一行, 返回值与输入同序且共享同一行."""
    created = await repo.create_tasks(TaskType.ACTOR_SCRAPE, payloads)
    assert len(created) == len(payloads)
    assert len({task.id for task in created}) == rows
    assert await repo.count_tasks(task_types=[TaskType.ACTOR_SCRAPE]) == rows


@pytest.mark.asyncio(loop_scope="function")
async def test_create_tasks_reuses_existing_active_rows(repo: Repository) -> None:
    """已有 queued / running 行按 actor_id 复用, 终态行不复用."""
    parent = await repo.create_task(TaskType.REFRESH, {"library_id": 1})
    assert parent.id is not None
    running = await repo.create_task(TaskType.ACTOR_SCRAPE, {"actor_id": 12})
    claimed_parent = await repo.claim_next_task()
    assert claimed_parent is not None and claimed_parent.id == parent.id
    claimed_actor = await repo.claim_next_task()
    assert claimed_actor is not None and claimed_actor.id == running.id
    queued = await repo.create_task(TaskType.ACTOR_SCRAPE, {"actor_id": 11})
    terminal = await repo.create_task(TaskType.ACTOR_SCRAPE, {"actor_id": 13})
    assert terminal.id is not None
    assert await repo.fail_task(terminal.id, error="boom") is True

    created = await repo.create_tasks(TaskType.ACTOR_SCRAPE, [{"actor_id": 11}, {"actor_id": 12}, {"actor_id": 13}])
    assert [task.id for task in created][:2] == [queued.id, running.id]
    assert created[2].id not in {queued.id, running.id, terminal.id}
    assert await repo.count_tasks(task_types=[TaskType.ACTOR_SCRAPE]) == 4


@pytest.mark.asyncio(loop_scope="function")
async def test_followups_reuse_active_rows_and_link_them(repo: Repository) -> None:
    """后继命中活跃行时直接建边, 不另建行; 批内同键复用同一行; 裸行被收进本次链."""
    parent = await repo.create_task(TaskType.REFRESH, {"library_id": 1})
    assert parent.id is not None
    running = await repo.create_task(TaskType.ACTOR_SCRAPE, {"actor_id": 8})
    claimed_parent = await repo.claim_next_task()
    assert claimed_parent is not None and claimed_parent.id == parent.id
    claimed_actor = await repo.claim_next_task()
    assert claimed_actor is not None and claimed_actor.id == running.id
    queued = await repo.create_task(TaskType.ACTOR_SCRAPE, {"actor_id": 7})

    followups: list[tuple[str, TaskType, dict[str, object], int]] = [
        ("actor:7", TaskType.ACTOR_SCRAPE, {"actor_id": 7}, -1),
        ("actor:8", TaskType.ACTOR_SCRAPE, {"actor_id": 8}, -1),
        ("actor:9", TaskType.ACTOR_SCRAPE, {"actor_id": 9}, -1),
        ("actor:9-again", TaskType.ACTOR_SCRAPE, {"actor_id": 9}, -1),
        ("scrape", TaskType.SCRAPE, {"number": "A"}, 0),
    ]
    children = await repo.complete_task_with_followups(parent.id, {}, followups)

    assert [child.payload for child in children] == [
        {"actor_id": 7},
        {"actor_id": 8},
        {"actor_id": 9},
        {"actor_id": 9},
        {"number": "A"},
    ]
    assert queued.id is not None and running.id is not None
    assert children[0].id == queued.id
    assert children[1].id == running.id
    assert children[2].id == children[3].id
    assert children[2].id not in {queued.id, running.id}
    queued_id, running_id = queued.id, running.id
    assert queued_id is not None and running_id is not None
    for reused_id in (queued_id, running_id):
        reused = await repo.get_task(reused_id)
        assert reused is not None and reused.root_task_id == parent.id
    assert await repo.count_tasks(task_types=[TaskType.ACTOR_SCRAPE]) == 3

    links = await repo.list_task_links(parent_task_id=parent.id)
    assert [link.key for link in links] == [key for key, _task_type, _payload, _priority in followups]
    assert len(links) == len(followups)


@pytest.mark.asyncio(loop_scope="function")
async def test_concurrent_completion_shares_followup_batch(repo: Repository) -> None:
    """两个父任务并发派生同一批 actor: 每个 actor 只建一行, 两边都建边."""
    a = await repo.create_task(TaskType.SCRAPE, {"number": "A"})
    b = await repo.create_task(TaskType.SCRAPE, {"number": "B"})
    assert a.id is not None and b.id is not None
    assert await repo.claim_next_task() is not None
    assert await repo.claim_next_task() is not None
    followups: list[tuple[str, TaskType, dict[str, object], int]] = [
        (f"actor:{i}", TaskType.ACTOR_SCRAPE, {"actor_id": i}, -1) for i in range(1_000)
    ]

    results = await asyncio.gather(
        repo.complete_task_with_followups(a.id, {}, followups),
        repo.complete_task_with_followups(b.id, {}, followups),
    )

    assert len(results[0]) == len(results[1]) == len(followups)
    assert [task.id for task in results[0]] == [task.id for task in results[1]]
    assert await repo.count_tasks(task_types=[TaskType.ACTOR_SCRAPE]) == len(followups)
    assert len(await repo.list_task_links(parent_task_id=a.id)) == len(followups)
    assert len(await repo.list_task_links(parent_task_id=b.id)) == len(followups)


@pytest.mark.asyncio(loop_scope="function")
async def test_create_tasks_reuses_active_batch_at_scale(repo: Repository) -> None:
    """万级活跃行上再入队同一批: 互斥键只查一次, 不按 payload 重扫活跃集."""
    payloads: list[dict[str, object]] = [{"actor_id": i} for i in range(_SCALE_ROWS)]
    first = await repo.create_tasks(TaskType.ACTOR_SCRAPE, payloads, priority=-1)
    assert len(first) == len(payloads)

    with _capture_statements(repo) as captured:
        again = await repo.create_tasks(TaskType.ACTOR_SCRAPE, payloads, priority=-1)

    assert [task.id for task in again] == [task.id for task in first]
    assert await repo.count_tasks(task_types=[TaskType.ACTOR_SCRAPE]) == len(payloads)
    assert len(captured.items) < len(payloads) // 2


@pytest.mark.asyncio(loop_scope="function")
async def test_retry_tasks_batches_clones(repo: Repository) -> None:
    """批量重试一批提交: 克隆为无根裸任务, 行数与顺序都对."""
    originals = await repo.create_tasks(TaskType.SCRAPE, [{"number": f"N-{i}"} for i in range(1_000)])
    original_ids = {task.id for task in originals}
    assert await repo.fail_queued_tasks(error="boom") == len(originals)

    with _capture_statements(repo) as captured:
        clones = await repo.retry_tasks(originals)

    assert [clone.payload for clone in clones] == [task.payload for task in originals]
    assert len({clone.id for clone in clones}) == len(originals)
    assert {clone.id for clone in clones}.isdisjoint(original_ids)
    assert {clone.root_task_id for clone in clones} == {None}
    assert await repo.count_tasks(task_types=[TaskType.SCRAPE]) == 2 * len(originals)
    assert len(captured.items) < len(originals) // 2


@pytest.mark.asyncio(loop_scope="function")
async def test_retry_reuses_active_exclusive_row(repo: Repository) -> None:
    """重试同样走互斥: 已有活跃行时复用, 批内同键去重后只剩一行."""
    first = await repo.create_task(TaskType.ACTOR_SCRAPE, {"actor_id": 3})
    assert first.id is not None
    assert await repo.fail_task(first.id, error="boom") is True
    second = await repo.create_task(TaskType.ACTOR_SCRAPE, {"actor_id": 3})
    assert second.id is not None
    assert await repo.fail_task(second.id, error="boom") is True
    active = await repo.create_task(TaskType.ACTOR_SCRAPE, {"actor_id": 3})
    assert active.id is not None

    clones = await repo.retry_tasks([first, second])

    assert [clone.id for clone in clones] == [active.id]
    # 两个失败行仍是终态行, 不因重试而消失.
    assert await repo.count_tasks(task_types=[TaskType.ACTOR_SCRAPE]) == 3
