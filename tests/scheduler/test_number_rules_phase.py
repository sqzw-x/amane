"""相位回填: 用户配的 `prefix_types` 必须一路写进 `MediaFile.content_type`.

遗漏任一写入口都会出现「同一文件库内一列一个说法」, 因此这里按入口逐条断言.
"""

from pathlib import Path

import pytest

from amane.config import ParsingConfig, build_number_rules
from amane.db.models import LibraryAutomation, MediaFileStatus
from amane.events import EventBus
from amane.parsing import ContentType
from amane.scheduler.service import WatcherService


def _rules():
    """用户把 MIDV 约定为无码; 内置判定给它的是 censored."""
    return build_number_rules(ParsingConfig(prefix_types={"MIDV": ContentType.UNCENSORED}))


@pytest.mark.asyncio(loop_scope="function")
async def test_registration_writes_configured_phase(repo, tmp_path: Path):
    """watcher 登记: 相位按当时规则落库, 与解析结果一致."""
    library = await repo.create_library(name="t", path=str(tmp_path), automation=LibraryAutomation.WATCH)
    assert library.id is not None
    video = tmp_path / "MIDV-123.mp4"
    video.write_bytes(b"x")

    service = WatcherService(repo, EventBus(), number_rules=_rules())
    await service._on_file_found(video, library.id)

    media = await repo.get_media_file_by_path(str(video))
    assert media is not None
    assert media.content_type is ContentType.UNCENSORED


@pytest.mark.asyncio(loop_scope="function")
async def test_moved_file_recomputes_with_configured_rules(repo, tmp_path: Path):
    """watcher 观察到移动: 改 path 时同样用当时规则重算, 不会退回内置判定."""
    library = await repo.create_library(name="t", path=str(tmp_path), automation=LibraryAutomation.WATCH)
    assert library.id is not None
    src = tmp_path / "old.mp4"
    dest = tmp_path / "MIDV-123.mp4"
    media = await repo.create_media_file(library_id=library.id, path=str(src), rules=_rules())
    assert media.id is not None
    # 源文件名没有 MIDV, 规则不命中, 因此内置判定给的仍是 censored.
    assert media.content_type is ContentType.CENSORED

    service = WatcherService(repo, EventBus(), number_rules=_rules())
    await service._on_file_moved(src, dest, library.id)

    updated = await repo.get_media_file(media.id)
    assert updated is not None
    assert updated.path == str(dest)
    assert updated.content_type is ContentType.UNCENSORED


@pytest.mark.asyncio(loop_scope="function")
async def test_set_number_rules_takes_effect(repo, tmp_path: Path):
    """热重载替换规则后, 后续登记用新规则."""
    library = await repo.create_library(name="t", path=str(tmp_path), automation=LibraryAutomation.WATCH)
    assert library.id is not None

    service = WatcherService(repo, EventBus())
    service.set_number_rules(_rules())

    video = tmp_path / "MIDV-456.mp4"
    video.write_bytes(b"x")
    await service._on_file_found(video, library.id)

    media = await repo.get_media_file_by_path(str(video))
    assert media is not None
    assert media.content_type is ContentType.UNCENSORED


@pytest.mark.asyncio(loop_scope="function")
async def test_rewrite_media_path_uses_given_rules(repo, tmp_path: Path):
    """改 path 的专用入口: 传入规则决定相位, 不会被内置判定覆盖."""
    media = await repo.create_media_file(library_id=1, path=str(tmp_path / "a.mp4"), rules=_rules())
    assert media.id is not None

    updated = await repo.rewrite_media_path(media.id, str(tmp_path / "MIDV-789.mp4"), rules=_rules(), library=None)

    assert updated is not None
    assert updated.content_type is ContentType.UNCENSORED


@pytest.mark.asyncio(loop_scope="function")
async def test_metadata_only_update_keeps_phase(repo, tmp_path: Path):
    """只更新元数据的调用不重算相位."""
    media = await repo.create_media_file(
        library_id=1, path=str(tmp_path / "MIDV-001.mp4"), rules=_rules(), status=MediaFileStatus.PENDING
    )
    assert media.id is not None

    updated = await repo.update_media_file(media.id, number="MIDV-001")

    assert updated is not None
    assert updated.content_type is ContentType.UNCENSORED
