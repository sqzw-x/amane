"""并发合并的单元测试: 同一键只执行一次请求, 失败与取消不向等待者传播异常."""

import asyncio

import pytest

from amane.enums import Language, MetadataField
from amane.llm import TranslationCoalescer

_KEY = ("Hello world", Language.ZH_CN, MetadataField.TITLE, "system")


class _Gate:
    """produce 在 release 上阻塞, 让并发调用确定地进入同一合并键."""

    def __init__(self, result: str | None = "译文") -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self._result = result

    async def produce(self) -> str | None:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self._result


@pytest.mark.asyncio
async def test_same_key_runs_once_and_reuses_result():
    gate = _Gate()
    coalescer = TranslationCoalescer()

    first = asyncio.create_task(coalescer.run(_KEY, gate.produce))
    await gate.started.wait()
    followers = [asyncio.create_task(coalescer.run(_KEY, gate.produce)) for _ in range(4)]
    gate.release.set()

    assert await asyncio.gather(first, *followers) == ["译文"] * 5
    assert gate.calls == 1


@pytest.mark.asyncio
async def test_result_settled_before_key_released():
    """结算后同一键可以再次执行, 不残留到后续刮削."""
    gate = _Gate()
    gate.release.set()
    coalescer = TranslationCoalescer()

    assert await coalescer.run(_KEY, gate.produce) == "译文"
    assert await coalescer.run(_KEY, gate.produce) == "译文"
    assert gate.calls == 2


@pytest.mark.asyncio
async def test_distinct_keys_run_independently():
    gate = _Gate()
    coalescer = TranslationCoalescer()
    other = ("Hello world", Language.ZH_CN, MetadataField.PLOT, "system")

    first = asyncio.create_task(coalescer.run(_KEY, gate.produce))
    second = asyncio.create_task(coalescer.run(other, gate.produce))
    await gate.started.wait()
    gate.release.set()

    assert await asyncio.gather(first, second) == ["译文", "译文"]
    assert gate.calls == 2


@pytest.mark.asyncio
async def test_none_result_shared_without_retry():
    """首个请求没有译文时, 等待者同样得到 None, 不各自重试."""
    gate = _Gate(result=None)
    coalescer = TranslationCoalescer()

    first = asyncio.create_task(coalescer.run(_KEY, gate.produce))
    await gate.started.wait()
    followers = [asyncio.create_task(coalescer.run(_KEY, gate.produce)) for _ in range(4)]
    gate.release.set()

    assert await asyncio.gather(first, *followers) == [None] * 5
    assert gate.calls == 1


@pytest.mark.asyncio
async def test_leader_exception_reaches_only_leader():
    """首个请求抛出异常时该调用收到异常, 等待者得到 None, 不阻断刮削."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def produce() -> str | None:
        started.set()
        await release.wait()
        raise RuntimeError("上游不可用")

    coalescer = TranslationCoalescer()
    first = asyncio.create_task(coalescer.run(_KEY, produce))
    await started.wait()
    follower = asyncio.create_task(coalescer.run(_KEY, produce))
    release.set()

    with pytest.raises(RuntimeError):
        await first
    assert await follower is None


@pytest.mark.asyncio
async def test_leader_cancellation_releases_waiters():
    """首个请求被取消时等待者得到 None, 不会一并被取消."""
    gate = _Gate()
    coalescer = TranslationCoalescer()

    first = asyncio.create_task(coalescer.run(_KEY, gate.produce))
    await gate.started.wait()
    follower = asyncio.create_task(coalescer.run(_KEY, gate.produce))
    await asyncio.sleep(0)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await follower is None
    assert gate.calls == 1
