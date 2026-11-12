"""全局测试 fixtures"""

import io
import os
from contextlib import contextmanager
from ipaddress import ip_address
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from amane.db.embeddings import EmbeddingStore
from amane.db.models import Resource
from amane.db.repository import Repository
from amane.media import ResourceStore
from tests.schema_template import copy_schema

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Sequence
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """把域名解析成公网地址, 地址字面量按原值: 出站策略的用例不访问网络."""

    async def _resolve(host: str, port: int) -> set[str]:
        try:
            return {str(ip_address(host))}
        except ValueError:
            return {"93.184.216.34"}

    monkeypatch.setattr("amane.net.egress.resolve_addresses", _resolve)


@pytest.fixture
def candidates_first_scandir(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定目录枚举顺序: 名字以 `ad-` 开头的候选排在媒体之前.

    清单触顶的用例, 前提是截断点落在媒体收集完成之前; 文件系统给的顺序是环境属性,
    靠文件名巧合会让用例失去鉴别力. 触顶用例与需要同一前提的入库扫描用例都用它.

    `amane.library.cleanup.inventory.os` 就是 `os` 模块, 因此这是整个进程内的替换, 只给单元级用例用:
    带 worker 的用例上会连带影响别的调用方.
    """
    real_scandir = os.scandir

    @contextmanager
    def ordered(path: str | os.PathLike[str]) -> Iterator[Sequence[os.DirEntry[str]]]:
        with real_scandir(path) as scanned:
            yield sorted(scanned, key=lambda entry: (not entry.name.startswith("ad-"), entry.name))

    monkeypatch.setattr("amane.library.cleanup.inventory.os.scandir", ordered)


def _file_engine(db_path: Path) -> AsyncEngine:
    """文件 SQLite 引擎 (与生产同构: 多连接 + WAL).

    SQLite 自身通过连接级事务隔离 + WAL 控制并发写, 应用层无需加锁.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", connect_args={"timeout": 5})

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        # 测试库数据可丢弃: 关闭 fsync 刷盘加速写入 (生产保持默认 FULL).
        cursor.execute("PRAGMA synchronous=OFF")
        # 旧内存库 (StaticPool) 不启用 FK; 保持该语义, 避免无归属 MediaFile 的用例集体失败.
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> AsyncGenerator[Repository]:
    """
    基于文件 SQLite 的异步 Repository (与生产同构).

    每个测试在 tmp_path 下独立数据库文件, 测试结束自动清理.
    """
    db_path = tmp_path / "amane.db"
    copy_schema(db_path)
    engine = _file_engine(db_path)
    embeddings = EmbeddingStore(tmp_path / "embeddings.db")
    yield Repository(engine, embeddings=embeddings)

    await embeddings.close()

    await engine.dispose()


@pytest_asyncio.fixture
async def resource_engine(tmp_path: Path) -> AsyncGenerator[AsyncEngine]:
    db_path = tmp_path / "resources.db"
    copy_schema(db_path)
    engine = _file_engine(db_path)
    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def resource_store(resource_engine: AsyncEngine, tmp_path: Path) -> ResourceStore:
    """独立的 ResourceStore (文件 DB + tmp 目录).

    handler 现强制注入 ResourceStore; 测试不触发真实下载时仅作占位.
    """
    return ResourceStore(engine=resource_engine, base_dir=tmp_path / "resources")


@pytest.fixture
def set_resource_mime(resource_engine: AsyncEngine) -> Callable[[str, str | None], Awaitable[None]]:
    """改写已落盘资源的媒体类型; 用于构造与盘上内容无关的存量行."""

    async def apply(url: str, mime: str | None) -> None:
        async with AsyncSession(resource_engine, expire_on_commit=False) as session:
            result = await session.exec(select(Resource).where(Resource.url == url))
            record = result.first()
            assert record is not None
            record.mime_type = mime
            await session.commit()

    return apply


@pytest.fixture
def bmp_bytes() -> bytes:
    """最小合法 BMP: 文件头未登记, 须由 PIL 判定."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "BMP")
    return buf.getvalue()
