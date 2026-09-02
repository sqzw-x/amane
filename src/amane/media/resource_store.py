"""
资源存储层 - URL 级下载缓存 + 派生资源 (裁剪) + 就地超分.

核心能力:
- 同一 URL 不重复下载; 多源 fallback: 逐 URL 尝试直到成功.
- 文件以 URL hash 命名, 两位前缀分级目录.
- 派生资源 (裁剪): url = 合成 locator `derived:{sha256(src)}:{op}:{args}`, 经 `meta` 记录可逆来源.
- 就地超分: SR 覆盖原资源文件, 不新建 URL, 在 `meta` 打 'sr' 标记 (URL 不变 → metadata 静态).
- 一等存储: 未被 Metadata 引用的条目由 CLEANUP 回收; 不做 LRU.
"""

import asyncio
import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import Resource

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

    from ..net.http import WebClient

logger = structlog.get_logger()


def _url_hash(url: str) -> str:
    """从 URL 生成 16 字符 hex hash (64-bit, 碰撞概率极低)."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def derived_locator(src_url: str, op: str, args: str) -> str:
    """派生资源的合成 locator (两层 hash 的可寻址表达).

    inner = sha256(src_url), 外层 = `derived:{inner}:{op}:{args}`.
    同一 (src_url, op, args) 恒得同一 locator → 派生文件确定可定位, 无需反推 url.
    """
    inner = hashlib.sha256(src_url.encode()).hexdigest()
    return f"derived:{inner}:{op}:{args}"


def _guess_ext(url: str) -> str:
    """从 URL 猜测文件扩展名, 默认 .bin."""
    # 去掉 query string
    path = url.split("?")[0]
    ext = Path(path).suffix.lower()
    if ext and len(ext) <= 5:
        return ext
    return ".bin"


@dataclass
class AcquireResult:
    """acquire_first() 的返回结果."""

    success: bool
    path: Path | None = None
    used_url: str | None = None
    failed: list[str] | None = None


class ResourceStore:
    """
    URL 级资源缓存层.

    文件布局: {base_dir}/{hash[:2]}/{hash}.{ext}
    数据库记录: resources 表, 按 URL 唯一索引.
    """

    def __init__(self, engine: AsyncEngine, base_dir: Path):
        self._engine = engine
        self._base_dir = base_dir
        base_dir.mkdir(parents=True, exist_ok=True)

    def _session(self) -> AsyncSession:
        return AsyncSession(self._engine, expire_on_commit=False)

    def _compute_path(self, url: str, ext: str | None = None) -> Path:
        """根据 URL 计算本地存储路径. ext 显式给定时覆盖从 URL 猜测的扩展名 (派生资源用)."""
        h = _url_hash(url)
        suffix = ext if ext is not None else _guess_ext(url)
        if suffix and not suffix.startswith("."):
            suffix = f".{suffix}"
        return self._base_dir / h[:2] / f"{h}{suffix}"

    def _relative_path(self, full_path: Path) -> str:
        """转为相对于 base_dir 的路径字符串.

        统一 POSIX 分隔符 (as_posix), 与给 get_by_url_hash 的 LIKE 前缀模式
        '{h[:2]}/{h}.%' 保持一致; 否则 Windows 上存入反斜杠路径, 前缀查询匹配不到.
        """
        return full_path.relative_to(self._base_dir).as_posix()

    def full_path(self, resource: Resource) -> Path:
        """Resource 记录 → 本地绝对文件路径."""
        return self._base_dir / resource.file_path

    @property
    def data_dir(self) -> Path:
        """应用数据目录 (resources 目录的父级). 供 SR 二进制缓存等使用."""
        return self._base_dir.parent

    async def resolve(self, url: str) -> Path | None:
        """查缓存, 命中且文件存在则返回路径."""
        async with self._session() as session:
            stmt = select(Resource).where(Resource.url == url)
            result = await session.exec(stmt)
            record = result.first()
            if record is None:
                return None

            full_path = self._base_dir / record.file_path
            if not full_path.exists():
                # 文件丢失, 清除记录
                logger.warning("resource file missing, invalidating record", url=url, path=str(full_path))
                await session.delete(record)
                await session.commit()
                return None

            return full_path

    async def acquire(self, url: str, client: WebClient) -> Path | None:
        """下载 (如未缓存) 并返回本地路径. 失败返回 None."""
        # 先查缓存
        cached = await self.resolve(url)
        if cached:
            return cached

        # 下载到目标位置
        dest = self._compute_path(url)
        dest.parent.mkdir(parents=True, exist_ok=True)

        ok = await client.download(url, dest)
        if not ok:
            return None

        # 记录到数据库
        size = dest.stat().st_size if dest.exists() else None
        mime = mimetypes.guess_type(str(dest))[0]
        content_hash = self._hash_file(dest)

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

    async def get_by_url(self, url: str) -> Resource | None:
        """按 url (locator key) 取 Resource 记录."""
        async with self._session() as session:
            result = await session.exec(select(Resource).where(Resource.url == url))
            return result.first()

    async def list_all(self) -> list[Resource]:
        """列出全部 Resource 记录 (定时超分任务扫描用)."""
        async with self._session() as session:
            result = await session.exec(select(Resource))
            return list(result.all())

    async def get_by_url_hash(self, url_hash: str) -> tuple[Resource, Path] | None:
        """按 url 的 hash (serve 端点用) 取 Resource + 本地文件路径.

        serve 哑文件服务: 前端用相对 URL `/resources/{url_hash}` 请求, 后端按 hash 查表返回文件.
        文件名即 `_url_hash(url)`, 故按文件名前缀匹配可定位 (无需在 DB 存 hash 列).
        """
        async with self._session() as session:
            # file_path 形如 '{h[:2]}/{h}{ext}', 用前缀匹配定位唯一 hash
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
        """暴露 locator → 文件 hash 的映射 (供构造 serve 相对 URL)."""
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
        """获取或生成派生资源 (如裁剪). 幂等: 同 (src_url, op, args) 命中已有记录直出.

        Args:
            src_url: 源资源 url (派生从它产生).
            op: 操作名 (如 'crop').
            args: 操作参数串 (如 '0.714'), 参与 locator, 决定唯一性.
            producer: async 回调, 接收目标输出路径, 生成文件, 成功返回 True.
            ext: 输出扩展名.

        Returns:
            派生 Resource 记录; producer 失败返回 None.
        """
        locator = derived_locator(src_url, op, args)
        existing = await self.get_by_url(locator)
        if existing is not None and (self._base_dir / existing.file_path).exists():
            return existing

        dest = self._compute_path(locator, ext=ext)
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
    ) -> bool:
        """就地超分: 覆盖资源文件, URL 不变, 在 meta 打 'sr' 标记 (D2).

        producer 接收 (输入路径, 临时输出路径), 成功返回 True; 随后原子替换原文件并更新记录.
        meta 已含 'sr' 键则视为已超分, 跳过 (定时任务去重亦依赖此).

        Returns:
            True = 已超分 (本次执行); False = 跳过 (已超分/文件缺失) 或失败.
        """
        if resource.meta and "sr" in resource.meta:
            return False
        src = self._base_dir / resource.file_path
        if not src.exists():
            logger.warning("upscale source missing", url=resource.url, path=str(src))
            return False

        tmp = src.with_name(f"{src.stem}.sr_tmp{src.suffix}")
        try:
            ok = await producer(src, tmp)
            if not ok or not tmp.exists():
                logger.warning("upscale producer failed", url=resource.url)
                tmp.unlink(missing_ok=True)
                return False
            tmp.replace(src)  # 原子替换
        except Exception as e:
            logger.warning("upscale in place error", url=resource.url, error=str(e))
            tmp.unlink(missing_ok=True)
            return False

        size = src.stat().st_size
        content_hash = self._hash_file(src)
        new_meta = dict(resource.meta) if resource.meta else {}
        new_meta["sr"] = sr_args

        async with self._session() as session:
            result = await session.exec(select(Resource).where(Resource.url == resource.url))
            record = result.first()
            if record is None:
                return False
            record.size = size
            record.content_hash = content_hash
            record.meta = new_meta
            session.add(record)
            await session.commit()
        return True

    async def acquire_first(self, urls: list[str], client: WebClient) -> AcquireResult:
        """多源容错: 逐 URL 尝试, 成功即停; 全部失败返回 success=False 的 AcquireResult (failed 列出所有失败 URL)."""
        if not urls:
            return AcquireResult(success=False, failed=[])

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
        """按站点优先级下载剧照. 优先站点有结果则完成, 否则尝试下一站点."""
        for site in priority:
            if site not in urls_by_site:
                continue
            results = await asyncio.gather(*[self.acquire(u, client) for u in urls_by_site[site]])
            paths = [p for p in results if p is not None]
            if paths:
                return paths
        return []

    async def invalidate(self, url: str) -> None:
        """标记失效, 删除本地文件和 DB 记录."""
        async with self._session() as session:
            stmt = select(Resource).where(Resource.url == url)
            result = await session.exec(stmt)
            record = result.first()
            if record is None:
                return

            # 删除文件
            full_path = self._base_dir / record.file_path
            if full_path.exists():
                full_path.unlink()

            await session.delete(record)
            await session.commit()

    async def purge_unreferenced(self, live_urls: set[str], live_hashes: set[str]) -> int:
        """删除未被引用的 Resource (文件 + DB 行).

        存活判定: ``Resource.url`` 落在 ``live_urls``, 或 ``url_hash(url)`` 落在 ``live_hashes``
        (后者对应 metadata 中的内部相对 URL ``/api/resources/{hash}``).
        """
        removed = 0
        for resource in await self.list_all():
            if resource.url in live_urls or _url_hash(resource.url) in live_hashes:
                continue
            await self.invalidate(resource.url)
            removed += 1
        return removed

    @staticmethod
    def _hash_file(path: Path) -> str:
        """计算文件的 SHA-256 hash."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
