"""``Translator``: 管线依赖的翻译面."""

from typing import Protocol, runtime_checkable

from ..enums import Language, MetadataField


@runtime_checkable
class Translator(Protocol):
    async def translate(
        self, text: str, target: Language, field: MetadataField, *, use_cache: bool = True
    ) -> str | None:
        """无需翻译或失败时返回 ``None`` (调用方保留原值).

        ``use_cache=False`` 时跳过缓存读取, 但仍回写以刷新缓存.
        """
        ...
