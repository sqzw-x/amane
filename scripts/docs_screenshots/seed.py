"""向全新数据目录写入文档截图用的占位数据集 (不访问网络).

所有字段都是无语义的占位值: 番号 ABC-001 / XYZ-001, 标题「标题 N」, 演员「演员 A」, 标签「标签 N」等.
图片由 Pillow 在本地生成 (渐变底 + 番号文字), 以内部 URL (`/api/resources/{hash}`) 登记为 Resource.
媒体库与文件只写数据库行, 路径沿用 Docker 约定的 /media (磁盘上不存在); 库与订阅源都不触发扫描、拉取或刮削.

单独运行: uv run python -m scripts.docs_screenshots.seed --data-dir /tmp/x
端到端 (含启动服务与截图) 见 capture.py.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath

from croniter import croniter
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from amane.agent.rows import TextDeltaRow, ToolCallRow, ToolResultRow, TraceRow, TurnUsageRow, UserMessageRow
from amane.agent.trace import SessionStore, session_dir
from amane.agent.usage import TurnTokenUsage
from amane.db.engine import create_async_engine_from_path
from amane.db.models import (
    Actor,
    FacetKind,
    MediaFileStatus,
    Resource,
    RoutineType,
    SavedQueryEntity,
    Studio,
    Tag,
    Task,
    TaskStatus,
    TaskType,
)
from amane.db.repo_types import WriteMode
from amane.db.repository import Repository
from amane.enums import ActorGender, LibraryAutomation
from amane.media.resource_store import RESOURCE_URL_PREFIX, ResourceStore
from amane.parsing.file_info import ContentType

logger = logging.getLogger("docs_screenshots.seed")

FONT_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
)

#: 占位来源键; 均不在爬虫注册表中.
SITE_MAIN = "site_a"
SITE_ALT = "site_b"
SITE_THIRD = "site_c"

PLOT = "简介占位文本"
POSTER_SIZE = (800, 1143)
THUMB_SIZE = (1280, 720)
AVATAR_SIZE = (600, 800)
COMMENT_IMAGE_SIZE = (280, 158)
#: 界面展示的媒体根目录; 所列视图只读数据库, 不要求文件存在.
MEDIA_ROOT = PurePosixPath("/media")
#: 登记到数据库的文件大小, 高于媒体库的最小视频大小.
DUMMY_VIDEO_SIZE = 1_536 * 1024 * 1024

type RGB = tuple[int, int, int]

#: 渐变底色 (上, 下); 只用于区分卡片, 不对应任何主题.
GRADIENTS: tuple[tuple[RGB, RGB], ...] = (
    ((160, 192, 232), (84, 118, 178)),
    ((186, 214, 196), (88, 136, 116)),
    ((232, 200, 174), (186, 122, 98)),
    ((204, 196, 232), (118, 104, 174)),
    ((228, 212, 164), (166, 138, 82)),
    ((200, 210, 222), (108, 122, 140)),
)


@dataclass(frozen=True)
class MovieSeed:
    index: int
    number: str
    title: str
    release: str
    runtime: int
    studio: str
    publisher: str
    series: str | None
    directors: list[str]
    actors: list[str]
    tags: list[str]
    score: float
    fanart: int


@dataclass(frozen=True)
class ActorSeed:
    index: int
    name: str
    gender: ActorGender
    birthday: str
    height: int


@dataclass
class SeedContext:
    data_dir: Path
    font_path: Path
    resources: list[Resource] = field(default_factory=list)


def _build_actors() -> tuple[ActorSeed, ...]:
    genders = (ActorGender.FEMALE, ActorGender.FEMALE, ActorGender.MALE, ActorGender.FEMALE)
    return tuple(
        ActorSeed(
            index=i,
            name=f"演员 {letter}",
            gender=genders[i % len(genders)],
            birthday=(date(1990, 1, 15) + timedelta(days=i * 400)).isoformat(),
            height=158 + (i * 5) % 24,
        )
        for i, letter in enumerate("ABCDEFGH")
    )


ACTORS = _build_actors()


def _build_movies() -> tuple[MovieSeed, ...]:
    """确定性生成: 各字段按序号轮转, 使分类与筛选里每一项都有若干条目."""
    movies: list[MovieSeed] = []
    for prefix, count in (("ABC", 16), ("XYZ", 8)):
        for n in range(1, count + 1):
            i = len(movies)
            actors = [ACTORS[i % 8].name, ACTORS[(i + 3) % 8].name]
            if i % 4 == 0:
                actors.append(ACTORS[(i + 5) % 8].name)
            movies.append(
                MovieSeed(
                    index=i,
                    number=f"{prefix}-{n:03d}",
                    title=f"标题 {i + 1}",
                    release=(date(2016, 3, 1) + timedelta(days=i * 120)).isoformat(),
                    runtime=90 + (i * 7) % 31,
                    studio=f"制作商 {i % 4 + 1}",
                    publisher=f"发行商 {i % 3 + 1}",
                    series=f"系列 {i // 2 % 3 + 1}" if i % 2 == 0 else None,
                    directors=[f"导演 {i % 3 + 1}"],
                    actors=actors,
                    tags=[f"标签 {(i + k * 3) % 8 + 1}" for k in range(3)],
                    score=round(7.5 + (i * 37) % 14 / 10, 1),
                    fanart=4 if i % 4 == 0 else 0,
                )
            )
    return tuple(movies)


MOVIES = _build_movies()
MOVIES_BY_NUMBER = {m.number: m for m in MOVIES}

#: 带多来源快照的番号, 供「对比来源」展示差异.
ALT_NUMBERS: tuple[str, ...] = ("ABC-005", "ABC-009")

#: 已挂载到媒体库的番号 (文件名, 状态); XYZ-011 / XYZ-012 无元数据.
#: 文件名后缀驱动海报墙左上角的文件标记 (中字 / 破解 / 清晰度).
LIBRARY_FILES: tuple[tuple[str, str, MediaFileStatus], ...] = (
    ("ABC-001", "ABC-001-C.mp4", MediaFileStatus.SCRAPED),
    ("ABC-003", "ABC-003-U.mp4", MediaFileStatus.SCRAPED),
    ("ABC-005", "ABC-005-C 4K.mkv", MediaFileStatus.SCRAPED),
    ("ABC-009", "ABC-009.mp4", MediaFileStatus.SCRAPED),
    ("ABC-010", "ABC-010-UC.mp4", MediaFileStatus.SCRAPED),
    ("ABC-014", "ABC-014-C.mp4", MediaFileStatus.SCRAPED),
    ("ABC-016", "ABC-016 4K.mp4", MediaFileStatus.SCRAPED),
    ("XYZ-002", "XYZ-002-C.mkv", MediaFileStatus.SCRAPED),
    ("XYZ-004", "XYZ-004.mp4", MediaFileStatus.SCRAPED),
    ("XYZ-007", "XYZ-007-U.mp4", MediaFileStatus.SCRAPED),
    ("XYZ-008", "XYZ-008-C 4K.mp4", MediaFileStatus.SCRAPED),
    ("XYZ-011", "XYZ-011.mp4", MediaFileStatus.PENDING),
    ("XYZ-012", "XYZ-012.mp4", MediaFileStatus.FAILED),
)
#: 第二个媒体库的文件, 只为媒体库列表有两行.
SECOND_LIBRARY_FILES: tuple[tuple[str, str, MediaFileStatus], ...] = (
    ("XYZ-001", "XYZ-001.mp4", MediaFileStatus.SCRAPED),
    ("XYZ-003", "XYZ-003-C.mp4", MediaFileStatus.SCRAPED),
    ("XYZ-005", "XYZ-005.mp4", MediaFileStatus.PENDING),
)
#: 带 VR 标签的番号, 海报墙右上角显示 VR 徽标.
VR_NUMBERS: frozenset[str] = frozenset({"ABC-016", "XYZ-008", "XYZ-003"})


def _alt_sources(movie: MovieSeed) -> dict[str, dict[str, object]]:
    """与主站不同的字段; 差异值同样是占位."""
    if movie.number not in ALT_NUMBERS:
        return {}
    return {
        SITE_ALT: {
            "title": f"{movie.title} (来源 B)",
            "runtime": movie.runtime + 12,
            "release": (date.fromisoformat(movie.release) + timedelta(days=7)).isoformat(),
            "tags": [*movie.tags[:2], "标签 8"],
            "publisher": "发行商 4",
            "score": round(movie.score + 0.3, 1),
        },
        SITE_THIRD: {
            "title": movie.title,
            "tags": movie.tags[:2],
            "studio": f"{movie.studio} (来源 C)",
            "score": round(movie.score - 0.4, 1),
        },
    }


def _load_font(ctx: SeedContext, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ctx.font_path), size)


def _fit_font(ctx: SeedContext, text: str, max_width: int, start: int) -> ImageFont.FreeTypeFont:
    size = start
    font = _load_font(ctx, size)
    while size > 24 and font.getlength(text) > max_width:
        size -= 4
        font = _load_font(ctx, size)
    return font


def _gradient(size: tuple[int, int], index: int) -> Image.Image:
    top, bottom = GRADIENTS[index % len(GRADIENTS)]
    height = size[1]
    column = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    return column.resize(size).convert("RGBA")


def _centered_text(image: Image.Image, text: str, font: ImageFont.FreeTypeFont, center_y: float) -> None:
    """白字加柔和阴影, 浅色渐变上也可读."""
    bbox = ImageDraw.Draw(image).textbbox((0, 0), text, font=font)
    x = (image.width - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = center_y - (bbox[3] - bbox[1]) / 2 - bbox[1]
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x + 2, y + 3), text, font=font, fill=(0, 0, 0, 90))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(4)))
    ImageDraw.Draw(image).text((x, y), text, font=font, fill=(255, 255, 255))


def _number_card(ctx: SeedContext, size: tuple[int, int], index: int, text: str, center: float) -> Image.Image:
    image = _gradient(size, index)
    font = _fit_font(ctx, text, int(size[0] * 0.78), int(size[1] * 0.16))
    # 海报墙卡片底边叠有日期徽标, 文字放在中部偏上.
    _centered_text(image, text, font, size[1] * center)
    return image.convert("RGB")


def _register(ctx: SeedContext, locator: str, image: Image.Image) -> str:
    """把图片落到 resources 目录并登记; 返回前端可直接读取的内部 URL."""
    url_hash = ResourceStore.url_hash(locator)
    relative = f"{url_hash[:2]}/{url_hash}.jpg"
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88, optimize=True)
    data = buffer.getvalue()
    target = ctx.data_dir / "resources" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    ctx.resources.append(
        Resource(
            url=locator,
            file_path=relative,
            content_hash=hashlib.sha256(data).hexdigest(),
            size=len(data),
            mime_type="image/jpeg",
        )
    )
    return f"{RESOURCE_URL_PREFIX}/{url_hash}"


@dataclass(frozen=True)
class MovieImages:
    poster: str
    thumb: str
    fanart: list[str]
    alt_poster: str | None


def render_images(ctx: SeedContext) -> tuple[dict[str, MovieImages], dict[str, str], str]:
    movie_images: dict[str, MovieImages] = {}
    for movie in MOVIES:
        i = movie.index
        poster = _register(
            ctx, f"seed://{movie.number}/poster.jpg", _number_card(ctx, POSTER_SIZE, i, movie.number, 0.42)
        )
        thumb = _register(ctx, f"seed://{movie.number}/thumb.jpg", _number_card(ctx, THUMB_SIZE, i, movie.number, 0.5))
        fanart = [
            _register(
                ctx,
                f"seed://{movie.number}/fanart-{k}.jpg",
                _number_card(ctx, THUMB_SIZE, i + k + 1, f"{movie.number} · {k + 1}", 0.5),
            )
            for k in range(movie.fanart)
        ]
        alt_poster = None
        if movie.number in ALT_NUMBERS:
            alt_poster = _register(
                ctx, f"seed://{movie.number}/poster-alt.jpg", _number_card(ctx, POSTER_SIZE, i + 3, movie.number, 0.42)
            )
        movie_images[movie.number] = MovieImages(poster, thumb, fanart, alt_poster)
    avatars = {
        actor.name: _register(
            ctx, f"seed://actor/{actor.index}.jpg", _number_card(ctx, AVATAR_SIZE, actor.index, actor.name[-1], 0.46)
        )
        for actor in ACTORS
    }
    # 评论图片按原始像素尺寸显示; 用小图, 不让它占满评论卡片.
    comment_image = _register(ctx, "seed://comment/1.jpg", _number_card(ctx, COMMENT_IMAGE_SIZE, 5, "ABC-005", 0.5))
    return movie_images, avatars, comment_image


def _site_snapshot(movie: MovieSeed, images: MovieImages) -> dict[str, object]:
    return {
        "title": movie.title,
        "actors": movie.actors,
        "studio": movie.studio,
        "publisher": movie.publisher,
        "release": movie.release,
        "runtime": movie.runtime,
        "tags": movie.tags,
        "series": movie.series,
        "plot": PLOT,
        "directors": movie.directors,
        "poster_urls": [images.poster],
        "thumb_urls": [images.thumb],
        "trailer_urls": [],
        "extrafanart": images.fanart,
        "score": movie.score,
    }


async def _seed_metadata(repo: Repository, images: dict[str, MovieImages]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for movie in MOVIES:
        movie_images = images[movie.number]
        main = _site_snapshot(movie, movie_images)
        raw: dict[str, dict[str, object]] = {SITE_MAIN: main}
        scores = {SITE_MAIN: movie.score}
        source_urls = {SITE_MAIN: f"https://site-a.example/{movie.number.lower()}"}
        for site, diff in _alt_sources(movie).items():
            snapshot = {**main, **diff}
            if movie_images.alt_poster and site == SITE_ALT:
                snapshot["poster_urls"] = [movie_images.alt_poster]
                snapshot["extrafanart"] = movie_images.fanart[:2]
            raw[site] = snapshot
            score = diff.get("score")
            if isinstance(score, float):
                scores[site] = score
            source_urls[site] = f"https://{site.replace('_', '-')}.example/{movie.number.lower()}"
        extrafanart = {SITE_MAIN: movie_images.fanart} if movie_images.fanart else {}
        record = await repo.upsert_metadata(
            movie.number,
            content_type=ContentType.CHINESE,
            title=movie.title,
            actors=movie.actors,
            studio=movie.studio,
            publisher=movie.publisher,
            release=movie.release,
            runtime=movie.runtime,
            tags=[*movie.tags, "VR"] if movie.number in VR_NUMBERS else movie.tags,
            series=movie.series,
            plot=PLOT,
            directors=movie.directors,
            poster_urls=[movie_images.poster],
            thumb_urls=[movie_images.thumb],
            extrafanart_urls=extrafanart,
            scores=scores,
            source_urls=source_urls,
            field_sources=dict.fromkeys(
                ("title", "actors", "studio", "publisher", "release", "runtime", "tags", "plot", "poster_urls"),
                SITE_MAIN,
            ),
            raw=raw,
        )
        assert record.id is not None
        ids[movie.number] = record.id
    return ids


async def _seed_actors(repo: Repository, session: AsyncSession, avatars: dict[str, str]) -> None:
    for actor in ACTORS:
        row = (await session.exec(select(Actor).where(Actor.name == actor.name))).first()
        if row is None or row.id is None:
            raise RuntimeError(f"演员未由影片写入: {actor.name}")
        await repo.update_actor(
            row.id,
            mode=WriteMode.AUTO,
            gender=actor.gender,
            birthday=actor.birthday,
            birthplace=f"地区 {actor.index % 3 + 1}",
            height=actor.height,
            overview=PLOT,
            image_urls=[avatars[actor.name]],
        )


def _comment_body(image_url: str) -> str:
    """GFM 评论: 各级标题、行内样式、有序 / 嵌套 / 任务列表、代码块、表格、引用、分隔线、内嵌图片与时间戳."""
    return "\n".join(
        (
            "## 评论标题",
            "",
            "评论占位文本 1, 含 **粗体**、*斜体*、~~删除线~~、`代码` 与 [链接](https://example.com).",
            "",
            "### 小节标题",
            "",
            "1. 12:34 片段占位文本",
            "2. 1:02:03 片段占位文本",
            "   - 子项占位文本",
            "",
            "- [x] 任务 1",
            "- [ ] 任务 2",
            "",
            "```sql",
            "SELECT number, title FROM metadata WHERE release >= '2020-01-01';",
            "```",
            "",
            "| 列 1 | 列 2 |",
            "| --- | --- |",
            "| 值 1 | 值 2 |",
            "",
            "> 引用占位文本",
            "",
            "---",
            "",
            f"![图片]({image_url})",
        )
    )


async def _seed_annotations(repo: Repository, session: AsyncSession, ids: dict[str, int], comment_image: str) -> None:
    comment_body = _comment_body(comment_image)
    user_tags, _ = await repo.ensure_user_tags(["用户标签 1", "用户标签 2", "用户标签 3"])
    tag_ids = [t.id for t in user_tags if t.id is not None]
    await repo.apply_metadata_user_tags([ids["ABC-005"], ids["ABC-009"]], tag_ids[:2], action="attach")
    await repo.apply_metadata_user_tags([ids["ABC-005"], ids["XYZ-002"]], tag_ids[2:], action="attach")
    await repo.create_comment(ids["ABC-005"], comment_body)
    await repo.create_comment(ids["ABC-005"], "评论占位文本 2")
    await repo.create_comment(ids["ABC-009"], "评论占位文本 3")

    favorite_tag = (await session.exec(select(Tag).where(Tag.name == "标签 1"))).first()
    favorite_studio = (await session.exec(select(Studio).where(Studio.name == "制作商 1"))).first()
    if favorite_tag and favorite_tag.id is not None:
        await repo.set_facets_favorite(FacetKind.TAG, [favorite_tag.id], True)
    if favorite_studio and favorite_studio.id is not None:
        await repo.set_facets_favorite(FacetKind.STUDIO, [favorite_studio.id], True)

    await repo.create_saved_query(
        name="查询 1",
        description="查询描述占位文本",
        sql=(
            "SELECT id, number, title, release FROM metadata "
            f"WHERE CAST(json_extract(scores, '$.{SITE_MAIN}') AS REAL) >= 8.0 "
            "ORDER BY release DESC"
        ),
        entity=SavedQueryEntity.METADATA,
        persisted=True,
    )
    await repo.create_saved_query(
        name="查询 2",
        description="查询描述占位文本",
        sql="SELECT id, number, title FROM metadata WHERE number LIKE 'XYZ-%' ORDER BY number",
        entity=SavedQueryEntity.METADATA,
        persisted=True,
    )
    await repo.create_saved_query(
        name="查询 3",
        description="查询描述占位文本",
        sql="SELECT id, name FROM actors ORDER BY name",
        entity=SavedQueryEntity.ACTOR,
        persisted=True,
    )


async def _seed_library(repo: Repository, ids: dict[str, int]) -> int:
    library = await repo.create_library(
        name="媒体库 1",
        path=str(MEDIA_ROOT),
        automation=LibraryAutomation.NONE,
        patterns=["*.mp4", "*.mkv"],
        video_template="{studio}/{year}/{number} {title}/{number}[-CD{cd?}][-{sub?}].{ext}",
        min_file_size=500 * 1024 * 1024,
    )
    assert library.id is not None
    for number, filename, status in LIBRARY_FILES:
        await repo.create_media_file(
            library.id,
            path=str(MEDIA_ROOT / filename),
            number=number,
            size=DUMMY_VIDEO_SIZE,
            status=status,
            metadata_id=ids.get(number) if status == MediaFileStatus.SCRAPED else None,
        )
    return library.id


async def _seed_second_library(repo: Repository, ids: dict[str, int]) -> None:
    root = PurePosixPath("/media2")
    library = await repo.create_library(
        name="媒体库 2",
        path=str(root),
        automation=LibraryAutomation.NONE,
        patterns=["*.mp4"],
        video_template="{number}/{number}[-{sub?}].{ext}",
        min_file_size=500 * 1024 * 1024,
    )
    assert library.id is not None
    for number, filename, status in SECOND_LIBRARY_FILES:
        await repo.create_media_file(
            library.id,
            path=str(root / filename),
            number=number,
            size=DUMMY_VIDEO_SIZE,
            status=status,
            metadata_id=ids.get(number) if status == MediaFileStatus.SCRAPED else None,
        )


async def _seed_schedules(repo: Repository, now: datetime) -> None:
    """cron 按 UTC 解释, 界面按浏览器时区 (Asia/Shanghai) 显示; 下次运行至少在一小时后, 截图期间不会触发."""
    earliest = now + timedelta(hours=1)
    schedules: tuple[tuple[str, str, RoutineType, dict[str, object], bool], ...] = (
        (
            "定时任务 1",
            "0 20 * * *",
            RoutineType.CLEANUP,
            {"remove_missing_files": True, "remove_unreferenced_resources": True},
            True,
        ),
        ("定时任务 2", "30 19 * * 6", RoutineType.RESCRAPE, {"limit": 100, "min_age_days": 30}, True),
        ("定时任务 3", "0 */6 * * *", RoutineType.UPSCALE, {"limit": 200}, False),
    )
    for name, cron, routine, payload, enabled in schedules:
        await repo.create_schedule(
            cron,
            routine,
            name=name,
            payload=payload,
            enabled=enabled,
            next_run=croniter(cron, earliest).get_next(datetime),
        )


async def _seed_agent(repo: Repository, data_dir: Path) -> None:
    """预置一段对话回放 (只写 events.jsonl, 不调用 LLM): 一次 sql_explore 工具调用加回复."""
    session = await repo.create_agent_session("会话 1")
    assert session.id is not None
    await repo.create_agent_session("会话 2")
    # 后建的会话排在前面; 把「会话 1」的 updated_at 刷新到最新, 让智能体首页默认打开它.
    await repo.update_agent_session(session.id, title="会话 1")
    sql = f"SELECT number, title, release FROM metadata WHERE json_extract(scores, '$.{SITE_MAIN}') >= 8.5"
    picked = [m for m in MOVIES if m.score >= 8.5][::-1][:3]
    rows: list[TraceRow] = [
        UserMessageRow(type="user_message", text="评分 8.5 以上的条目有哪些?"),
        TextDeltaRow(type="text_delta", block_id="m1", text="我先查一下评分。"),
        ToolCallRow(type="tool_call", tool_call_id="c1", name="sql_explore", args={"sql": sql}),
        ToolResultRow(
            type="tool_result",
            tool_call_id="c1",
            result={
                "columns": ["number", "title", "release"],
                "sample_rows": [[m.number, m.title, m.release] for m in picked],
                "row_count": len(picked),
                "truncated": False,
            },
        ),
        TextDeltaRow(
            type="text_delta",
            block_id="m2",
            text="\n".join(
                [f"共 {len(picked)} 条:", "", *(f"- **{m.number}** {m.title} ({m.release})" for m in picked)]
            ),
        ),
        TurnUsageRow(
            type="turn_usage",
            usage=TurnTokenUsage(input=1820, cache_read=6400, cache_write=0, output=164, requests=2),
        ),
    ]
    store = SessionStore(session_dir(data_dir, session.id))
    for row in rows:
        await store.append_row(row)


async def _seed_feeds(repo: Repository, now: datetime) -> None:
    feeds = (
        ("订阅源 1", "https://feeds.example/1.xml", "分组 1", ["XYZ-005", "XYZ-006", "XYZ-007", "XYZ-008"]),
        ("订阅源 2", "https://feeds.example/2.xml", "分组 1", ["ABC-009", "XYZ-002", "XYZ-013"]),
    )
    for name, url, group, numbers in feeds:
        feed = await repo.create_feed(name=name, url=url, group=group, auto_enqueue=False, interval_seconds=21600)
        assert feed.id is not None
        for offset, number in enumerate(numbers):
            movie = MOVIES_BY_NUMBER.get(number)
            title = movie.title if movie else f"标题 {len(MOVIES) + 1}"
            await repo.create_feed_item(
                feed_id=feed.id,
                item_key=f"{url}#{number}",
                title=f"[{number}] {title}",
                link=f"https://feeds.example/item/{number.lower()}",
                description=f"<p>{PLOT}</p>",
                number=number,
                published_at=now - timedelta(days=offset * 2 + 1, hours=offset * 3),
            )
        await repo.update_feed(
            feed.id,
            last_fetched_at=now - timedelta(minutes=42),
            # 远期下次拉取: 截图期间不访问网络.
            next_fetch_at=now + timedelta(days=365),
            last_enqueued=0,
        )


def _tasks(ids: dict[str, int], library_id: int, now: datetime) -> list[Task]:
    tasks: list[Task] = []
    started = now - timedelta(hours=3)
    scraped = [number for number, _f, status in LIBRARY_FILES if status == MediaFileStatus.SCRAPED]
    for i, number in enumerate(scraped):
        created = started + timedelta(minutes=i * 6)
        tasks.append(
            Task(
                type=TaskType.SCRAPE,
                status=TaskStatus.DONE,
                payload={"number": number, "content_type": ContentType.CHINESE.value, "use_cache": ["metadata"]},
                result={
                    "type": TaskType.SCRAPE.value,
                    "metadata_id": ids[number],
                    "field_sources": {"title": SITE_MAIN, "plot": SITE_MAIN},
                    "failed_sites": [],
                    "outcomes": [],
                },
                created_at=created,
                started_at=created + timedelta(seconds=2),
                finished_at=created + timedelta(seconds=9 + i),
            )
        )
    failed_at = started + timedelta(minutes=50)
    tasks.append(
        Task(
            type=TaskType.SCRAPE,
            status=TaskStatus.FAILED,
            payload={"number": "XYZ-012", "content_type": ContentType.CHINESE.value, "use_cache": []},
            error="所有来源均未找到该番号",
            created_at=failed_at,
            started_at=failed_at + timedelta(seconds=1),
            finished_at=failed_at + timedelta(seconds=6),
        )
    )
    refresh_at = started - timedelta(minutes=5)
    tasks.append(
        Task(
            type=TaskType.REFRESH,
            status=TaskStatus.DONE,
            payload={"library_id": library_id, "scan": ["add"], "scrape": ["pending"]},
            result={"type": TaskType.REFRESH.value, "added": len(LIBRARY_FILES), "removed": 0, "scrape": 8},
            created_at=refresh_at,
            started_at=refresh_at + timedelta(seconds=1),
            finished_at=refresh_at + timedelta(seconds=4),
        )
    )
    return tasks


@dataclass(frozen=True)
class SeedResult:
    """截图需要定位的条目 ID."""

    metadata_ids: dict[str, int]
    actor_ids: dict[str, int]
    library_id: int


async def seed(data_dir: Path, font_path: Path) -> SeedResult:
    ctx = SeedContext(data_dir=data_dir, font_path=font_path)
    data_dir.mkdir(parents=True, exist_ok=True)
    images, avatars, comment_image = render_images(ctx)
    now = datetime.now(UTC)

    engine = await create_async_engine_from_path(data_dir / "amane.db")
    try:
        repo = Repository(engine)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add_all(ctx.resources)
            await session.commit()
        ids = await _seed_metadata(repo, images)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await _seed_actors(repo, session, avatars)
            await _seed_annotations(repo, session, ids, comment_image)
        library_id = await _seed_library(repo, ids)
        await _seed_second_library(repo, ids)
        await _seed_feeds(repo, now)
        await _seed_schedules(repo, now)
        await _seed_agent(repo, data_dir)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add_all(_tasks(ids, library_id, now))
            await session.commit()
            actor_ids = {a.name: a.id for a in (await session.exec(select(Actor))).all() if a.id is not None}
    finally:
        await engine.dispose()
    logger.info("seeded %d movies, %d resources into %s", len(MOVIES), len(ctx.resources), data_dir)
    return SeedResult(metadata_ids=ids, actor_ids=actor_ids, library_id=library_id)


def find_font() -> Path:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    raise SystemExit("未找到 CJK 字体, 请用 --font 指定")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--font", type=Path, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data_dir: Path = args.data_dir
    if (data_dir / "amane.db").exists():
        raise SystemExit(f"{data_dir} 已有数据库; 只向全新目录写入")
    asyncio.run(seed(data_dir, args.font or find_font()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
