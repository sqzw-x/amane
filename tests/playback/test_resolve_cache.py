"""``cache_ttl`` 契约: 播放目标字段校验与解析结果缓存."""

import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from amane.playback.cache import PlaybackCaches
from amane.playback.factory import PlaybackState
from amane.plugins.api import (
    FilePlaybackTarget,
    HlsPlaybackTarget,
    PlaybackQuery,
    RelativeHlsLocator,
    UpstreamPlaybackTarget,
)

PlaybackTargetT = FilePlaybackTarget | UpstreamPlaybackTarget | HlsPlaybackTarget

_LOCATOR = RelativeHlsLocator("http://cdn.example/index.m3u8", playlist_text="#EXTM3U\n")
_QUERY = PlaybackQuery(metadata_id=1, number="TEST-001")


def _target(kind: str, cache_ttl: float | None) -> PlaybackTargetT:
    if kind == "file":
        return FilePlaybackTarget(
            path=Path("/tmp/clip.mp4"),
            content_type="video/mp4",
            media_file_id=1,
            cache_ttl=cache_ttl,
        )
    if kind == "upstream":
        return UpstreamPlaybackTarget(url="http://cdn.example/clip.mp4", cache_ttl=cache_ttl)
    return HlsPlaybackTarget(_LOCATOR, cache_ttl=cache_ttl)


@pytest.mark.parametrize("kind", ["file", "upstream", "hls"])
@pytest.mark.parametrize(
    ("cache_ttl", "expected"),
    [(None, None), (0.5, 0.5), (30, 30.0), (300.0, 300.0)],
)
def test_cache_ttl_accepts_absent_or_positive(kind: str, cache_ttl: float | None, expected: float | None) -> None:
    """缺省 ``None`` 表示不缓存; 正数 (含整数写法) 原样保留."""
    assert _target(kind, cache_ttl).cache_ttl == expected


@pytest.mark.parametrize("kind", ["file", "upstream", "hls"])
@pytest.mark.parametrize("cache_ttl", [0, -1.5, "abc", float("nan"), float("inf")])
def test_cache_ttl_rejects_non_positive_or_non_numeric(kind: str, cache_ttl: object) -> None:
    """三种目标共用同一套校验, 报错信息必须点名 ``cache_ttl`` 才便于作者排查."""
    with pytest.raises(ValidationError) as excinfo:
        _target(kind, cache_ttl)  # pyright: ignore[reportArgumentType] — 非法值故意绕过类型标注, 校验在运行期
    assert "cache_ttl" in str(excinfo.value)


def test_put_without_ttl_keeps_fixed_tier() -> None:
    """``put`` 的 ``ttl`` 是逐条覆盖: 不传时仍用该表的固定档位."""
    caches = PlaybackCaches()
    caches.open_fail.put("tier", True)
    caches.open_fail.put("short", True, ttl=0.05)
    time.sleep(0.1)
    assert caches.open_fail.is_blocked("tier")
    assert not caches.open_fail.is_blocked("short")


def test_resolve_hits_use_per_entry_ttl() -> None:
    """解析结果每条记录自己的到期时间, 与探测 / 打开失败的固定档位互不影响."""
    caches = PlaybackCaches()
    short = UpstreamPlaybackTarget(url="http://cdn.example/short.mp4", cache_ttl=0.05)
    long = UpstreamPlaybackTarget(url="http://cdn.example/long.mp4", cache_ttl=60.0)
    caches.resolve_hits.put("short", short, ttl=short.cache_ttl)
    caches.resolve_hits.put("long", long, ttl=long.cache_ttl)
    assert caches.resolve_hits.get("long") is long
    time.sleep(0.1)
    assert caches.resolve_hits.get("short") is None
    assert caches.resolve_hits.get("long") is long


def test_reset_clears_resolve_hits() -> None:
    """插件集合变化时解析结果随 token 表一起作废."""
    caches = PlaybackCaches()
    caches.resolve_hits.put("k", _target("upstream", 30.0), ttl=30.0)
    caches.reset()
    assert caches.resolve_hits.get("k") is None


def test_invalidate_resolved_keeps_tokens_and_negative_caches() -> None:
    """rebuild 只丢解析结果: token 表与探测 / 打开失败的缓存必须留着, 在播会话不受影响."""
    state = PlaybackState()
    token = state.hls.register(
        source_id="acme.play",
        query=_QUERY,
        locator=_LOCATOR,
        uri="http://cdn.example/seg.ts",
        is_key=False,
    )
    state.caches.resolve_hits.put("k", _target("hls", 30.0), ttl=30.0)
    state.caches.open_fail.put("k", True)
    state.invalidate_resolved()
    assert state.caches.resolve_hits.get("k") is None
    assert state.caches.open_fail.is_blocked("k")
    assert state.hls.get(token) is not None
