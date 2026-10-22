"""URL 级下载缓存. 未被 Metadata 引用的条目由 CLEANUP 回收, 不做 LRU.

派生资源用合成 locator ``derived:{sha256(src)}:{op}:{args}``.
就地超分覆盖原文件, URL 不变, meta 打 ``sr`` 标记.
内部 URL ``/api/resources/{hash}`` 是对既有 Resource 的引用, 解析为本地文件而不发请求.
"""

import asyncio
import hashlib
import mimetypes
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

import structlog
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import Resource

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

    from ..net.http import WebClient

logger = structlog.get_logger()

RESOURCE_URL_PREFIX = "/api/resources"

# 上游把已下架资源重定向到 200 的占位图 (DMM 的 now_printing) 时不能算获取成功:
# 否则该 URL 会被判为可用并前置, 前端最终显示占位图本身.
_PLACEHOLDER_PATH_MARKERS: tuple[str, ...] = ("/now_printing/",)


def _is_placeholder_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return any(marker in path for marker in _PLACEHOLDER_PATH_MARKERS)


def internal_url_hash(url: str) -> str | None:
    """内部 URL 的 hash; 非内部 URL 返回 None. 查询串与额外路径段一律剥离."""
    prefix = f"{RESOURCE_URL_PREFIX}/"
    if not url.startswith(prefix):
        return None
    token = url[len(prefix) :].split("?", 1)[0].split("/", 1)[0]
    return token or None


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def derived_locator(src_url: str, op: str, args: str) -> str:
    # 同一 (src_url, op, args) 恒得同一 locator, 无需反推 url.
    inner = hashlib.sha256(src_url.encode()).hexdigest()
    return f"derived:{inner}:{op}:{args}"


def _guess_ext(url: str) -> str:
    path = url.split("?")[0]
    ext = Path(path).suffix.lower()
    if ext and len(ext) <= 5:
        return ext
    return ".bin"


@dataclass
class AcquireResult:
    success: bool
    path: Path | None = None
    used_url: str | None = None
    failed: list[str] | None = None


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    waiters: int = 0


@dataclass(frozen=True)
class ResolvedSource:
    """处理入口的源解析结果: 底层 locator 与本地文件."""

    locator: str
    path: Path


class UpscaleOutcome(StrEnum):
    """``SKIPPED`` 表示盘上已是记录所指的那份字节, 调用方不应计作失败."""

    UPSCALED = "upscaled"
    SKIPPED = "skipped"
    FAILED = "failed"


class ResourceStore:
    def __init__(self, engine: AsyncEngine, base_dir: Path):
        self._engine = engine
        self._base_dir = base_dir
        self._guards: dict[str, _LockEntry] = {}
        self._guards_lock = asyncio.Lock()
        base_dir.mkdir(parents=True, exist_ok=True)

    def _session(self) -> AsyncSession:
        return AsyncSession(self._engine, expire_on_commit=False)

    @asynccontextmanager
    async def _serialized(self, locator: str) -> AsyncGenerator[None]:
        """按 locator 串行内容写入; 无人在用时回收条目, 长跑的进程不积累锁."""
        async with self._guards_lock:
            entry = self._guards.get(locator)
            if entry is None:
                entry = _LockEntry(asyncio.Lock())
                self._guards[locator] = entry
            entry.waiters += 1
        try:
            async with entry.lock:
                yield
        finally:
            async with self._guards_lock:
                entry.waiters -= 1
                if entry.waiters == 0:
                    del self._guards[locator]

    def _compute_path(self, url: str, ext: str | None = None) -> Path:
        h = _url_hash(url)
        suffix = ext if ext is not None else _guess_ext(url)
        if suffix and not suffix.startswith("."):
            suffix = f".{suffix}"
        return self._base_dir / h[:2] / f"{h}{suffix}"

    def _relative_path(self, full_path: Path) -> str:
        # 必须用 POSIX 分隔符; 否则 Windows 反斜杠路径与 LIKE '{h[:2]}/{h}.%' 匹配不到.
        return full_path.relative_to(self._base_dir).as_posix()

    def full_path(self, resource: Resource) -> Path:
        return self._base_dir / resource.file_path

    @property
    def data_dir(self) -> Path:
        return self._base_dir.parent

    async def resolve(self, url: str) -> Path | None:
        async with self._session() as session:
            stmt = select(Resource).where(Resource.url == url)
            result = await session.exec(stmt)
            record = result.first()
            if record is None:
                return None

            full_path = self._base_dir / record.file_path
            if not full_path.exists():
                # 文件丢失则删除记录.
                logger.warning("resource file missing, invalidating record", url=url, path=str(full_path))
                await session.delete(record)
                await session.commit()
                return None

            return full_path

    async def acquire(self, url: str, client: WebClient) -> Path | None:
        # 内部 URL 是对既有 Resource 的引用: 直接解析本地文件, 不下载也不写记录.
        internal = internal_url_hash(url)
        if internal is not None:
            found = await self.get_by_url_hash(internal)
            return found[1] if found else None

        # 同一 URL 的内容写入串行: 一起到达的调用都判缓存未命中, 若各自下载并改名到同一路径,
        # 后写会覆盖先写 (含先写方刚做完的就地超分), 而先写方的记录仍然作数.
        async with self._serialized(url):
            return await self._acquire_locked(url, client)

    async def _acquire_locked(self, url: str, client: WebClient) -> Path | None:
        # 缓存命中且文件存在则直出. 判据是记录 + 文件存在, 不看哈希: 超分是就地覆盖, 哈希每次都会变.
        cached = await self.resolve(url)
        if cached:
            return cached

        # 上游改派占位图时跳过, 不占用 dest 也不写记录.
        final_url = await client.resolve_final_url(url)
        if _is_placeholder_url(final_url):
            logger.warning("resource is upstream placeholder", url=url, final_url=final_url)
            return None

        dest = self._compute_path(url)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # 下载. 失败可能是站点拦下指纹 (403/406, 见 net/http.py 的轮换), 也可能是上游本来就没有图;
        # 两种失败都不写资源记录, 这里给出资源层的告警, 与上游的请求级日志区分开.
        #
        # 临时文件按调用命名, 分块下载失败留下的残片只落在其中; 成功后原子改名到 dest, 失败只清理
        # 临时文件, 不触碰 dest (dest 只由 URL 决定, 而这里已按 URL 串行, 改名不会被并发写覆盖).
        # 临时文件保留原扩展名: download 的实现可能按扩展名决定写出的格式.
        tmp = dest.with_name(f"{dest.stem}.{uuid4().hex}.part{dest.suffix}")
        try:
            if not await client.download(url, tmp):
                logger.warning("resource download failed", url=url, host=urlsplit(url).hostname)
                return None
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)

        size = dest.stat().st_size
        mime = mimetypes.guess_type(str(dest))[0]
        content_hash = self._hash_file(dest)

        # 写入记录.
        async with self._session() as session:
            record = Resource(
                url=url,
                file_path=self._relative_path(dest),
                content_hash=content_hash,
                size=size,
                mime_type=mime,
            )
            session.add(record)
            await session.commit()

        return dest

    async def resolve_source(self, url: str, client: WebClient) -> ResolvedSource | None:
        """处理入口的源解析: 内部 URL 定位既有 Resource, 外部仅接受 http(s), 其余返回 None."""
        internal = internal_url_hash(url)
        if internal is not None:
            found = await self.get_by_url_hash(internal)
            if found is None:
                return None
            return ResolvedSource(locator=found[0].url, path=found[1])

        if not url.startswith(("http://", "https://")):
            return None
        path = await self.acquire(url, client)
        if path is None:
            return None
        return ResolvedSource(locator=url, path=path)

    async def get_by_url(self, url: str) -> Resource | None:
        async with self._session() as session:
            result = await session.exec(select(Resource).where(Resource.url == url))
            return result.first()

    async def list_all(self) -> list[Resource]:
        async with self._session() as session:
            result = await session.exec(select(Resource))
            return list(result.all())

    async def get_by_url_hash(self, url_hash: str) -> tuple[Resource, Path] | None:
        # 文件名即 _url_hash(url); 按前缀匹配定位, 无需在 DB 存 hash 列.
        async with self._session() as session:
            pattern = f"{url_hash[:2]}/{url_hash}.%"
            result = await session.exec(select(Resource).where(col(Resource.file_path).like(pattern)))
            record = result.first()
            if record is None:
                return None
            full_path = self._base_dir / record.file_path
            if not full_path.exists():
                return None
            return record, full_path

    @staticmethod
    def url_hash(url: str) -> str:
        return _url_hash(url)

    async def acquire_derived(
        self,
        src_url: str,
        op: str,
        args: str,
        producer: Callable[[Path], Awaitable[bool]],
        *,
        ext: str = ".jpg",
    ) -> Resource | None:
        # 同 (src_url, op, args) 命中已有记录且磁盘仍是记录的那份字节则直出. producer 失败返回 None.
        locator = derived_locator(src_url, op, args)
        dest = self._compute_path(locator, ext=ext)
        async with self._serialized(locator):
            existing = await self.get_by_url(locator)
            if existing is not None and self._matches_record(self.full_path(existing), existing):
                return existing

            dest.parent.mkdir(parents=True, exist_ok=True)
            ok = await producer(dest)
            if not ok or not dest.exists():
                logger.warning("derived producer failed", src=src_url, op=op, args=args)
                return None

            size = dest.stat().st_size
            mime = mimetypes.guess_type(str(dest))[0]
            content_hash = self._hash_file(dest)
            meta = {"op": op, "src": src_url, "args": args}

            async with self._session() as session:
                if existing is not None:
                    existing.file_path = self._relative_path(dest)
                    existing.content_hash = content_hash
                    existing.size = size
                    existing.mime_type = mime
                    existing.meta = meta
                    session.add(existing)
                    await session.commit()
                    return existing
                record = Resource(
                    url=locator,
                    file_path=self._relative_path(dest),
                    content_hash=content_hash,
                    size=size,
                    mime_type=mime,
                    meta=meta,
                )
                session.add(record)
                await session.commit()
                return record

    async def upscale_in_place(
        self,
        resource: Resource,
        sr_args: dict,
        producer: Callable[[Path, Path], Awaitable[bool]],
    ) -> UpscaleOutcome:
        """``sr`` 标记的判据是该标记所指的那份字节仍在盘上; 磁盘已被换掉时按未超分处理, 重跑后重新
        记录 — 标记与内容因此不会永久不一致.
        """
        async with self._serialized(resource.url):
            # 调用方的 Resource 可能已过期 (并发调用刚改过), 标记与哈希一律按锁内重读的行判定.
            async with self._session() as session:
                result = await session.exec(select(Resource).where(Resource.url == resource.url))
                record = result.first()
            if record is None:
                return UpscaleOutcome.SKIPPED
            src = self._base_dir / record.file_path
            if not src.exists():
                logger.warning("upscale source missing", url=record.url, path=str(src))
                return UpscaleOutcome.SKIPPED

            # 临时文件按调用命名: 同一路径的并发超分不共用半截文件.
            tmp = src.with_name(f"{src.stem}.{uuid4().hex}.sr_tmp{src.suffix}")
            try:
                if record.meta and "sr" in record.meta and self._matches_record(src, record):
                    return UpscaleOutcome.SKIPPED
                ok = await producer(src, tmp)
                if not ok or not tmp.exists():
                    logger.warning("upscale producer failed", url=record.url)
                    return UpscaleOutcome.FAILED
                tmp.replace(src)
            except Exception as exc:
                logger.warning("upscale in place error", url=record.url, error=str(exc))
                return UpscaleOutcome.FAILED
            finally:
                tmp.unlink(missing_ok=True)

            # 原地替换后重新量取, 记入的就是盘上那份.
            size = src.stat().st_size
            content_hash = self._hash_file(src)
            new_meta = dict(record.meta) if record.meta else {}
            new_meta["sr"] = sr_args

            async with self._session() as session:
                result = await session.exec(select(Resource).where(Resource.url == record.url))
                row = result.first()
                if row is None:
                    return UpscaleOutcome.SKIPPED
                row.size = size
                row.content_hash = content_hash
                row.meta = new_meta
                session.add(row)
                await session.commit()
            return UpscaleOutcome.UPSCALED

    async def acquire_first(self, urls: list[str], client: WebClient) -> AcquireResult:
        if not urls:
            return AcquireResult(success=False, failed=[])

        # 逐 URL 尝试, 成功即停.
        failed: list[str] = []
        for url in urls:
            path = await self.acquire(url, client)
            if path:
                return AcquireResult(success=True, path=path, used_url=url, failed=failed)
            failed.append(url)
            logger.debug("download source failed, trying next", url=url)

        logger.warning("all download sources exhausted", urls_tried=len(urls))
        return AcquireResult(success=False, path=None, used_url=None, failed=failed)

    async def acquire_extrafanart(
        self,
        urls_by_site: dict[str, list[str]],
        priority: list[str],
        client: WebClient,
    ) -> list[Path]:
        for site in priority:
            if site not in urls_by_site:
                continue
            results = await asyncio.gather(*[self.acquire(u, client) for u in urls_by_site[site]])
            paths = [p for p in results if p is not None]
            if paths:
                return paths
        return []

    async def invalidate(self, url: str) -> None:
        async with self._session() as session:
            stmt = select(Resource).where(Resource.url == url)
            result = await session.exec(stmt)
            record = result.first()
            if record is None:
                return

            full_path = self._base_dir / record.file_path
            if full_path.exists():
                full_path.unlink()

            await session.delete(record)
            await session.commit()

    async def purge_unreferenced(self, live_urls: set[str], live_hashes: set[str]) -> int:
        # 存活: url 在 live_urls, 或 url_hash 在 live_hashes (内部相对 URL /api/resources/{hash}).
        removed = 0
        for resource in await self.list_all():
            if resource.url in live_urls or _url_hash(resource.url) in live_hashes:
                continue
            await self.invalidate(resource.url)
            removed += 1
        return removed

    @staticmethod
    def _hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _matches_record(self, path: Path, record: Resource) -> bool:
        """路径上的字节是否仍是 ``record`` 记的那份."""
        if record.content_hash is None or record.size is None or not path.exists():
            return False
        if path.stat().st_size != record.size:
            return False
        return self._hash_file(path) == record.content_hash
