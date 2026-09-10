"""文件相位聚合 / 无码判定 / 清晰度取最高档."""

import pytest

from amane.parsing import (
    ContentType,
    FilePhase,
    Mosaic,
    file_phase_from_path,
    file_shows_uncensored,
    max_definition,
    summarize_file_phases,
)


def test_file_phase_from_path_midv_fills_censored() -> None:
    phase = file_phase_from_path("/media/MIDV-123.mp4")
    assert phase["content_type"] is ContentType.CENSORED
    assert phase["mosaic"] is Mosaic.CENSORED
    assert file_shows_uncensored(phase["mosaic"], phase["content_type"]) is False


def test_file_phase_from_path_heyzo_uncensored_type_fills_mosaic() -> None:
    phase = file_phase_from_path("/media/HEYZO-1234.mp4")
    assert phase["content_type"] is ContentType.UNCENSORED
    assert phase["mosaic"] is Mosaic.UNCENSORED
    assert phase["has_subtitle"] is False
    assert file_shows_uncensored(phase["mosaic"], phase["content_type"]) is True


def test_file_phase_from_path_chinese_leaves_mosaic_empty() -> None:
    phase = file_phase_from_path("/media/MD0165-1.mp4")
    assert phase["content_type"] is ContentType.CHINESE
    assert phase["mosaic"] is None


def test_file_phase_from_path_midv_u_is_cracked_not_uncensored() -> None:
    phase = file_phase_from_path("/media/MIDV-123-U.mp4")
    assert phase["content_type"] is ContentType.CENSORED
    assert phase["mosaic"] is Mosaic.CRACKED
    assert file_shows_uncensored(phase["mosaic"], phase["content_type"]) is False


def test_file_phase_from_path_midv_uncensored_marker_not_content_type() -> None:
    phase = file_phase_from_path("/media/MIDV-123-无码.mp4")
    assert phase["content_type"] is ContentType.CENSORED
    assert phase["mosaic"] is Mosaic.UNCENSORED
    assert file_shows_uncensored(phase["mosaic"], phase["content_type"]) is True


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
    censored: FilePhase = {
        "content_type": ContentType.CENSORED,
        "mosaic": None,
        "has_subtitle": True,
        "definition": "1080p",
    }
    cracked: FilePhase = {
        "content_type": ContentType.CENSORED,
        "mosaic": Mosaic.CRACKED,
        "has_subtitle": False,
        "definition": "4K",
    }
    heyzo: FilePhase = {
        "content_type": ContentType.UNCENSORED,
        "mosaic": None,
        "has_subtitle": False,
        "definition": None,
    }
    summary = summarize_file_phases((censored, cracked, heyzo))
    assert summary.has_subtitle is True
    assert summary.uncensored is True
    assert summary.mosaics == (Mosaic.CRACKED,)
    assert summary.definition == "4K"


def test_summarize_file_phases_empty() -> None:
    summary = summarize_file_phases(())
    assert summary.has_subtitle is False
    assert summary.uncensored is False
    assert summary.mosaics == ()
    assert summary.definition is None
