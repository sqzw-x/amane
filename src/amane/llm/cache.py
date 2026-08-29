"""翻译结果缓存 - 独立 SQLite 文件.

故意不进主 amane.db, 也不走 Alembic: 这是纯缓存, 仅 ``CREATE TABLE IF NOT EXISTS``,
删除文件即清空, 下次自动重建. 与 per-site 爬取缓存正交.

缓存键 = (源文本 hash, 目标语言, 字段):
- 不含 number: 翻译输出只取决于文本本身, 跨番号去重 (系列共用简介/相同标语只译一次).
- 含 field: 因不同字段使用不同提示词 (见 translator._FIELD_HINT), 输出与字段相关.
- 不含 model/temperature: 换模型想重译时直接删除缓存文件即可.

会话级: 单连接经 aiosqlite 内部串行化, 不随 HotSettings 热重载重建.
"""

import asyncio
import hashlib
from pathlib import Path

import aiosqlite
import structlog

from ..enums import Language, MetadataField

logger = structlog.get_logger()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS translations (
    text_hash   TEXT NOT NULL,
    target      TEXT NOT NULL,
    field       TEXT NOT NULL,
    translation TEXT NOT NULL,
    PRIMARY KEY (text_hash, target, field)
)
"""


class TranslationCache:
    """(源文本, 目标语言, 字段) → 译文 的持久化缓存."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> aiosqlite.Connection:
        """惰性建连 - LLM 未启用时不创建文件."""
        if self._conn is None:
            async with self._lock:
                if self._conn is None:
                    conn = await aiosqlite.connect(self._db_path)
                    await conn.execute("PRAGMA journal_mode=WAL")
                    await conn.execute(_SCHEMA)
                    await conn.commit()
                    self._conn = conn
        return self._conn

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get(self, text: str, target: Language, field: MetadataField) -> str | None:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT translation FROM translations WHERE text_hash=? AND target=? AND field=?",
            (self._hash(text), target, field),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def put(self, text: str, target: Language, field: MetadataField, translation: str) -> None:
        conn = await self._ensure()
        await conn.execute(
            "INSERT OR REPLACE INTO translations (text_hash, target, field, translation) VALUES (?, ?, ?, ?)",
            (self._hash(text), target, field, translation),
        )
        await conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
