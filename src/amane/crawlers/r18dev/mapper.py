"""dump 中图片 URL 无域名、无扩展名, 映射时补全为 DMM 绝对 URL.

指定 JP/EN 时仅取对应语言, 不允许回退; 未指定则日文优先再回退英文.
有高清副本的分类 (mono 与 digital 视频) 产出双候选 (aws 高清 → pics 标准); gallery 只用 pics, 不生成 aws 候选.
"""

import re
from typing import TYPE_CHECKING

from ...enums import ActorGender, Language
from ..models import FilmActor, MediaMetadata
from ..sites.dmm_cdn import PICS_CDN, image_candidates

if TYPE_CHECKING:
    from .models import R18VideoDetail


_GALLERY_NUM_RE = re.compile(r"-(\d+)$")


def _parse_gallery_num(path: str) -> int | None:
    m = _GALLERY_NUM_RE.search(path)
    return int(m.group(1)) if m else None


def _generate_gallery(first: str | None, last: str | None) -> list[str]:
    # dump 只存首尾路径; last < first (含 -0) 时仅保留 first. 不生成 AWS 双候选.
    if not first:
        return []

    first_num = _parse_gallery_num(first)
    last_num = _parse_gallery_num(last) if last else None

    if first_num is None:
        return [f"{PICS_CDN}/{first}.jpg"]

    if last_num is None or last_num < first_num:
        return [f"{PICS_CDN}/{first}.jpg"]

    base = _GALLERY_NUM_RE.sub("", first)
    return [f"{PICS_CDN}/{base}-{n}.jpg" for n in range(first_num, last_num + 1)]


def to_metadata(detail: R18VideoDetail, number: str, language: Language | None = None) -> MediaMetadata:
    # number 用调用方传入的规范化番号, 不用 content_id.
    v = detail.video

    # 标题与简介按语言选取.
    if language is Language.JP:
        title = v.title_ja
        plot = v.comment_ja
    elif language is Language.EN:
        title = v.title_en
        plot = v.comment_en
    else:
        title = v.title_ja or v.title_en
        plot = v.comment_ja or v.comment_en

    actors = [
        *[FilmActor(name=n, gender=ActorGender.FEMALE) for p in detail.actresses if (n := p.best(language))],
        *[FilmActor(name=n, gender=ActorGender.MALE) for p in detail.actors if (n := p.best(language))],
    ]
    directors = [n for p in detail.directors if (n := p.best(language))]
    tags = [n for c in detail.categories if (n := c.best(language))]

    studio = detail.maker.best(language) if detail.maker else None
    publisher = detail.label.best(language) if detail.label else None
    series = detail.series.best(language) if detail.series else None

    # jacket_full 作 thumb, jacket_thumb 作 poster; dump 不存域名与扩展名, 这里补全.
    poster_urls = image_candidates(f"{PICS_CDN}/{v.jacket_thumb_url}.jpg") if v.jacket_thumb_url else []
    jacket_path = v.jacket_full_url or v.jacket_thumb_url
    thumb_urls = image_candidates(f"{PICS_CDN}/{jacket_path}.jpg") if jacket_path else []

    # 剧照: gallery_full 优先, 再回退 gallery_thumb.
    extrafanart = _generate_gallery(v.gallery_full_first, v.gallery_full_last)
    if not extrafanart:
        extrafanart = _generate_gallery(v.gallery_thumb_first, v.gallery_thumb_last)

    release = v.release_date.isoformat() if v.release_date else None
    score = None

    return MediaMetadata(
        number=number,
        title=title or None,
        actors=actors,
        studio=studio,
        publisher=publisher,
        release=release,
        runtime=v.runtime_mins,
        tags=tags,
        series=series,
        plot=plot or None,
        poster_urls=poster_urls,
        thumb_urls=thumb_urls,
        trailer_urls=[detail.trailer_url] if detail.trailer_url else [],
        score=score,
        external_id=v.content_id,
        source_url=None,
        directors=directors,
        extrafanart=extrafanart,
    )
