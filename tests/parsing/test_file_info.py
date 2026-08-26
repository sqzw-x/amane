"""parse_file_info 表测试: 完整路径上的番号、内容类型、分集、字幕、马赛克、清晰度."""

from typing import NamedTuple

import pytest

from amane.parsing import ContentType, parse_file_info


class _Case(NamedTuple):
    path: str
    cd: int | None = None
    has_subtitle: bool = False
    mosaic: str | None = None
    definition: str | None = None
    number: str | None = None
    content_type: ContentType | None = None


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
    # --- 完整路径: 目录关键词决定内容类型 ---
    _Case("/media/里番/something-123.mp4", content_type=ContentType.HENTAI),
    _Case("/media/裏番/something-123.mp4", content_type=ContentType.HENTAI),
    _Case("/media/getchu/item-123.mp4", content_type=ContentType.HENTAI),
    _Case("/media/GETCHU/item-123.mp4", content_type=ContentType.HENTAI),
    _Case("/media/欧美/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.WESTERN),
    _Case(
        "/mnt/nas/library/欧美/2024/studio/MIDV-123.mp4",
        number="MIDV-123",
        content_type=ContentType.WESTERN,
    ),
    # 东欧美 不含「欧美」单独命中
    _Case("/media/东欧美/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    # 路径关键词优先于番号分类
    _Case("/media/欧美/HEYZO-123.mp4", number="HEYZO-123", content_type=ContentType.WESTERN),
    _Case("/media/里番/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.HENTAI),
    # 无路径关键词: 按番号
    _Case("/media/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    _Case("/media/SSIS-456.mp4", number="SSIS-456", content_type=ContentType.CENSORED),
    _Case("/media/FC2-PPV-1234567.mp4", number="FC2-1234567", content_type=ContentType.FC2),
    _Case("/media/FC2PPV1234567.mp4", number="FC2-1234567", content_type=ContentType.FC2),
    _Case("/media/HEYZO-1234.mp4", number="HEYZO-1234", content_type=ContentType.UNCENSORED),
    _Case("/media/H4610-ki221218.mp4", number="H4610-KI221218", content_type=ContentType.UNCENSORED),
    _Case("/media/S2MBD-006.mp4", number="S2MBD-006", content_type=ContentType.UNCENSORED),
    _Case("/media/MD0165-1.mp4", cd=1, number="MD0165-1", content_type=ContentType.CHINESE),
    _Case("/media/259LUXU-1456.mp4", number="259LUXU-1456", content_type=ContentType.AMATEUR),
    _Case("/media/SIRO-4567.mp4", number="SIRO-4567", content_type=ContentType.AMATEUR),
    _Case("/media/SSNI00321.mp4", number="SSNI-321", content_type=ContentType.CENSORED),
    _Case("/media/Mywife No.1234.mp4", number="Mywife No.1234", content_type=ContentType.CENSORED),
    _Case("/media/Vixen.23.04.15.mp4", content_type=ContentType.WESTERN),
    # --- 完整路径: 目录名里的清晰度/马赛克不计入文件标记 ---
    _Case(
        "/media/uncensored/4K/MIDV-123.mp4",
        number="MIDV-123",
        content_type=ContentType.CENSORED,
    ),
    _Case(
        "/media/cracked/1080p/ABC-123.mp4",
        number="ABC-123",
        content_type=ContentType.CENSORED,
    ),
    _Case("/library/censored/HD/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    # --- 完整路径: 目录关键词与文件名标记同时出现 ---
    _Case(
        "/incoming/batch/[無碼]MIDV-123-4K.mp4",
        mosaic="uncensored",
        definition="4K",
        number="MIDV-123",
        content_type=ContentType.CENSORED,
    ),
    _Case(
        "/media/欧美/MIDV-123-4K-無碼.mp4",
        mosaic="uncensored",
        definition="4K",
        number="MIDV-123",
        content_type=ContentType.WESTERN,
    ),
    _Case(
        "/media/里番/foo-123-CD2.mp4",
        cd=2,
        content_type=ContentType.HENTAI,
    ),
    _Case(
        "/vol/data/library/sub/[破解]MIDV-123.1080p.mp4",
        mosaic="cracked",
        definition="1080p",
        number="MIDV-123",
        content_type=ContentType.CENSORED,
    ),
    _Case(
        "/media/videos/StudioX/MIDV-123-CD1.mp4",
        cd=1,
        number="MIDV-123",
        content_type=ContentType.CENSORED,
    ),
    _Case("/media/SSIS-456 CD2.mp4", number="SSIS-456", content_type=ContentType.CENSORED),
    _Case("/media/[HD]SSIS-456.mp4", definition="HD", number="SSIS-456", content_type=ContentType.CENSORED),
    _Case(
        "/media/videos/MIDV-123-UC.mp4",
        has_subtitle=True,
        mosaic="uncensored",
        number="MIDV-123",
        content_type=ContentType.CENSORED,
    ),
    _Case(
        "/media/欧美/ABC-123.1080p.mp4",
        definition="1080p",
        number="ABC-123",
        content_type=ContentType.WESTERN,
    ),
    _gap(
        _Case(
            "/media/lib/MIDV-123-UC-CD1.mp4",
            cd=1,
            has_subtitle=True,
            mosaic="uncensored",
            number="MIDV-123",
            content_type=ContentType.CENSORED,
        )
    ),
    _gap(
        _Case(
            "/incoming/MIDV-123_4K_无码.mp4",
            mosaic="uncensored",
            definition="4K",
            number="MIDV-123",
            content_type=ContentType.CENSORED,
        )
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.path)
def test_parse_file_info(case: _Case) -> None:
    info = parse_file_info(case.path)
    assert info.cd == case.cd
    assert info.has_subtitle is case.has_subtitle
    assert info.mosaic == case.mosaic
    assert info.definition == case.definition
    if case.number is not None:
        assert info.number == case.number
    if case.content_type is not None:
        assert info.content_type == case.content_type
