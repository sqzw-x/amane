"""parse_file_info 表测试: 分集 / 字幕 / 马赛克 / 清晰度, 只看文件名 (stem)."""

from typing import NamedTuple

import pytest

from amane.parsing import parse_file_info


class _Case(NamedTuple):
    filename: str
    cd: int | None = None
    has_subtitle: bool = False
    mosaic: str | None = None
    definition: str | None = None
    number: str | None = None


def _gap(case: _Case) -> object:
    """当前解析器过不了: 下划线/汉字邻接、`-UC` 后跟分片或清晰度、`HD-`/`SD-` 番号误报。修好后会 XPASS, 去掉此包装即可。"""
    return pytest.param(case, marks=pytest.mark.xfail(strict=True, reason="file_info marker delimiters"))


CASES: list[object] = [
    # --- 分集 ---
    _Case("MIDV-123-CD1.mp4", cd=1, number="MIDV-123"),
    _Case("MIDV-123-cd2.mp4", cd=2, number="MIDV-123"),
    _Case("MIDV-123-A.mp4", cd=1, number="MIDV-123"),
    _Case("MIDV-123-B.mp4", cd=2, number="MIDV-123"),
    _Case("MIDV-123.part2.mp4", cd=2, number="MIDV-123"),
    _Case("MIDV-123-1.mp4", cd=1, number="MIDV-123"),
    _Case("MIDV-123-2.mp4", cd=2, number="MIDV-123"),
    _Case("MIDV-123-3.mp4", cd=3, number="MIDV-123"),
    _Case("MIDV-123-9.mp4", cd=9, number="MIDV-123"),
    _Case("MIDV-123.mp4", number="MIDV-123"),
    _Case("MIDV-123-0.mp4"),
    _Case("MIDV-123-01.mp4"),
    _Case("MIDV-123-10.mp4"),
    _Case("MIDV-123-12.mp4"),
    # --- 字幕 ---
    _Case("MIDV-123-C.mp4", has_subtitle=True, number="MIDV-123"),
    _Case("MIDV-123-UC.mp4", has_subtitle=True, mosaic="uncensored", number="MIDV-123"),
    _Case("[字幕]MIDV-123.mp4", has_subtitle=True, number="MIDV-123"),
    _Case("[中文字幕]MIDV-123.mp4", has_subtitle=True, number="MIDV-123"),
    # --- 马赛克: 文件名标记 ---
    _Case("[無碼]MIDV-123.mp4", mosaic="uncensored", number="MIDV-123"),
    _Case("[无码]MIDV-123.mp4", mosaic="uncensored", number="MIDV-123"),
    _Case("[UNCENSORED]ABC-123.mp4", mosaic="uncensored", number="ABC-123"),
    _Case("ABC-123-uncensored.mp4", mosaic="uncensored", number="ABC-123"),
    _Case("[破解]MIDV-123.mp4", mosaic="cracked", number="MIDV-123"),
    _Case("[流出]MIDV-123.mp4", mosaic="cracked", number="MIDV-123"),
    _Case("MIDV-123-LEAKED.mp4", mosaic="cracked", number="MIDV-123"),
    _Case("MIDV-123流出.mp4", mosaic="cracked", number="MIDV-123"),
    # 无码标记优先于破解/流出
    _Case("MIDV-123-無碼流出.mp4", mosaic="uncensored", number="MIDV-123"),
    _Case("MIDV-123-無碼破解.mp4", mosaic="uncensored", number="MIDV-123"),
    # -UC 后面还跟分片或清晰度
    _gap(_Case("MIDV-123-UC-CD1.mp4", cd=1, has_subtitle=True, mosaic="uncensored", number="MIDV-123")),
    _gap(_Case("MIDV-123-UC-4K.mp4", has_subtitle=True, mosaic="uncensored", definition="4K", number="MIDV-123")),
    _gap(
        _Case(
            "MIDV-123-UC-CD1-4K.mp4", cd=1, has_subtitle=True, mosaic="uncensored", definition="4K", number="MIDV-123"
        )
    ),
    # 无文件名标记: mosaic 为空 (无码片商走 content_type, 不在本字段)
    _Case("HEYZO-123.mp4", number="HEYZO-123"),
    _Case("HEYZO-123-1080p.mp4", definition="1080p", number="HEYZO-123"),
    # --- 清晰度: 点号 / 连字符 ---
    _Case("ABC-123.8K.mp4", definition="8K", number="ABC-123"),
    _Case("ABC-123-4K.mp4", definition="4K", number="ABC-123"),
    _Case("ABC-123.2160p.mp4", definition="4K", number="ABC-123"),
    _Case("ABC-123.2160P.mp4", definition="4K", number="ABC-123"),
    _Case("ABC-123.1440p.mp4", definition="1440p", number="ABC-123"),
    _Case("ABC-123.1080p.mp4", definition="1080p", number="ABC-123"),
    _Case("ABC-123-720p.mp4", definition="720p", number="ABC-123"),
    _Case("ABC-123.480p.mp4", definition="480p", number="ABC-123"),
    _Case("ABC-123.HD.mp4", definition="HD", number="ABC-123"),
    _Case("ABC-123.SD.mp4", definition="SD", number="ABC-123"),
    # 多命中取最高
    _Case("ABC-123.1080p.HD.mp4", definition="1080p", number="ABC-123"),
    _Case("ABC-123.4K.2160p.mp4", definition="4K", number="ABC-123"),
    _Case("ABC-123.8K.HD.mp4", definition="8K", number="ABC-123"),
    _Case("ABC-123.720p.1080p.mp4", definition="1080p", number="ABC-123"),
    # 下划线分隔 (番号剥离已把 _ 当标记分隔符)
    _gap(_Case("MIDV-123_4K.mp4", definition="4K", number="MIDV-123")),
    _gap(_Case("MIDV-123_4K_无码.mp4", mosaic="uncensored", definition="4K", number="MIDV-123")),
    _gap(_Case("MIDV-123_1080p_x.mp4", definition="1080p", number="MIDV-123")),
    _gap(_Case("ABC-123_8K_HDR.mp4", definition="8K", number="ABC-123")),
    # 方括号 / 汉字紧贴
    _Case("[4K]MIDV-123.mp4", definition="4K", number="MIDV-123"),
    _gap(_Case("[4K無碼]MIDV-123.mp4", mosaic="uncensored", definition="4K", number="MIDV-123")),
    _gap(_Case("[1080p無碼]MIDV-123.mp4", mosaic="uncensored", definition="1080p", number="MIDV-123")),
    _gap(_Case("MIDV-123-4K無碼.mp4", mosaic="uncensored", definition="4K", number="MIDV-123")),
    _Case("MIDV-123-4K-無碼.mp4", mosaic="uncensored", definition="4K", number="MIDV-123"),
    _gap(_Case("[破解]MIDV-123_1080p.mp4", mosaic="cracked", definition="1080p", number="MIDV-123")),
    # 空格分隔
    _Case("ABC-123 4K.mp4", definition="4K", number="ABC-123"),
    _Case("ABC-123 1080p uncensored.mp4", mosaic="uncensored", definition="1080p", number="ABC-123"),
    # 帧率后缀仍识别清晰度
    _gap(_Case("ABC-123.1080p60.mp4", definition="1080p", number="ABC-123")),
    _gap(_Case("ABC-123.2160p30.mp4", definition="4K", number="ABC-123")),
    # 分集 + 清晰度
    _Case("MIDV-123-4K-CD1.mp4", cd=1, definition="4K", number="MIDV-123"),
    # --- 清晰度误报: 番号/编码里的字母数字不当作独立标记 ---
    _Case("SKYHD-172.mp4", number="SKYHD-172"),
    _Case("ABC-123.HDTV.mp4", number="ABC-123"),
    _Case("ABC-123.4KS.mp4", number="ABC-123"),
    _Case("ABC-2160.mp4"),
    _Case("ABC-123.1080.mp4", number="ABC-123"),
    _Case("ABC-123.mp4", number="ABC-123"),
    _gap(_Case("HD-123.mp4")),
    _gap(_Case("SD-123.mp4")),
    _Case("ABC-123.FHD.mp4", number="ABC-123"),
    _Case("ABC-123.UHD.mp4", number="ABC-123"),
    # 只看 stem, 不看父目录 (二次整理后标记已在目录上)
    _Case("/media/uncensored/4K/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/cracked/1080p/ABC-123.mp4", number="ABC-123"),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.filename)
def test_parse_file_info(case: _Case) -> None:
    info = parse_file_info(case.filename)
    assert info.cd == case.cd
    assert info.has_subtitle is case.has_subtitle
    assert info.mosaic == case.mosaic
    assert info.definition == case.definition
    if case.number is not None:
        assert info.number == case.number
