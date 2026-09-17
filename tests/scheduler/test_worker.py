"""异步任务 worker 测试"""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from amane.db.models import TaskStatus, TaskType
from amane.handlers.protocol import FollowupTask, TaskHandler, TaskResult
from amane.scheduler.worker import AsyncWorker

if TYPE_CHECKING:
    from amane.db.repository import Repository


@dataclass
class Tracker:
    """记录 handler 被调用的参数和结果, 用于测试断言."""

    calls: list[dict] = field(default_factory=list)


class SuccessHandler(TaskHandler):
    payload_type = dict

    def __init__(self, tracker: Tracker | None = None):
        self.tracker = tracker

    async def handle(self, payload: dict):
        if self.tracker:
            self.tracker.calls.append(payload)
        return TaskResult(success=True, result={"echo": payload})


class FailHandler(TaskHandler):
    payload_type = dict

    def __init__(self, tracker: Tracker | None = None):
        self.tracker = tracker

    async def handle(self, payload: dict):
        if self.tracker:
            self.tracker.calls.append(payload)
        return TaskResult(success=False, error="intentional failure")


class SlowHandler(TaskHandler):
    payload_type = dict

    def __init__(self, tracker: Tracker | None = None):
        self.tracker = tracker

    async def handle(self, payload: dict):
        await asyncio.sleep(0.05)
        if self.tracker:
            self.tracker.calls.append(payload)
        return TaskResult(success=True, result={"slow": True})


async def recv(worker: AsyncWorker, n: int, timeout: float = 5.0) -> list[int]:
    """从 worker 的 done channel 接收 n 个完成信号."""
    ids = []
    async with asyncio.timeout(timeout):
        for _ in range(n):
            task_id = await worker._done_queue.get()
            ids.append(task_id)
    return ids


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_stop_blocks_inflight_claim(repo: Repository):
    """stop() 不得放行「在飞 claim」: stop 后新入队任务必须保持 QUEUED.

    回归 CI 偶发 test_filter_type_scrape_sees_children 失败: stop() 只置 ``_running=False``
    并返回, 不等待主循环任务; 若主循环已进入 claim_next_task (在飞), 该 claim 会在
    stop() 返回后完成并认领测试刚创建的任务, 使测试自身的 claim 读到 None.
    """
    tracker = Tracker()
    handlers = {TaskType.SCRAPE: SuccessHandler(tracker)}
    worker = AsyncWorker(repo=repo, handlers=handlers, poll_interval=0.05)

    started = asyncio.Event()
    release = asyncio.Event()
    orig_claim = repo.claim_next_task
    blocked = True

    async def blocked_claim():
        nonlocal blocked
        if blocked:
            blocked = False
            started.set()
            await release.wait()
        return await orig_claim()

    # 实例级遮蔽: 仅阻塞 worker 主循环的第一次 claim, 模拟慢 DB 下在飞 claim
    repo.claim_next_task = blocked_claim  # type: ignore[method-assign]

    worker.start()
    await started.wait()  # worker 已进入 claim 并在飞

    await worker.stop()  # 契约: stop() 返回后不得再有任何 claim 存活

    t = await repo.create_task(TaskType.SCRAPE, payload={"number": "LATE-1"})
    assert t.id is not None
    release.set()
    await asyncio.sleep(0.1)

    fetched = await repo.get_task(t.id)
    assert fetched is not None
    assert fetched.status == TaskStatus.QUEUED, "stop() 后 in-flight claim 不得认领新任务"
    assert tracker.calls == []


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_processes_task(repo: Repository):
    """Worker 拾取排队的任务并执行 handler"""
    tracker = Tracker()
    handlers = {TaskType.SCRAPE: SuccessHandler(tracker)}
    worker = AsyncWorker(repo=repo, handlers=handlers, poll_interval=0.05)

    t = await repo.create_task(TaskType.SCRAPE, payload={"number": "TEST-001"})

    worker.start()
    done_ids = await recv(worker, 1)
    await worker.stop()

    assert done_ids == [t.id]
    assert tracker.calls == [{"number": "TEST-001"}]


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_handles_failure(repo: Repository):
    """Handler 返回失败时 worker 正确处理"""
    tracker = Tracker()
    handlers = {TaskType.SCRAPE: FailHandler(tracker)}
    worker = AsyncWorker(repo=repo, handlers=handlers, poll_interval=0.05)

    t = await repo.create_task(TaskType.SCRAPE, payload={"x": 1})

    worker.start()
    done_ids = await recv(worker, 1)
    await worker.stop()

    assert done_ids == [t.id]
    assert tracker.calls == [{"x": 1}]


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_handles_exception(repo: Repository):
    """Worker 捕获 handler 异常而不崩溃"""
    called = []

    class CrashHandler(TaskHandler):
        def __init__(self):
            super().__init__(payload_t=dict, result_t=dict)

        async def handle(self, payload: dict):
            called.append(payload)
            raise RuntimeError("boom")

    handlers = {TaskType.SCRAPE: CrashHandler()}
    worker = AsyncWorker(repo=repo, handlers=handlers, poll_interval=0.05)

    t = await repo.create_task(TaskType.SCRAPE, payload={"z": 9})

    worker.start()
    done_ids = await recv(worker, 1)
    await worker.stop()

    assert done_ids == [t.id]
    assert called == [{"z": 9}]


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_respects_concurrency(repo: Repository):
    """Worker 限制并发任务执行数"""
    max_concurrent = 0
    current = 0
    lock = asyncio.Lock()

    class ConcurrencyTracker(TaskHandler):
        def __init__(self):
            super().__init__(payload_t=dict, result_t=dict)

        async def handle(self, payload: dict):
            nonlocal max_concurrent, current
            async with lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.05)
            async with lock:
                current -= 1
            return TaskResult(success=True, result={})

    handlers = {TaskType.SCRAPE: ConcurrencyTracker()}
    worker = AsyncWorker(repo=repo, handlers=handlers, poll_interval=0.02, concurrency=2)

    for i in range(4):
        await repo.create_task(TaskType.SCRAPE, payload={"i": i})

    worker.start()
    await recv(worker, 4)
    await worker.stop()

    # 并发不应超过 2
    assert max_concurrent <= 2
    # 但应该有并发 (不是串行)
    assert max_concurrent == 2


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_idle_when_no_tasks(repo: Repository):
    """队列为空时 worker 空闲等待且无错误"""
    handlers = {TaskType.SCRAPE: SuccessHandler()}
    worker = AsyncWorker(repo=repo, handlers=handlers, poll_interval=0.05)

    worker.start()
    await asyncio.sleep(0.1)
    await worker.stop()

    assert worker._done_queue.empty()


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_stop_waits_for_inflight_claim(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    """stop() 不得取消进行中的认领.

    认领的 commit 被打断, 事务就不会正常结束: SQLite 写锁留在池里的连接上,
    紧随其后的 fail_all_running_tasks() 写入在 Windows CI 上就以 database is locked 超时.
    """
    await repo.create_task(TaskType.SCRAPE, payload={"i": 0})

    claim_commit_started = asyncio.Event()
    release_claim_commit = asyncio.Event()
    real_commit = AsyncSession.commit
    armed = True

    async def _commit(self: AsyncSession) -> None:
        nonlocal armed
        if armed:
            armed = False
            claim_commit_started.set()
            await release_claim_commit.wait()
        await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", _commit)

    worker = AsyncWorker(repo=repo, handlers={TaskType.SCRAPE: SuccessHandler()}, poll_interval=0.05)
    worker.start()
    await asyncio.wait_for(claim_commit_started.wait(), timeout=5)

    stop_task = asyncio.create_task(worker.stop())
    await asyncio.sleep(0.05)
    assert not stop_task.done(), "stop() 提前返回: 进行中的认领被取消"

    release_claim_commit.set()
    await asyncio.wait_for(stop_task, timeout=5)

    # 事务已结束, 写锁释放: 后续写入不能撞上锁超时.
    await asyncio.wait_for(repo.create_task(TaskType.SCRAPE, payload={"i": 1}), timeout=5)


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_pause_skips_claim(repo: Repository):
    """暂停后不再认领排队任务; 恢复后继续."""
    tracker = Tracker()
    handlers = {TaskType.SCRAPE: SuccessHandler(tracker)}
    worker = AsyncWorker(repo=repo, handlers=handlers, poll_interval=0.05)

    t = await repo.create_task(TaskType.SCRAPE, payload={"number": "PAUSE-1"})
    assert t.id is not None
    worker.set_paused(True)
    worker.start()
    await asyncio.sleep(0.15)
    fetched = await repo.get_task(t.id)
    assert fetched is not None
    assert fetched.status == TaskStatus.QUEUED
    assert tracker.calls == []

    worker.set_paused(False)
    await recv(worker, 1)
    await worker.stop()
    assert tracker.calls == [{"number": "PAUSE-1"}]


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_completes_with_followups(repo: Repository):
    """Handler 返回 followups 时, worker 完成事务内创建子任务并写 TaskLink."""

    class FollowupHandler(TaskHandler):
        def __init__(self):
            super().__init__(payload_t=dict, result_t=dict)

        async def handle(self, payload: dict):
            return TaskResult(
                success=True,
                result={"ok": True},
                followups=[
                    FollowupTask(key="child-a", task_type=TaskType.SCRAPE, payload={"number": "FO-1"}),
                    FollowupTask(key="child-b", task_type=TaskType.CLEANUP, payload={}, priority=-1),
                ],
            )

    handlers = {TaskType.REFRESH: FollowupHandler()}
    worker = AsyncWorker(repo=repo, handlers=handlers, poll_interval=0.05)

    t = await repo.create_task(TaskType.REFRESH, payload={"library_id": 1})
    assert t.id is not None

    worker.start()
    await recv(worker, 1)
    await worker.stop()

    done = await repo.get_task(t.id)
    assert done is not None
    assert done.status == TaskStatus.DONE
    assert done.result == {"ok": True}

    links = await repo.list_task_links(parent_task_id=t.id)
    assert {link.key for link in links} == {"child-a", "child-b"}
    children = await repo.list_tasks_by_root(t.id)
    assert {c.id for c in children} == {t.id, *{link.child_task_id for link in links}}
    by_key = {link.key: link.child_task_id for link in links}
    child_a = await repo.get_task(by_key["child-a"])
    assert child_a is not None and child_a.payload == {"number": "FO-1"}
    child_b = await repo.get_task(by_key["child-b"])
    assert child_b is not None and child_b.priority == -1


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_no_followups_on_failure(repo: Repository):
    """失败时不创建 on_success 后继."""

    class FailingFollowupHandler(TaskHandler):
        def __init__(self):
            super().__init__(payload_t=dict, result_t=dict)

        async def handle(self, payload: dict):
            return TaskResult(
                success=False,
                error="boom",
                followups=[FollowupTask(key="child", task_type=TaskType.SCRAPE, payload={"number": "X"})],
            )

    handlers = {TaskType.REFRESH: FailingFollowupHandler()}
    worker = AsyncWorker(repo=repo, handlers=handlers, poll_interval=0.05)

    t = await repo.create_task(TaskType.REFRESH, payload={"library_id": 1})
    assert t.id is not None

    worker.start()
    await recv(worker, 1)
    await worker.stop()

    assert await repo.list_task_links(parent_task_id=t.id) == []
    assert await repo.list_tasks_by_root(t.id) == []
