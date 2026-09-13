"""翻译结果缓存. 独立 SQLite, 不写入主 ``amane.db``, 不经 Alembic.

仅 ``CREATE TABLE IF NOT EXISTS``; 删除文件即清空, 下次自动重建. 与 per-site 爬取缓存正交.

缓存键 = (源文本 hash, 目标语言, 字段, system 提示词 hash):
- 不含 number: 译文只取决于文本本身, 跨番号去重.
- 含 field: 不同字段使用不同字段说明, 输出与字段相关.
- 含提示词 hash: 自定义提示词与内置提示词的改动都会改变实际 system 内容, 旧译文随即失效.
- 不含 model/temperature: 换模型重译时删除缓存文件.

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
    prompt_hash TEXT NOT NULL,
    translation TEXT NOT NULL,
    PRIMARY KEY (text_hash, target, field, prompt_hash)
)
"""

_COLUMNS: frozenset[str] = frozenset({"text_hash", "target", "field", "prompt_hash", "translation"})


class TranslationCache:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> aiosqlite.Connection:
        # LLM 未启用时不创建文件.
        if self._conn is None:
            async with self._lock:
                if self._conn is None:
                    conn = await aiosqlite.connect(self._db_path)
                    await conn.execute("PRAGMA journal_mode=WAL")
                    await self._drop_foreign_schema(conn)
                    await conn.execute(_SCHEMA)
                    await conn.commit()
                    self._conn = conn
        return self._conn

    @staticmethod
    async def _drop_foreign_schema(conn: aiosqlite.Connection) -> None:
        """旧版键不含提示词, 列集合不符时整表重建: 纯缓存, 无迁移负担."""
        async with conn.execute("PRAGMA table_info(translations)") as cur:
            columns = {str(row[1]) async for row in cur}
        if columns and columns != _COLUMNS:
            await conn.execute("DROP TABLE translations")
            logger.info("translation cache dropped", reason="incompatible schema")

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get(self, text: str, target: Language, field: MetadataField, system_prompt: str) -> str | None:
        conn = await self._ensure()
        async with conn.execute(
            "SELECT translation FROM translations WHERE text_hash=? AND target=? AND field=? AND prompt_hash=?",
            (self._hash(text), target, field, self._hash(system_prompt)),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def put(
        self, text: str, target: Language, field: MetadataField, system_prompt: str, translation: str
    ) -> None:
        conn = await self._ensure()
        await conn.execute(
            "INSERT OR REPLACE INTO translations"
            " (text_hash, target, field, prompt_hash, translation) VALUES (?, ?, ?, ?, ?)",
            (self._hash(text), target, field, self._hash(system_prompt), translation),
        )
        await conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
