from collections.abc import Iterable, Sequence
from typing import NamedTuple

from pydantic import BaseModel
from sqlalchemy import asc, func, insert, update
from sqlalchemy import delete as sqla_delete
from sqlalchemy.sql.functions import coalesce, count
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from ..models import SortOrder, Task, TaskLink, TaskSortField, TaskStatus, TaskType
from ..repo_types import _TASK_SORT_COLUMNS, _order_clause, _utcnow
from .base import RepositoryMixinBase, chunks

_ACTIVE_STATUSES = (TaskStatus.QUEUED, TaskStatus.RUNNING)
# 单条 INSERT 的行数: 每行至多 8 个绑定变量, 100 行合 800, 仍在 SQLite 旧默认的 999 变量上限内.
_INSERT_CHUNK_SIZE = 100
# 互斥键在 payload 里; 同键已有 queued/running 则复用, 不另建行.
# 其余类型每次新建; 同库串行由 handler 内的锁保证.
_EXCLUSIVE_FIELDS: dict[TaskType, str] = {
    TaskType.ACTOR_SCRAPE: "actor_id",
}


def _scope_tasks[T](
    stmt: SelectOfScalar[T],
    *,
    task_ids: Iterable[int] | None = None,
    statuses: Iterable[TaskStatus] | None = None,
    task_types: Iterable[TaskType] | None = None,
) -> SelectOfScalar[T]:
    if task_ids is not None:
        stmt = stmt.where(col(Task.id).in_(list(task_ids)))
    if statuses:
        stmt = stmt.where(col(Task.status).in_(list(statuses)))
    if task_types:
        stmt = stmt.where(col(Task.type).in_(list(task_types)))
    return stmt


def _payload_dict(payload: dict[str, object] | BaseModel) -> dict[str, object]:
    return payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload


def _exclusive_value(payload: dict[str, object], field: str) -> int | None:
    raw = payload.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


class _PendingTask(NamedTuple):
    """一条待入队任务. ``root_task_id`` 是所属链根, 裸任务为空."""

    task_type: TaskType
    payload: dict[str, object]
    priority: int
    root_task_id: int | None = None


async def _active_exclusive_ids(session: AsyncSession, task_type: TaskType, field: str) -> dict[int, int]:
    """一次查出该类型全部活跃行的互斥键 → 行 id, 供整批判重.

    只认 JSON 整数: ``true`` / ``1.0`` / 字符串与 ``_exclusive_value`` 的口径一致, 都不参与互斥.
    逐条 payload 各查一次时, 每条都要读出全部活跃行再在 Python 里比较, 后继一多就是平方级.
    """
    path = f"$.{field}"
    extracted = func.json_extract(col(Task.payload), path)
    stmt = select(extracted, col(Task.id)).where(
        col(Task.type) == task_type,
        col(Task.status).in_(_ACTIVE_STATUSES),
        func.json_type(col(Task.payload), path) == "integer",
    )
    out: dict[int, int] = {}
    for value, task_id in (await session.exec(stmt)).all():
        assert task_id is not None
        out[int(value)] = task_id
    return out


async def _insert_tasks(session: AsyncSession, pending: Sequence[_PendingTask]) -> list[Task]:
    """一批入队, 返回值与 ``pending`` 同序.

    调用方须在读取前取得 SQLite 写锁, 覆盖互斥判定与新行 id 分配. 返回行按显式 id 对应输入,
    不依赖 ``RETURNING`` 顺序; SQLite 的有序 RETURNING 会退回逐行插入. 提交后属性仍可读.
    """
    if not pending:
        return []

    active: dict[TaskType, dict[int, int]] = {}
    for task_type in dict.fromkeys(row.task_type for row in pending):
        field = _EXCLUSIVE_FIELDS.get(task_type)
        if field is not None:
            active[task_type] = await _active_exclusive_ids(session, task_type, field)

    values: list[int | None] = []
    for row in pending:
        field = _EXCLUSIVE_FIELDS.get(row.task_type)
        values.append(_exclusive_value(row.payload, field) if field is not None else None)

    # 命中的活跃行按 id 一次取回; 逐个 session.get 会让复用型的大批入队退化成逐行查询.
    hit_ids = {
        active[row.task_type][value]
        for row, value in zip(pending, values, strict=True)
        if value is not None and value in active[row.task_type]
    }
    reused: dict[int, Task] = {}
    for chunk in chunks(sorted(hit_ids)):
        for task in (await session.exec(select(Task).where(col(Task.id).in_(chunk)))).all():
            assert task.id is not None
            reused[task.id] = task

    slots: list[Task | None] = [None] * len(pending)
    aliases: dict[int, int] = {}
    first_index: dict[tuple[TaskType, int], int] = {}
    new_rows: list[_PendingTask] = []
    new_indexes: list[int] = []
    for index, row in enumerate(pending):
        value = values[index]
        if value is not None:
            key = (row.task_type, value)
            existing_id = active[row.task_type].get(value)
            existing = reused.get(existing_id) if existing_id is not None else None
            if existing is not None:
                if row.root_task_id is not None and existing.root_task_id is None:
                    existing.root_task_id = row.root_task_id
                    session.add(existing)
                slots[index] = existing
                continue
            first = first_index.get(key)
            if first is not None:
                aliases[index] = first
                continue
            first_index[key] = index
        new_rows.append(row)
        new_indexes.append(index)

    if new_rows:
        last_id = (await session.exec(select(func.max(col(Task.id))))).one() or 0
        stmt = insert(Task).returning(Task)
        for start in range(0, len(new_rows), _INSERT_CHUNK_SIZE):
            batch = new_rows[start : start + _INSERT_CHUNK_SIZE]
            inserted = (
                await session.exec(
                    stmt,
                    params=[
                        {
                            "id": last_id + start + offset + 1,
                            "type": row.task_type,
                            "payload": row.payload,
                            "priority": row.priority,
                            "root_task_id": row.root_task_id,
                        }
                        for offset, row in enumerate(batch)
                    ],
                )
            ).scalars()
            by_id = {task.id: task for task in inserted}
            for offset, index in enumerate(new_indexes[start : start + _INSERT_CHUNK_SIZE]):
                slots[index] = by_id[last_id + start + offset + 1]
    for index, first in aliases.items():
        slots[index] = slots[first]
    assert all(slot is not None for slot in slots)
    return [slot for slot in slots if slot is not None]


_IS_CHAIN_ROOT = (col(Task.root_task_id).is_(None)) | (col(Task.root_task_id) == col(Task.id))


def _matching_root_ids(
    statuses: Iterable[TaskStatus] | None, task_types: Iterable[TaskType] | None
) -> SelectOfScalar[int]:
    """有 root 用 root, 裸任务用自身 id."""
    stmt = select(coalesce(col(Task.root_task_id), col(Task.id)))
    return _scope_tasks(stmt, statuses=statuses, task_types=task_types).distinct()


async def _ids_with_external_descendants(session: AsyncSession, ids: Sequence[int]) -> set[int]:
    """待删集合里存在集合外后裔的那些 id (含菱形多父).
    有未纳入本次删除的后裔时祖先须跳过, 否则剩余子任务失去链根后无法在列表中显示.
    """
    id_set = set(ids)
    if not id_set:
        return set()
    children_of: dict[int, list[int]] = {}
    frontier = list(id_set)
    visited = set(id_set)
    while frontier:
        next_frontier: list[int] = []
        # 每层可达上千个节点, 整批放进一条 IN 会超过绑定变量上限.
        for chunk in chunks(frontier):
            rows = await session.exec(
                select(TaskLink.parent_task_id, TaskLink.child_task_id).where(col(TaskLink.parent_task_id).in_(chunk))
            )
            for parent, child in rows.all():
                if parent is None or child is None:
                    continue
                children_of.setdefault(parent, []).append(child)
                if child not in visited:
                    visited.add(child)
                    next_frontier.append(child)
        frontier = next_frontier
    external = visited - id_set
    if not external:
        return set()
    parents_of: dict[int, list[int]] = {}
    for parent, kids in children_of.items():
        for child in kids:
            parents_of.setdefault(child, []).append(parent)
    protected: set[int] = set()
    stack = list(external)
    seen = set(external)
    while stack:
        node = stack.pop()
        for parent in parents_of.get(node, []):
            if parent in id_set:
                protected.add(parent)
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return protected


class TasksRepoMixin(RepositoryMixinBase):
    async def create_task(self, task_type: TaskType, payload: dict[str, object] | BaseModel, priority: int = 0) -> Task:
        tasks = await self.create_tasks(task_type, [payload], priority=priority)
        return tasks[0]

    async def create_tasks(
        self,
        task_type: TaskType,
        payloads: Sequence[dict[str, object] | BaseModel],
        priority: int = 0,
    ) -> list[Task]:
        if not payloads:
            return []
        pending = [_PendingTask(task_type, _payload_dict(payload), priority) for payload in payloads]
        async with self._task_insert_lock, self._session() as session:
            connection = await session.connection()
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            tasks = await _insert_tasks(session, pending)
            await session.commit()
            return tasks

    async def get_task(self, task_id: int) -> Task | None:
        async with self._session() as session:
            return await session.get(Task, task_id)

    async def claim_next_task(self) -> Task | None:
        """认领优先级最高的 QUEUED 行, 标为 RUNNING.

        选择与写入分两条语句, 因此 UPDATE 再带 ``status == QUEUED``: 候选在两步之间被取消,
        或被并发的认领取走时命中 0 行, 本轮不认领 (下一轮再取).
        """
        async with self._session() as session:
            candidate = (
                await session.exec(
                    select(col(Task.id))
                    .where(col(Task.status) == TaskStatus.QUEUED)
                    .order_by(col(Task.priority).desc(), asc(col(Task.created_at)), col(Task.id).asc())
                    .limit(1)
                )
            ).first()
            if candidate is None:
                return None
            updated = await session.exec(
                update(Task)
                .where(col(Task.id) == candidate, col(Task.status) == TaskStatus.QUEUED)
                .values(status=TaskStatus.RUNNING, started_at=_utcnow())
            )
            if not updated.rowcount:
                await session.rollback()
                return None
            task = await session.get(Task, candidate)
            await session.commit()
            return task

    async def complete_task(self, task_id: int, result: dict[str, object] | None = None) -> bool:
        """仅 ``RUNNING → DONE``, 返回是否命中. 已进入终态的行不被覆盖."""
        async with self._session() as session:
            updated = await session.exec(
                update(Task)
                .where(col(Task.id) == task_id, col(Task.status) == TaskStatus.RUNNING)
                .values(status=TaskStatus.DONE, result=result, finished_at=_utcnow())
            )
            await session.commit()
            return bool(updated.rowcount)

    async def complete_task_with_followups(
        self,
        task_id: int,
        result: dict[str, object] | None,
        followups: Sequence[tuple[str, TaskType, dict[str, object], int]],
    ) -> list[Task]:
        """父完成与子创建须在同一事务. 同父同 key 只留第一条, UNIQUE(parent, key) 拒绝重复.

        先以条件 UPDATE 取得 ``RUNNING → DONE``; 命中 0 行说明父任务不在运行中 (已终态或已删除), 不创建后继.
        """
        async with self._task_insert_lock, self._session() as session:
            updated = await session.exec(
                update(Task)
                .where(col(Task.id) == task_id, col(Task.status) == TaskStatus.RUNNING)
                .values(
                    status=TaskStatus.DONE,
                    result=result,
                    finished_at=_utcnow(),
                    # 裸任务成为链根: 指向自己.
                    root_task_id=coalesce(col(Task.root_task_id), col(Task.id)),
                )
            )
            if not updated.rowcount:
                await session.rollback()
                return []
            task = await session.get(Task, task_id)
            assert task is not None
            root_id = task.root_task_id or task_id
            seen_keys: set[str] = set()
            pending: list[_PendingTask] = []
            keys: list[str] = []
            for key, task_type, payload, priority in followups:
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                pending.append(_PendingTask(task_type, payload, priority, root_id))
                keys.append(key)
            children = await _insert_tasks(session, pending)
            links: list[dict[str, object]] = []
            for child, key in zip(children, keys, strict=True):
                assert child.id is not None
                links.append({"parent_task_id": task_id, "child_task_id": child.id, "key": key})
            for start in range(0, len(links), _INSERT_CHUNK_SIZE):
                await session.exec(insert(TaskLink), params=links[start : start + _INSERT_CHUNK_SIZE])
            await session.commit()
            return children

    async def fail_task(self, task_id: int, error: str, result: dict[str, object] | None = None) -> bool:
        """把 QUEUED / RUNNING 标为 FAILED, 返回是否命中; 终态不覆盖.

        排队中与已认领的任务都可能走到失败路径, 谓词因此是两种活动状态.
        失败也可带结果载荷: 刮削失败时站点明细仍要能展示; None 会清掉上一次运行的残留.
        """
        async with self._session() as session:
            updated = await session.exec(
                update(Task)
                .where(
                    col(Task.id) == task_id,
                    col(Task.status).in_((TaskStatus.QUEUED, TaskStatus.RUNNING)),
                )
                .values(
                    status=TaskStatus.FAILED,
                    error=error,
                    result=result,
                    retries=col(Task.retries) + 1,
                    finished_at=_utcnow(),
                )
            )
            await session.commit()
            return bool(updated.rowcount)

    async def fail_running_task(self, task_id: int, error: str, result: dict[str, object] | None = None) -> bool:
        """仅当任务仍为 RUNNING 时标记失败; 返回是否命中.

        取消回退与取消兜底使用: 已进入终态的任务不得被覆盖为 FAILED. 取消同样落库载荷.
        """
        async with self._session() as session:
            updated = await session.exec(
                update(Task)
                .where(col(Task.id) == task_id, col(Task.status) == TaskStatus.RUNNING)
                .values(
                    status=TaskStatus.FAILED,
                    error=error,
                    result=result,
                    retries=col(Task.retries) + 1,
                    finished_at=_utcnow(),
                )
            )
            await session.commit()
            return bool(updated.rowcount)

    async def fail_all_running_tasks(self) -> int:
        """将全部 RUNNING 标为 FAILED (进程重启后的僵尸任务)."""
        async with self._session() as session:
            stmt = (
                update(Task)
                .where(col(Task.status) == TaskStatus.RUNNING)
                .values(
                    {
                        col(Task.status): TaskStatus.FAILED,
                        col(Task.error): "Task marked as failed due to application restart",
                        col(Task.finished_at): _utcnow(),
                    }
                )
            )
            result = await session.exec(stmt)
            await session.commit()
            return result.rowcount

    async def list_tasks(
        self,
        statuses: Iterable[TaskStatus] | None = None,
        task_types: Iterable[TaskType] | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: TaskSortField = TaskSortField.CREATED_AT,
        order: SortOrder = SortOrder.DESC,
        *,
        roots_only: bool = False,
    ) -> list[Task]:
        async with self._session() as session:
            if roots_only and (statuses or task_types):
                # roots_only 只约束列表显示哪一层: 筛选仍匹配全部任务, 再还原链根.
                stmt = select(Task).where(col(Task.id).in_(_matching_root_ids(statuses, task_types)), _IS_CHAIN_ROOT)
            else:
                stmt = _scope_tasks(select(Task), statuses=statuses, task_types=task_types)
                if roots_only:
                    stmt = stmt.where(_IS_CHAIN_ROOT)
            stmt = (
                stmt.order_by(_order_clause(_TASK_SORT_COLUMNS[sort_by], order), col(Task.id).asc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.exec(stmt)
            return list(result.all())

    async def count_tasks(
        self,
        statuses: Iterable[TaskStatus] | None = None,
        task_types: Iterable[TaskType] | None = None,
        *,
        roots_only: bool = False,
    ) -> int:
        async with self._session() as session:
            if roots_only and (statuses or task_types):
                stmt = select(count()).where(col(Task.id).in_(_matching_root_ids(statuses, task_types)), _IS_CHAIN_ROOT)
            else:
                base = _scope_tasks(select(Task), statuses=statuses, task_types=task_types)
                if roots_only:
                    base = base.where(_IS_CHAIN_ROOT)
                stmt = select(count()).select_from(base.subquery())
            result = await session.exec(stmt)
            return result.one() or 0

    async def list_task_links(
        self, *, parent_task_id: int | None = None, child_task_id: int | None = None
    ) -> list[TaskLink]:
        """可按父或子过滤."""
        async with self._session() as session:
            stmt = select(TaskLink)
            if parent_task_id is not None:
                stmt = stmt.where(col(TaskLink.parent_task_id) == parent_task_id)
            if child_task_id is not None:
                stmt = stmt.where(col(TaskLink.child_task_id) == child_task_id)
            stmt = stmt.order_by(col(TaskLink.id))
            result = await session.exec(stmt)
            return list(result.all())

    async def list_tasks_by_root(self, root_task_id: int) -> list[Task]:
        """同一链全部任务 (含根). root_task_id 在完成事务内写入."""
        async with self._session() as session:
            result = await session.exec(
                select(Task).where(col(Task.root_task_id) == root_task_id).order_by(col(Task.created_at), col(Task.id))
            )
            return list(result.all())

    async def list_children(
        self, parent_task_id: int, *, limit: int | None = None, offset: int = 0
    ) -> list[tuple[Task, str]]:
        """直接子任务, 按边创建顺序, 每项带出边 key."""
        async with self._session() as session:
            stmt = (
                select(Task, col(TaskLink.key))
                .join(TaskLink, col(TaskLink.child_task_id) == col(Task.id))
                .where(col(TaskLink.parent_task_id) == parent_task_id)
                .order_by(col(TaskLink.id))
            )
            if offset:
                stmt = stmt.offset(offset)
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = (await session.exec(stmt)).all()
            return [(task, key) for task, key in rows]

    async def child_status_counts(self, parent_task_ids: Iterable[int]) -> dict[int, dict[TaskStatus, int]]:
        """返回 {parent_id: {status: n}}."""
        ids = list(parent_task_ids)
        if not ids:
            return {}
        async with self._session() as session:
            stmt = (
                select(col(TaskLink.parent_task_id), col(Task.status), count())
                .join(Task, col(Task.id) == col(TaskLink.child_task_id))
                .where(col(TaskLink.parent_task_id).in_(ids))
                .group_by(col(TaskLink.parent_task_id), col(Task.status))
            )
            out: dict[int, dict[TaskStatus, int]] = {}
            for parent_id, status, n in (await session.exec(stmt)).all():
                bucket = out.setdefault(parent_id, {})
                st = status if isinstance(status, TaskStatus) else TaskStatus(status)
                bucket[st] = int(n)
            return out

    async def find_tasks(
        self,
        *,
        task_ids: Iterable[int] | None = None,
        statuses: Iterable[TaskStatus] | None = None,
        task_types: Iterable[TaskType] | None = None,
    ) -> list[Task]:
        """不分页. task_ids 为空可迭代视为无匹配; 大批 id 分批绑定."""
        async with self._session() as session:
            if task_ids is None:
                stmt = _scope_tasks(select(Task), statuses=statuses, task_types=task_types)
                return list((await session.exec(stmt)).all())
            ids = list(task_ids)
            if not ids:
                return []
            found: list[Task] = []
            for chunk in chunks(ids):
                stmt = _scope_tasks(select(Task), task_ids=chunk, statuses=statuses, task_types=task_types)
                found.extend((await session.exec(stmt)).all())
            return found

    async def fail_queued_tasks(
        self,
        *,
        error: str,
        task_ids: Iterable[int] | None = None,
        task_types: Iterable[TaskType] | None = None,
    ) -> int:
        """只标 QUEUED. 返回更新行数."""
        if task_ids is not None:
            ids = list(task_ids)
            if not ids:
                return 0
        else:
            ids = None
        async with self._session() as session:
            stmt = update(Task).where(col(Task.status) == TaskStatus.QUEUED)
            if ids is not None:
                stmt = stmt.where(col(Task.id).in_(ids))
            if task_types:
                stmt = stmt.where(col(Task.type).in_(list(task_types)))
            stmt = stmt.values(
                {
                    col(Task.status): TaskStatus.FAILED,
                    col(Task.error): error,
                    col(Task.finished_at): _utcnow(),
                    col(Task.retries): col(Task.retries) + 1,
                }
            )
            result = await session.exec(stmt)
            await session.commit()
            return result.rowcount

    async def retry_tasks(self, tasks: Sequence[Task]) -> list[Task]:
        """克隆为无根裸任务, 一批提交. 不保留原链归属, 否则会与原有后继冲突."""
        if not tasks:
            return []
        pending = [_PendingTask(task.type, task.payload or {}, task.priority) for task in tasks]
        async with self._task_insert_lock, self._session() as session:
            connection = await session.connection()
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            clones = await _insert_tasks(session, pending)
            await session.commit()
            created: list[Task] = []
            seen: set[int] = set()
            for clone in clones:
                assert clone.id is not None
                if clone.id in seen:
                    continue
                seen.add(clone.id)
                created.append(clone)
            return created

    async def delete_task(self, task_id: int) -> bool:
        """有未纳入本次删除的后裔时拒绝, 否则剩余子任务失去链根后无法在列表中显示."""
        async with self._session() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return False
            if await _ids_with_external_descendants(session, [task_id]):
                return False
            await session.exec(
                sqla_delete(TaskLink).where(
                    (col(TaskLink.parent_task_id) == task_id) | (col(TaskLink.child_task_id) == task_id)
                )
            )
            await session.delete(task)
            await session.commit()
            return True

    async def delete_tasks(self, task_ids: Iterable[int | None]) -> int:
        """含相关后继边. 存在集合外后裔的行跳过. 边与行都分批删除, 一次提交."""
        ids = [i for i in task_ids if i is not None]
        if not ids:
            return 0
        async with self._session() as session:
            protected = await _ids_with_external_descendants(session, ids)
            deletable = [i for i in ids if i not in protected]
            if not deletable:
                return 0
            # 整批任务组可达上万行, 单条语句绑定全部 id 会超过 SQLite 变量上限.
            for chunk in chunks(deletable):
                await session.exec(
                    sqla_delete(TaskLink).where(
                        (col(TaskLink.parent_task_id).in_(chunk)) | (col(TaskLink.child_task_id).in_(chunk))
                    )
                )
            deleted = 0
            for chunk in chunks(deletable):
                result = await session.exec(sqla_delete(Task).where(col(Task.id).in_(chunk)))
                deleted += result.rowcount or 0
            await session.commit()
            return deleted
