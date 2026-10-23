"""同一译文键的并发请求合并.

多个任务并发刮削同一番号的不同分集时, 翻译阶段会拿到相同的源文本. 不合并则每个任务各请求一次模型:
既重复计费, 也因采样不同使同一部作品的标题不一致, 整理时落到不同目录. 合并键与译文缓存键同源
(源文本, 目标语言, 字段, system 提示词).

会话级对象: 热重载会重建 translator, 合并状态留在会话层, 过渡期内旧、新 worker 仍共用一次请求.
"""

import asyncio
from collections.abc import Awaitable, Callable

from ..enums import Language, MetadataField

type TranslationKey = tuple[str, Language, MetadataField, str]
"""合并键: 源文本, 目标语言, 字段, system 提示词."""


class TranslationCoalescer:
    """按译文键合并并发请求: 首个调用执行 ``produce``, 后到的调用等待并复用它返回的结果.

    首个请求失败或被取消时, 后到的调用得到 ``None`` 而不是异常, 与调用方「失败返回 ``None``,
    保留原值」的语义一致; 失败结果不写入译文缓存, 下一次刮削会重新请求.
    """

    def __init__(self) -> None:
        self._pending: dict[TranslationKey, asyncio.Future[str | None]] = {}

    async def run(self, key: TranslationKey, produce: Callable[[], Awaitable[str | None]]) -> str | None:
        pending = self._pending.get(key)
        if pending is not None:
            return await pending
        return await self._lead(key, produce)

    async def _lead(self, key: TranslationKey, produce: Callable[[], Awaitable[str | None]]) -> str | None:
        pending: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._pending[key] = pending
        result: str | None = None
        try:
            result = await produce()
            return result
        finally:
            # 登记先于请求: 请求期间到达的调用都能看到这一项, 不会各自再发一次.
            self._pending.pop(key, None)
            if not pending.done():
                pending.set_result(result)
