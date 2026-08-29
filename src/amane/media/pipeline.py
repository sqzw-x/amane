"""刮削期资源物化管线 - 按配置下载到 Resource / 裁剪 / 就地超分, 返回各 role 最终 URL.

- 外部原始图: metadata 记外部 URL; 经 store 下载到 Resource 目录供本地消费与超分.
- 裁剪 (外部不存在): 经 store 派生, metadata 记内部相对 URL `/api/resources/{hash}`.
- 就地超分 (sr.enabled 时急切): 覆盖资源本地文件, URL 不变, meta 打 'sr' 标记.
- 视频 (trailer): 仅下载到 Resource, 不超分; metadata 记外部 URL.
- ``scraping.download_resources`` 控制本步下载哪些类型; 与整理到媒体库路径无关.

返回 MaterializedImages - 各 role 最终应写入 metadata 的 URL.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from ..config import DownloadableResource
from ..sr import get_preset_meta, run_SR
from .images import (
    crop_box,
    crop_poster,
    format_crop_box_args,
    needs_upscale,
    probe_size,
    should_crop_poster,
    validate_crop_box,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import HotSettings, SrConfig
    from ..db.models import Resource
    from ..net.http import WebClient
    from .resource_store import ResourceStore

logger = structlog.get_logger()

# 内部派生资源的相对 URL 前缀 (serve 哑文件服务挂载点).
RESOURCE_URL_PREFIX = "/api/resources"


@dataclass
class MaterializedImages:
    """物化后应写入 metadata 的 URL 列表 (取代聚合产出的对应字段).

    - poster_urls: 裁剪时 = [内部相对 URL]; 否则 = 外部候选列表, 下载成功者前置.
    - thumb_urls / trailer_urls: 外部候选列表, 下载成功者前置 (仅下载到 Resource + 就地超分, URL 不变).
     fanart 不是 metadata 字段 (整理时按 JAV 约定取 thumb), 故不在此返回.
    """

    poster_urls: list[str]
    thumb_urls: list[str]
    trailer_urls: list[str]


def _internal_url(store: ResourceStore, locator_url: str) -> str:
    """派生资源 locator → 前端可访问的内部相对 URL."""
    return f"{RESOURCE_URL_PREFIX}/{store.url_hash(locator_url)}"


def _success_first(urls: list[str], succeeded: set[str]) -> list[str]:
    """按下载结果稳定分区: 成功者保序前置, 失败者保序沉底.

    ``succeeded`` 为空 (类型未被下载) 时幂等返回原序.
    """
    return [u for u in urls if u in succeeded] + [u for u in urls if u not in succeeded]


def sr_args_dict(cfg: SrConfig) -> dict:
    """SrConfig → 规范化 sr 参数 (写入 Resource.meta, 也用于 locator 区分)."""
    pm = get_preset_meta(cfg.preset)
    return {"preset": cfg.preset, "tool": pm.tool, "model": pm.model, "scale": pm.scale}


async def _maybe_upscale(
    store: ResourceStore,
    resource: Resource,
    config: HotSettings,
    data_dir: Path,
) -> None:
    """对资源就地超分 (若启用且达低质阈值且未超分). 失败/跳过均静默."""
    if not config.sr.enabled:
        return
    full = store.full_path(resource)
    size = probe_size(full)
    file_bytes = full.stat().st_size if full.exists() else 0
    if not needs_upscale(
        size,
        file_bytes,
        max_dim_threshold=config.sr.max_dim_threshold,
        max_bytes_threshold=config.sr.max_bytes_threshold,
    ):
        return

    async def producer(src: Path, out: Path) -> bool:
        result = await run_SR(src, out, config.sr, data_dir)
        return result.success

    await store.upscale_in_place(resource, sr_args_dict(config.sr), producer)


async def materialize_images(
    poster_urls: list[str],
    thumb_urls: list[str],
    trailer_urls: list[str],
    store: ResourceStore,
    client: WebClient,
    config: HotSettings,
    data_dir: Path,
    *,
    extrafanart_urls: dict[str, list[str]] | None = None,
) -> MaterializedImages:
    """按 ``download_resources`` 下载到 Resource + 裁剪 + (急切) 超分, 返回应写入 metadata 的 URL.

    - 仅下载集合内的类型; 未选中的类型保留聚合 URL, 不写入 Resource.
    - 下载的 URL 列表按本次成功 (含缓存命中) 重排: 成功者保序前置, 失败保序沉底 —
      死 URL (来源站失效) 不再长期占据首位, 但也保留在尾部供来源恢复后重试.
    - thumb/trailer: metadata 保留外部候选列表 (重排后); 下载 + 就地超分首个成功源.
    - poster: 候选偏矮则从 thumb 裁剪 → 列表替换为 [内部派生 URL]; 否则保留外部列表 (重排后).
    - extrafanart: 仅下载到 Resource, URL 仍由调用方原样写入 metadata (站点分组结构, 不重排).
     机会主义: 任一步失败不抛, 仅降级. 不阻断刮削主流程.
    """
    kinds = set(config.scraping.download_resources)

    thumb_ok: set[str] = set()
    thumb_local: Path | None = None
    thumb_src: str | None = None
    if DownloadableResource.thumb in kinds:
        for url in thumb_urls:
            local = await store.acquire(url, client)
            if local:
                thumb_ok.add(url)
                if thumb_local is None:
                    thumb_local, thumb_src = local, url
                    res = await store.get_by_url(url)
                    if res:
                        await _maybe_upscale(store, res, config, data_dir)

    poster_ok: set[str] = set()
    poster_candidate_local: Path | None = None
    result_poster_urls = list(poster_urls)
    if DownloadableResource.poster in kinds:
        for url in poster_urls:
            local = await store.acquire(url, client)
            if local:
                poster_ok.add(url)
                if poster_candidate_local is None:
                    poster_candidate_local = local

        # 裁剪需要 thumb 本地文件: 若未选 thumb 下载, 为裁剪临时 acquire 首个 thumb.
        if thumb_local is None and thumb_urls and config.scraping.crop_poster:
            for url in thumb_urls:
                local = await store.acquire(url, client)
                if local:
                    thumb_local, thumb_src = local, url
                    break

        thumb_size = probe_size(thumb_local) if thumb_local else None
        cand_size = probe_size(poster_candidate_local) if poster_candidate_local else None

        if (
            config.scraping.crop_poster
            and should_crop_poster(thumb_size, cand_size, skip_ratio=config.scraping.poster_crop_skip_ratio)
            and thumb_local
        ):
            ratio = config.scraping.poster_ratio
            args = f"{ratio:.4f}"
            local_thumb = thumb_local

            async def crop_producer(dest: Path) -> bool:
                return crop_poster(local_thumb, dest, poster_ratio=ratio, jpeg_quality=config.scraping.jpeg_quality)

            crop_res = await store.acquire_derived(thumb_src or "", "crop", args, crop_producer)
            if crop_res:
                await _maybe_upscale(store, crop_res, config, data_dir)
                result_poster_urls = [_internal_url(store, crop_res.url)]
            else:
                result_poster_urls = _success_first(poster_urls, poster_ok)
        else:
            result_poster_urls = _success_first(poster_urls, poster_ok)
            if poster_candidate_local:
                for url in poster_urls:
                    res = await store.get_by_url(url)
                    if res:
                        await _maybe_upscale(store, res, config, data_dir)
                        break

    trailer_ok: set[str] = set()
    if DownloadableResource.trailer in kinds:
        for url in trailer_urls:
            local = await store.acquire(url, client)
            if local:
                trailer_ok.add(url)

    if DownloadableResource.extrafanart in kinds and extrafanart_urls:
        priority = list(extrafanart_urls.keys())
        await store.acquire_extrafanart(extrafanart_urls, priority, client)

    out = MaterializedImages(
        poster_urls=result_poster_urls,
        thumb_urls=_success_first(thumb_urls, thumb_ok),
        trailer_urls=_success_first(trailer_urls, trailer_ok),
    )
    logger.debug(
        "images materialized",
        kinds=sorted(kinds),
        poster=out.poster_urls[:1],
        thumb=out.thumb_urls[:1],
        trailer=out.trailer_urls[:1],
    )
    return out


async def manual_crop_poster(
    thumb_url: str,
    box: tuple[int, int, int, int],
    store: ResourceStore,
    client: WebClient,
    config: HotSettings,
    data_dir: Path,
) -> str:
    """从已有 thumb URL 按像素框裁切海报, 返回内部相对 URL.

    失败抛 ``ValueError`` (消息可直接作 API detail).
    """
    local = await store.acquire(thumb_url, client)
    if local is None:
        raise ValueError("无法获取封面图")

    size = probe_size(local)
    if size is None:
        raise ValueError("封面图无法读取")
    if not validate_crop_box(box, size):
        raise ValueError("裁切区域无效")

    args = format_crop_box_args(*box)
    jpeg_quality = config.scraping.jpeg_quality

    async def producer(dest: Path) -> bool:
        return crop_box(local, dest, box, jpeg_quality=jpeg_quality)

    crop_res = await store.acquire_derived(thumb_url, "crop", args, producer)
    if crop_res is None:
        raise ValueError("裁切失败")

    await _maybe_upscale(store, crop_res, config, data_dir)
    return _internal_url(store, crop_res.url)
