"""Filetree 索引的校验: ETag 比对、失败回退旧索引、失败后退避.

编码与路径规则在 ``cases/gfriends`` 的 TOML 用例里; 这里只覆盖需要多次调用才出现的缓存与校验状态.
缓存年龄用 ``os.utime`` 设置为过去的时间, 不依赖真实等待.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from amane.crawlers.actor.sites import gfriends as gfriends_module
from amane.crawlers.actor.sites.gfriends import GFriendsActorCrawler
from amane.crawlers.http import HttpClient
from amane.net.errors import RequestError

_AGENCY = "A-Agency"
_CACHED_FILE = "Alice.jpg"
_CACHED_URL = f"https://cache.example/Content/{_AGENCY}/Alice.jpg"
_TREE: dict[str, Any] = {"Content": {_AGENCY: {"Bob.jpg": "Bob.jpg"}}}
_TREE_URL = "https://raw.githubusercontent.com/gfriends/gfriends/master/Filetree.json"
_MIRROR_URL = "https://mirror.example/gfriends/master/Filetree.json"
_REMOTE_URL = f"https://raw.githubusercontent.com/gfriends/gfriends/master/Content/{_AGENCY}/Bob.jpg"
_STALE_AGE_S = gfriends_module._CHECK_INTERVAL_S + 60.0
_FILES_DOWNLOADED = {"gfriends.json": ["Bob.jpg"], "gfriends.etag": '"v1"'}
_FILES_NO_ETAG = {"gfriends.json": ["Bob.jpg"], "gfriends.etag": None}
_FILES_KEPT = {"gfriends.json": ["Alice.jpg"], "gfriends.etag": '"v0"'}


@dataclass(frozen=True)
class _Head:
    """``HEAD`` 的应答: 校验只读它的 ETag."""

    etag: str | None

    @property
    def headers(self) -> dict[str, str]:
        return {"etag": self.etag} if self.etag is not None else {}


@dataclass(frozen=True)
class _Case:
    """一行缓存 / ETag / 上游状态; 其余字段是应当出现的请求与写入磁盘的文件."""

    cache_age: float | None = None  # None: 没有索引缓存文件
    broken_cache: str | None = None  # "json" / "utf8": 缓存内容不可读
    etag: str | None = None  # 写入 gfriends.etag 的 ETag
    etag_url: str = _TREE_URL
    broken_etag: str | None = None  # "json" / "utf8": ETag 记录不可读
    head: str | None = '"v1"'  # HEAD 取到的 ETag
    probe_fails: bool = False
    tree: dict[str, Any] = field(default_factory=lambda: _TREE)
    fetch_fails: bool = False
    query: str = "Bob"
    hit: str | None = None
    gets: int = 0
    heads: int = 1
    touched: bool = False  # 命中后是否刷新缓存 mtime
    raises: bool = False
    files: dict[str, object] = field(default_factory=dict)  # 文件名 → 索引条目列表或 ETag


_STALE = _Case(cache_age=_STALE_AGE_S)  # 过期缓存的基线, 其余行在它上面改差异

_CASES: list[tuple[str, _Case]] = [
    ("无缓存", _Case(hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED)),
    ("缓存新鲜", _Case(cache_age=60.0, query="Alice", hit=_CACHED_URL, heads=0)),
    ("缓存 JSON 损坏", _Case(broken_cache="json", hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED)),
    ("缓存编码损坏", _Case(broken_cache="utf8", hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED)),
    ("ETag 一致", replace(_STALE, etag='"v1"', query="Alice", hit=_CACHED_URL, touched=True)),
    ("ETag 变化", replace(_STALE, etag='"v0"', hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED)),
    ("无 ETag 记录", replace(_STALE, hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED)),
    ("ETag 记录 JSON 损坏", replace(_STALE, broken_etag="json", hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED)),
    ("ETag 记录编码损坏", replace(_STALE, broken_etag="utf8", hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED)),
    ("HEAD 失败", replace(_STALE, etag='"v0"', probe_fails=True, hit=_REMOTE_URL, gets=1, files=_FILES_NO_ETAG)),
    ("HEAD 无 ETag", replace(_STALE, etag='"v0"', head=None, hit=_REMOTE_URL, gets=1, files=_FILES_NO_ETAG)),
    (
        "ETag 属于其他地址",
        replace(_STALE, etag='"v1"', etag_url=_MIRROR_URL, hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED),
    ),
    (
        "换过仓库地址",
        _Case(cache_age=60.0, etag='"v1"', etag_url=_MIRROR_URL, hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED),
    ),
    ("缓存已删除", _Case(etag='"v1"', hit=_REMOTE_URL, gets=1, files=_FILES_DOWNLOADED)),
    (
        "空应答沿用旧索引",
        replace(_STALE, etag='"v0"', tree={}, query="Alice", hit=_CACHED_URL, gets=1, files=_FILES_KEPT),
    ),
    (
        "下载失败退回旧索引",
        replace(_STALE, etag='"v0"', fetch_fails=True, query="Alice", hit=_CACHED_URL, gets=1, files=_FILES_KEPT),
    ),
    ("无缓存且下载失败", _Case(fetch_fails=True, raises=True, gets=1)),
]


def _seed_cache(data_dir: Path, *, age_s: float) -> None:
    """写入只含 ``Alice`` 的索引缓存, 并把 mtime 设置为 ``age_s`` 秒之前."""
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / "gfriends.json"
    payload = {_CACHED_FILE: {"url": _CACHED_URL, "path": f"Content/{_AGENCY}/{_CACHED_FILE}"}}
    cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    stamp = time.time() - age_s
    os.utime(cache, (stamp, stamp))


def _crawler(case: _Case, data_dir: Path) -> tuple[GFriendsActorCrawler, AsyncMock]:
    """按行准备缓存文件与 mock 上游."""
    if case.broken_cache == "json":
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "gfriends.json").write_text("{not json", encoding="utf-8")
    elif case.broken_cache == "utf8":
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "gfriends.json").write_bytes(b"\xff\xfe\x00")
    elif case.cache_age is not None:
        _seed_cache(data_dir, age_s=case.cache_age)
    if case.broken_etag == "json":
        (data_dir / "gfriends.etag").write_text("{not json", encoding="utf-8")
    elif case.broken_etag == "utf8":
        (data_dir / "gfriends.etag").write_bytes(b"\xff\xfe\x00")
    elif case.etag is not None:
        payload = {"url": case.etag_url, "etag": case.etag}
        (data_dir / "gfriends.etag").write_text(json.dumps(payload), encoding="utf-8")

    web = AsyncMock()
    if case.probe_fails:
        web.request.side_effect = RequestError(_TREE_URL, "boom")
    else:
        web.request.return_value = _Head(case.head)
    if case.fetch_fails:
        web.get_json.side_effect = RequestError(_TREE_URL, "boom")
    else:
        web.get_json.return_value = case.tree
    return GFriendsActorCrawler(client=HttpClient(web=web, browser=None), data_dir=data_dir), web


def _assert_files(data_dir: Path, expected: dict[str, object]) -> None:
    for name, want in expected.items():
        raw = json.loads((data_dir / name).read_text(encoding="utf-8"))
        got = raw["etag"] if name.endswith(".etag") else list(raw)
        assert got == want, name


async def _url_of(crawler: GFriendsActorCrawler, name: str) -> str | None:
    hit = await crawler.fetch(name)
    return hit.image_urls[0] if hit is not None else None


@pytest.mark.parametrize(("label", "expected"), _CASES, ids=[case[0] for case in _CASES])
@pytest.mark.asyncio
async def test_index_check(tmp_path: Path, label: str, expected: _Case) -> None:
    crawler, web = _crawler(expected, tmp_path)

    if expected.raises:
        with pytest.raises(RequestError):
            await crawler.fetch(expected.query)
    else:
        assert await _url_of(crawler, expected.query) == expected.hit, label

    assert web.get_json.await_count == expected.gets, label
    assert web.request.await_count == expected.heads, label
    if expected.heads:
        assert web.request.await_args.args[:2] == ("HEAD", _TREE_URL), label
        assert web.request.await_args.kwargs["max_attempts"] == 1, label
    if expected.gets:
        assert web.get_json.await_args.args[0] == _TREE_URL, label
    if expected.touched:
        assert time.time() - (tmp_path / "gfriends.json").stat().st_mtime < 60, label
    _assert_files(tmp_path, expected.files)


@pytest.mark.asyncio
async def test_empty_answer_keeps_stale_index(tmp_path: Path) -> None:
    """空索引按失败处理: 沿用旧索引并退避, 不能把进程里的头像清空一个间隔."""
    crawler, web = _crawler(replace(_STALE, etag='"v0"', tree={}), tmp_path)

    assert await _url_of(crawler, "Alice") == _CACHED_URL
    assert await _url_of(crawler, "Alice") == _CACHED_URL
    assert web.get_json.await_count == 1


@pytest.mark.asyncio
async def test_empty_answer_without_cache_backs_off(tmp_path: Path) -> None:
    """没有旧索引可用时保留空索引并退避, 不能每查一个演员就重试一次."""
    crawler, web = _crawler(_Case(tree={}), tmp_path)

    assert await _url_of(crawler, "Alice") is None
    assert await _url_of(crawler, "Alice") is None
    assert web.get_json.await_count == 1


@pytest.mark.asyncio
async def test_memory_index_outlives_cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """索引已在内存时不再回读磁盘, 也不因缓存文件消失而重新请求."""
    crawler, web = _crawler(_Case(cache_age=60.0), tmp_path)
    assert await _url_of(crawler, "Alice") == _CACHED_URL

    (tmp_path / "gfriends.json").unlink()
    web.request.side_effect = RequestError(_TREE_URL, "boom")
    web.get_json.side_effect = RequestError(_TREE_URL, "boom")

    assert await _url_of(crawler, "Alice") == _CACHED_URL
    assert web.request.await_count == 0
    assert web.get_json.await_count == 0

    # 缓存文件已删, 到期后刷新失败时只能靠内存索引回退.
    monkeypatch.setattr(gfriends_module, "_CHECK_INTERVAL_S", -1.0)
    assert await _url_of(crawler, "Alice") == _CACHED_URL
    assert web.get_json.await_count == 1


@pytest.mark.asyncio
async def test_memory_index_expires_with_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """校验间隔到期时内存索引随之失效, 不因缓存文件仍在而长期沿用."""
    crawler, web = _crawler(_Case(cache_age=60.0), tmp_path)
    assert await _url_of(crawler, "Alice") == _CACHED_URL

    monkeypatch.setattr(gfriends_module, "_CHECK_INTERVAL_S", -1.0)
    assert await _url_of(crawler, "Bob") == _REMOTE_URL
    assert web.get_json.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_refresh_downloads_once(tmp_path: Path) -> None:
    """到期瞬间的并发 fetch 由锁串行化, 只下载一份索引."""
    crawler, web = _crawler(_Case(cache_age=_STALE_AGE_S, hit=_REMOTE_URL), tmp_path)

    async def slow(url: str, **kwargs: object) -> dict[str, Any]:
        await asyncio.sleep(0)  # 让并发调用都先进入等锁
        return _TREE

    web.get_json.side_effect = slow
    results = await asyncio.gather(*(crawler.fetch("Bob") for _ in range(5)))

    assert [result.image_urls[0] if result is not None else None for result in results] == [_REMOTE_URL] * 5
    assert web.get_json.await_count == 1


# (id, 退避间隔, 连续查询次数, 期望下载次数)
_BACKOFF_CASES: list[tuple[str, float, int, int]] = [
    ("退避窗口内不重试", gfriends_module._RETRY_INTERVAL_S, 3, 1),
    ("退避窗口结束后重试", 0.0, 2, 2),
]


@pytest.mark.parametrize(
    ("interval", "fetches", "downloads"), [case[1:] for case in _BACKOFF_CASES], ids=[c[0] for c in _BACKOFF_CASES]
)
@pytest.mark.asyncio
async def test_failed_check_backs_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interval: float, fetches: int, downloads: int
) -> None:
    """上游不可达时沿用旧索引; 退避窗口内的查询不再发请求, 避免每个演员各触发一次超时."""
    monkeypatch.setattr(gfriends_module, "_RETRY_INTERVAL_S", interval)
    crawler, web = _crawler(replace(_STALE, fetch_fails=True), tmp_path)

    for _ in range(fetches):
        assert await _url_of(crawler, "Alice") == _CACHED_URL
    assert web.get_json.await_count == downloads


@pytest.mark.asyncio
async def test_write_failure_keeps_etag(tmp_path: Path) -> None:
    """索引没写成时不更新 ETag, 否则下次比对会把旧索引当成最新."""
    (tmp_path / "gfriends.json").mkdir()  # 目标是目录, 替换必然失败
    crawler, _ = _crawler(_Case(etag='"v0"'), tmp_path)

    assert await _url_of(crawler, "Bob") == _REMOTE_URL
    _assert_files(tmp_path, {"gfriends.etag": '"v0"'})
