"""文件相位投影与条目相位聚合: 有码 / 无码取条目类型, 其余按任一文件具备."""

import pytest

from amane.parsing import (
    ContentType,
    FilePhase,
    Mosaic,
    MosaicNature,
    file_phase_from_path,
    max_definition,
    mosaic_nature,
    parse_file_info,
    shows_uncensored,
    summarize_file_phases,
)


@pytest.mark.parametrize(
    ("path", "content_type", "mosaic"),
    [
        ("/media/MIDV-123.mp4", ContentType.CENSORED, None),
        ("/media/HEYZO-1234.mp4", ContentType.UNCENSORED, None),
        ("/media/MD0165-1.mp4", ContentType.CHINESE, None),
        ("/media/MIDV-123-U.mp4", ContentType.CENSORED, Mosaic.CRACKED),
        ("/media/MIDV-123-无码.mp4", ContentType.CENSORED, Mosaic.UNCENSORED),
        ("/media/无码/MIDV-123.mp4", ContentType.CENSORED, Mosaic.UNCENSORED),
    ],
)
def test_file_phase_from_path_keeps_marker_only(path: str, content_type: ContentType, mosaic: Mosaic | None) -> None:
    """类型不落文件相位, 马赛克只收词表命中的来路标记."""
    assert file_phase_from_path(path)["mosaic"] is mosaic
    assert parse_file_info(path).content_type is content_type


@pytest.mark.parametrize(
    ("mosaic", "content_type", "expected"),
    [
        (None, ContentType.UNCENSORED, True),
        (Mosaic.UNCENSORED, ContentType.CENSORED, True),
        (Mosaic.CRACKED, ContentType.UNCENSORED, True),
        (None, ContentType.CENSORED, False),
        (Mosaic.CRACKED, ContentType.CENSORED, False),
        # 未绑定条目的文件只看自身标记
        (None, None, False),
        (Mosaic.UNCENSORED, None, True),
    ],
)
def test_shows_uncensored(mosaic: Mosaic | None, content_type: ContentType | None, expected: bool) -> None:
    assert shows_uncensored(mosaic, content_type) is expected


@pytest.mark.parametrize(
    ("mosaic", "content_type", "expected"),
    [
        (None, ContentType.CENSORED, MosaicNature.CENSORED),
        (None, ContentType.UNCENSORED, MosaicNature.UNCENSORED),
        (Mosaic.UNCENSORED, ContentType.CENSORED, MosaicNature.UNCENSORED),
        (Mosaic.CRACKED, ContentType.UNCENSORED, MosaicNature.CRACKED),
        (Mosaic.LEAKED, ContentType.CENSORED, MosaicNature.LEAKED),
        (None, ContentType.CHINESE, None),
        (None, None, None),
    ],
)
def test_mosaic_nature(mosaic: Mosaic | None, content_type: ContentType | None, expected: MosaicNature | None) -> None:
    """性质: 标记优先, 没命中时继承条目类型的有码 / 无码."""
    assert mosaic_nature(mosaic, content_type) is expected


def test_every_mosaic_marker_has_a_nature() -> None:
    """标记与性质是两个枚举: 加标记时没同步, 整理落盘会在 mosaic_nature 抛 ValueError."""
    assert {marker.value for marker in Mosaic} <= {nature.value for nature in MosaicNature}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((), None),
        ((None, None), None),
        (("1080p", "4K", "720p"), "4K"),
        (("HD", "1080p"), "1080p"),
        (("unknown",), None),
    ],
)
def test_max_definition(values: tuple[str | None, ...], expected: str | None) -> None:
    assert max_definition(values) == expected


def test_summarize_file_phases_or_across_files() -> None:
    cracked: FilePhase = {"mosaic": Mosaic.CRACKED, "has_subtitle": True, "definition": "1080p"}
    leaked: FilePhase = {"mosaic": Mosaic.LEAKED, "has_subtitle": False, "definition": "4K"}
    summary = summarize_file_phases((cracked, leaked), ContentType.CENSORED)
    assert summary.censored is True
    assert summary.uncensored is False
    assert summary.has_subtitle is True
    assert summary.mosaics == (Mosaic.CRACKED, Mosaic.LEAKED)
    assert summary.definition == "4K"


@pytest.mark.parametrize(
    ("content_type", "censored", "uncensored"),
    [
        (ContentType.CENSORED, True, False),
        (ContentType.UNCENSORED, False, True),
        (ContentType.WESTERN, False, False),
    ],
)
def test_summarize_file_phases_without_files(content_type: ContentType, censored: bool, uncensored: bool) -> None:
    """没有挂载文件时条目相位仍然成立."""
    summary = summarize_file_phases((), content_type)
    assert summary.censored is censored
    assert summary.uncensored is uncensored
    assert summary.mosaics == ()
    assert summary.definition is None


def test_summarize_file_phases_uncensored_marker_on_censored_entry() -> None:
    """有码番的无码标记副本: 条目有码, 无码由文件标记并进来."""
    marked: FilePhase = {"mosaic": Mosaic.UNCENSORED, "has_subtitle": False, "definition": None}
    summary = summarize_file_phases((marked,), ContentType.CENSORED)
    assert summary.censored is True
    assert summary.uncensored is True
    assert summary.mosaics == (Mosaic.UNCENSORED,)
