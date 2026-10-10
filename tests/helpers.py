import asyncio
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from alembic.config import Config

from amane.db.sqlite_migrate import migrations_dir

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from amane.db.models import TaskType
    from amane.db.repository import Repository
    from amane.handlers.protocol import TaskHandler


# 等待「某个条件最终成立」时的预算. 超售的 CI runner 上单次事件循环轮转可达数百毫秒,
# 秒级预算会把「慢」误判成「没发生」; 条件本身仍然由事件判定, 这里只放大上限.
LOAD_TOLERANT_TIMEOUT = 30.0


def make_junction(link: Path, target: Path) -> None:
    """Windows 专有: 建一个目录交接点. 其它平台没有等价物, 调用方自行 skip."""
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"mklink /J 失败: {done.stdout.decode()} {done.stderr.decode()}")


def alembic_config(db_path: Path) -> Config:
    """迁移用例的配置: 与生产同源 (程序化指定脚本目录与 URL), 不加载 ``alembic.ini``.

    加载 ini 会让 ``env.py`` 执行 ``fileConfig``, 按其默认值关闭进程内已存在的 logger.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir()))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def patch_path(obj: type | Callable[..., object]) -> str:
    """根据对象动态生成 patch 路径"""
    return f"{obj.__module__}.{obj.__qualname__}"


def assert_exhaustive_enum[T: Enum](v: Iterable[T], e: type[T], msg: str = "", allow_extra: bool = False) -> None:
    """断言 v 包含了枚举类型 e 的所有成员. 当 allow_extra=True 时允许 v 包含额外成员."""
    missing = set(e) - set(v)
    if missing:
        raise AssertionError(f"Missing enum members: {missing}" + (f" ({msg})" if msg else ""))

    if allow_extra:
        extra = set(v) - set(e)
        if extra:
            raise AssertionError(f"Extra enum members: {extra}" + (f" ({msg})" if msg else ""))


class AsyncTaskRunner:
    """
    异步任务执行器, 替代生产环境的 AsyncWorker.

    在测试中用于逐个认领并执行任务, 方便断言任务处理结果.
    """

    def __init__(self, repo: Repository, handlers: dict[TaskType, TaskHandler[Any, Any]] | None = None):
        self._repo = repo
        self._handlers: dict[TaskType, TaskHandler[Any, Any]] = handlers or {}

    def register_handler(self, task_type: TaskType, handler: TaskHandler[Any, Any]) -> None:
        """为指定任务类型注册处理器"""
        self._handlers[task_type] = handler

    async def process_one(self) -> bool:
        """
        认领并处理下一个排队中的任务.

        如果处理了一个任务则返回 True, 如果队列为空则返回 False.
        """
        task = await self._repo.claim_next_task()
        if task is None:
            return False

        assert task.id is not None

        handler = self._handlers.get(task.type)
        if handler is None:
            await self._repo.fail_task(task.id, error=f"No handler registered for {task.type}")
            return True

        try:
            typed_payload = handler.parse_payload(task.payload)
        except (TypeError, KeyError, ValueError) as e:
            await self._repo.fail_task(task.id, error=f"Invalid payload: {e}")
            return True

        try:
            result = await handler.handle(typed_payload)
        except Exception as e:
            await self._repo.fail_task(task.id, error=str(e))
            return True

        if result.success:
            followups = [(f.key, f.task_type, f.payload, f.priority) for f in (result.followups or [])]
            await self._repo.complete_task_with_followups(task.id, result.as_dict(), followups)
        else:
            await self._repo.fail_task(task.id, error=result.error or "Unknown error")

        return True


class WaitTimeout(AssertionError):
    """轮询超时仍未满足条件."""


def wait_for[T](
    fn: Callable[[], T],
    *,
    timeout: float = 3.0,
    interval: float = 0.1,
    duration: float = 0.0,
    ignore: tuple = (AssertionError,),
) -> T:
    """
    用于需要等待某个条件成立的测试场景, 封装轮询逻辑.

    周期性执行 fn() 直到:
     - 不抛 `ignore` 中的异常, 且返回值不为 False/None
     - 超时 -> 抛 WaitTimeout 并附带最后一次失败原因
     - 如果 duration > 0 则在首次条件满足后继续检查指定时间
    """
    if duration > timeout:
        raise ValueError("duration cannot be greater than timeout")
    deadline = time.monotonic() + timeout
    last_exc: BaseException | None = None
    first_success: float | None = None
    while True:
        try:
            r = fn()
            if r is not None and r is not False:  # 条件满足
                if duration <= 0:
                    return r
                first_success = first_success or time.monotonic()
                if time.monotonic() - first_success >= duration:
                    return r
            else:
                first_success = None
            last_exc = AssertionError(f"got falsy result: {r!r}")
        except ignore as e:
            last_exc = e

        if time.monotonic() >= deadline:
            if first_success is not None:
                raise WaitTimeout(
                    f"condition only held for {time.monotonic() - first_success:.1f}s, less than required {duration}s"
                ) from last_exc
            raise WaitTimeout(
                f"condition not met within {timeout}s (interval={interval}s). last error: {last_exc!r}"
            ) from last_exc
        time.sleep(interval)


async def await_for[T](
    fn: Callable[[], Awaitable[T]],
    *,
    timeout=5.0,
    interval=0.1,
    duration: float = 0.0,
    ignore=(AssertionError,),
) -> T:
    if duration > timeout:
        raise ValueError("duration cannot be greater than timeout")
    deadline = asyncio.get_event_loop().time() + timeout
    last_exc: BaseException | None = None
    first_success: float | None = None
    while True:
        try:
            r = await fn()
            if r is not None and r is not False:
                if duration <= 0:
                    return r
                first_success = first_success or time.monotonic()
                if time.monotonic() - first_success >= duration:
                    return r
            else:
                first_success = None
            last_exc = AssertionError(f"falsy: {r!r}")
        except ignore as e:
            last_exc = e

        if asyncio.get_event_loop().time() >= deadline:
            if first_success is not None:
                raise WaitTimeout(
                    f"condition only held for {asyncio.get_event_loop().time() - first_success:.1f}s, less than required {duration}s"
                ) from last_exc
            raise WaitTimeout(
                f"condition not met within {timeout}s (interval={interval}s). last error: {last_exc!r}"
            ) from last_exc
        await asyncio.sleep(interval)


class LoopProgressProbe:
    """判定一段同步调用是否阻塞了事件循环, 判据是事件顺序而不是各自的耗时.

    把 ``wait`` 插入被测的同步路径, 把 ``marker`` 作为同一循环上的对照协程, 两者并发执行:
    ``wait`` 只在 ``marker`` 已经在循环上执行过之后才返回. 循环确实被同步调用独占时
    ``marker`` 没有机会运行, ``wait`` 到点返回失败, ``passed`` 保持 False.
    """

    def __init__(self, timeout: float = LOAD_TOLERANT_TIMEOUT) -> None:
        self._entered = threading.Event()
        self._released = threading.Event()
        self._timeout = timeout
        self.passed = False

    def wait(self) -> None:
        """在工作线程里等待 ``marker`` 放行."""
        self._entered.set()
        self.passed = self._released.wait(timeout=self._timeout)

    async def marker(self) -> None:
        """对照协程: 观察到 ``wait`` 已进入才放行它; 到点仍未进入则不再放行."""
        deadline = asyncio.get_running_loop().time() + self._timeout
        while not self._entered.is_set():
            if asyncio.get_running_loop().time() >= deadline:
                return
            await asyncio.sleep(0.01)
        self._released.set()
