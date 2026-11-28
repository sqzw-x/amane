"""刮削期按 ``download_resources`` 下载 / 裁剪 / 就地超分, 返回应写入 metadata 的 URL.

与整理到媒体库路径无关. 任一步失败不抛, 仅降级.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..config import DownloadableResource
from ..parsing import ContentType
from ..sr import get_preset_meta, run_SR
from .images import (
    CropRotation,
    allows_poster_crop,
    crop_box,
    crop_poster,
    format_crop_box_args,
    needs_upscale,
    probe_oriented_size,
    probe_size,
    rotated_size,
    should_crop_poster,
    validate_crop_box,
)
from .resource_store import RESOURCE_URL_PREFIX, unscaled_size

if TYPE_CHECKING:
    from ..config import HotSettings, SrConfig
    from ..db.models import Resource
    from ..net.http import WebClient
    from .resource_store import ResourceStore

logger = structlog.get_logger()


@dataclass
class MaterializedImages:
    # fanart 不是 metadata 字段 (整理时按 JAV 约定取 thumb), 不在此返回.

    poster_urls: list[str]
    thumb_urls: list[str]
    trailer_urls: list[str]


def _internal_url(store: ResourceStore, locator_url: str) -> str:
    return f"{RESOURCE_URL_PREFIX}/{store.url_hash(locator_url)}"


def _winner_first(urls: list[str], winner: str | None) -> list[str]:
    """成功者前置, 其余候选 (下载失败与未尝试的) 保序留在尾部, 供来源恢复后重试."""
    return ([winner] if winner else []) + [u for u in urls if u != winner]


async def _acquire_first(urls: list[str], store: ResourceStore, client: WebClient) -> tuple[str | None, Path | None]:
    """按序尝试候选, 命中即停. 候选是同一张图的回退链, 逐个下载只会把它在 Resource 里存成多份."""
    for url in urls:
        local = await store.acquire(url, client)
        if local:
            return url, local
    return None, None


def sr_args_dict(cfg: SrConfig) -> dict:
    pm = get_preset_meta(cfg.preset)
    return {"preset": cfg.preset, "tool": pm.tool, "model": pm.model, "scale": pm.scale}


async def _maybe_upscale(
    store: ResourceStore,
    resource: Resource,
    config: HotSettings,
    data_dir: Path,
) -> None:
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


async def _sizes_unscaled(store: ResourceStore, locator: str | None, local: Path | None) -> tuple[int, int] | None:
    """取判裁切用的尺寸: 超分过的资源按记下的原始尺寸, 否则当场量盘上那份."""
    if locator is None or local is None:
        return None
    resource = await store.get_by_url(locator)
    if resource is None:
        return probe_size(local)
    return unscaled_size(resource, local)


async def _tall_poster_candidate(
    urls: list[str],
    store: ResourceStore,
    client: WebClient,
    *,
    thumb_height: int,
    skip_ratio: float,
    current: tuple[str | None, tuple[int, int] | None],
) -> tuple[str | None, tuple[int, int] | None]:
    """首位候选不够高时按优先级继续找, 命中即停, 返回选中的候选与尺寸.

    判据与 ``should_crop_poster`` 同一口径: 候选高度相对封面高度达到 ``skip_ratio`` 就不必裁切.
    返回 ``(url, size)``; 全部偏矮时返回原本的 ``current``, 由调用方裁切.
    """
    winner, size = current
    if thumb_height <= 0:
        return winner, size
    if size is not None and size[1] / thumb_height >= skip_ratio:
        return winner, size
    # winner 为 None 时 _acquire_first 已经把整张表试过一遍, 再走一遍只会让死链的请求数翻倍.
    if winner is None:
        return winner, size
    for url in urls[urls.index(winner) + 1 :]:
        local = await store.acquire(url, client)
        if local is None:
            continue
        candidate = await _sizes_unscaled(store, url, local)
        if candidate is not None and candidate[1] / thumb_height >= skip_ratio:
            return url, candidate
    return winner, size


async def materialize_images(
    poster_urls: list[str],
    thumb_urls: list[str],
    trailer_urls: list[str],
    store: ResourceStore,
    client: WebClient,
    config: HotSettings,
    data_dir: Path,
    *,
    content_type: ContentType,
    extrafanart_urls: dict[str, list[str]] | None = None,
) -> MaterializedImages:
    """仅下载集合内的类型; 未选中的保留聚合 URL.

    候选按序尝试, 命中即停; 未命中的候选保序留在尾部, 供来源恢复后重试.
    extrafanart 仅下载到 Resource, 站点分组结构不重排.
    海报裁切只对 ``allows_poster_crop`` 放行的类型执行.
    """
    kinds = set(config.scraping.download_resources)
    crop_enabled = config.scraping.crop_poster and allows_poster_crop(content_type)

    # 下载 thumb. 裁切判据用超分前的尺寸: 超分就地放大盘上字节, 拿放大后的高度比会把来源海报一概判矮.
    want_thumb = DownloadableResource.thumb in kinds
    thumb_winner: str | None = None
    thumb_src: str | None = None
    thumb_local: Path | None = None
    if want_thumb:
        thumb_src, thumb_local = await _acquire_first(thumb_urls, store, client)
        thumb_winner = thumb_src
    # 裁剪需要 thumb 本地文件; 未选 thumb 下载时只为裁切临时 acquire. 判据要 poster 一起参与, 否则这一步
    # 下载的 thumb 没有任何消费者.
    if thumb_local is None and thumb_urls and crop_enabled and DownloadableResource.poster in kinds:
        thumb_src, thumb_local = await _acquire_first(thumb_urls, store, client)
    thumb_size = await _sizes_unscaled(store, thumb_src, thumb_local)

    # 下载 poster; 候选偏矮则从 thumb 裁剪.
    poster_winner: str | None = None
    poster_candidate_local: Path | None = None
    result_poster_urls = list(poster_urls)
    cand_size: tuple[int, int] | None = None
    if DownloadableResource.poster in kinds:
        poster_winner, poster_candidate_local = await _acquire_first(poster_urls, store, client)
        cand_size = await _sizes_unscaled(store, poster_winner, poster_candidate_local)
        # 首位偏矮时按优先级继续找够高的候选: 字段优先级只决定尝试顺序, 不保证首位就是这批里最高的.
        if crop_enabled and thumb_size is not None:
            poster_winner, cand_size = await _tall_poster_candidate(
                poster_urls,
                store,
                client,
                thumb_height=thumb_size[1],
                skip_ratio=config.scraping.poster_crop_skip_ratio,
                current=(poster_winner, cand_size),
            )

    if thumb_winner:
        thumb_res = await store.get_by_url(thumb_winner)
        if thumb_res:
            await _maybe_upscale(store, thumb_res, config, data_dir)

    if DownloadableResource.poster in kinds:
        if (
            crop_enabled
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
                result_poster_urls = _winner_first(poster_urls, poster_winner)
        else:
            result_poster_urls = _winner_first(poster_urls, poster_winner)
            if poster_winner:
                res = await store.get_by_url(poster_winner)
                if res:
                    await _maybe_upscale(store, res, config, data_dir)

    # 下载预告片.
    trailer_winner: str | None = None
    if DownloadableResource.trailer in kinds:
        trailer_winner, _ = await _acquire_first(trailer_urls, store, client)

    # 下载剧照: 按站点优先级, 有结果即停.
    if DownloadableResource.extrafanart in kinds and extrafanart_urls:
        priority = list(extrafanart_urls.keys())
        await store.acquire_extrafanart(extrafanart_urls, priority, client)

    out = MaterializedImages(
        poster_urls=result_poster_urls,
        thumb_urls=_winner_first(thumb_urls, thumb_winner),
        trailer_urls=_winner_first(trailer_urls, trailer_winner),
    )
    logger.debug(
        "images materialized",
        kinds=sorted(kinds),
        poster=out.poster_urls[:1],
        thumb=out.thumb_urls[:1],
        trailer=out.trailer_urls[:1],
    )
    return out


async def manual_crop_image(
    source_url: str,
    box: tuple[int, int, int, int],
    store: ResourceStore,
    client: WebClient,
    config: HotSettings,
    data_dir: Path,
    *,
    subject: str,
    rotation: CropRotation = 0,
) -> str:
    """手动裁切源图, 返回内部 URL. 源可为外部 http(s) 或 ``/api/resources/{hash}``.

    先按 EXIF 转正、再顺时针旋转 ``rotation``, 之后按 ``box`` 裁切; 失败抛 ``ValueError``
    (消息可直接作 API detail); ``subject`` 组装提示文案 (如「封面图」「头像」).
    """
    source = await store.resolve_source(source_url, client)
    if source is None:
        raise ValueError(f"无法获取{subject}")

    size = probe_oriented_size(source.path)
    if size is None:
        raise ValueError(f"{subject}无法读取")
    if not validate_crop_box(box, rotated_size(size, rotation)):
        raise ValueError("裁切区域无效")

    args = format_crop_box_args(*box, rotation=rotation)
    jpeg_quality = config.scraping.jpeg_quality

    async def producer(dest: Path) -> bool:
        return crop_box(source.path, dest, box, rotation=rotation, jpeg_quality=jpeg_quality)

    crop_res = await store.acquire_derived(source.locator, "crop", args, producer)
    if crop_res is None:
        raise ValueError("裁切失败")

    await _maybe_upscale(store, crop_res, config, data_dir)
    return _internal_url(store, crop_res.url)
