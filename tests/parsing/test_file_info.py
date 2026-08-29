"""parse_file_info 表测试: 完整路径上的番号、内容类型、分集、字幕、马赛克、清晰度."""

from typing import NamedTuple

import pytest

from amane.parsing import (
    ContentType,
    classify_number,
    extract_number,
    get_prefix,
    infer_content_type,
    is_amateur,
    is_uncensored,
    parse_file_info,
)


class _Case(NamedTuple):
    path: str
    cd: int | None = None
    has_subtitle: bool = False
    mosaic: str | None = None
    definition: str | None = None
    number: str | None = None
    content_type: ContentType | None = None


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
    _Case("MIDV-123-UC-CD1.mp4", cd=1, has_subtitle=True, mosaic="uncensored", number="MIDV-123"),
    _Case("MIDV-123-UC-4K.mp4", has_subtitle=True, mosaic="uncensored", definition="4K", number="MIDV-123"),
    _Case("MIDV-123-UC-CD1-4K.mp4", cd=1, has_subtitle=True, mosaic="uncensored", definition="4K", number="MIDV-123"),
    _Case("MIDV-123-U.mp4", mosaic="uncensored", number="MIDV-123"),
    _Case("MIDV-123-C-U.mp4", has_subtitle=True, mosaic="uncensored", number="MIDV-123"),
    _Case("MIDV-123-U-CD1.mp4", cd=1, mosaic="uncensored", number="MIDV-123"),
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
    _Case("MIDV-123_4K.mp4", definition="4K", number="MIDV-123"),
    _Case("MIDV-123_4K_无码.mp4", mosaic="uncensored", definition="4K", number="MIDV-123"),
    _Case("MIDV-123_1080p_x.mp4", definition="1080p", number="MIDV-123"),
    _Case("ABC-123_8K_HDR.mp4", definition="8K", number="ABC-123"),
    # 方括号 / 汉字紧贴
    _Case("[4K]MIDV-123.mp4", definition="4K", number="MIDV-123"),
    _Case("[4K無碼]MIDV-123.mp4", mosaic="uncensored", definition="4K", number="MIDV-123"),
    _Case("[1080p無碼]MIDV-123.mp4", mosaic="uncensored", definition="1080p", number="MIDV-123"),
    _Case("MIDV-123-4K無碼.mp4", mosaic="uncensored", definition="4K", number="MIDV-123"),
    _Case("MIDV-123-4K-無碼.mp4", mosaic="uncensored", definition="4K", number="MIDV-123"),
    _Case("[破解]MIDV-123_1080p.mp4", mosaic="cracked", definition="1080p", number="MIDV-123"),
    # 空格分隔
    _Case("ABC-123 4K.mp4", definition="4K", number="ABC-123"),
    _Case("ABC-123 1080p uncensored.mp4", mosaic="uncensored", definition="1080p", number="ABC-123"),
    # 帧率后缀仍识别清晰度
    _Case("ABC-123.1080p60.mp4", definition="1080p", number="ABC-123"),
    _Case("ABC-123.2160p30.mp4", definition="4K", number="ABC-123"),
    # 分集 + 清晰度
    _Case("MIDV-123-4K-CD1.mp4", cd=1, definition="4K", number="MIDV-123"),
    # --- 清晰度误报: 番号/编码里的字母数字不当作独立标记 ---
    _Case("SKYHD-172.mp4", number="SKYHD-172"),
    _Case("ABC-123.HDTV.mp4", number="ABC-123"),
    _Case("ABC-123.4KS.mp4", number="ABC-123"),
    _Case("ABC-2160.mp4"),
    _Case("ABC-123.1080.mp4", number="ABC-123"),
    _Case("ABC-123.mp4", number="ABC-123"),
    _Case("HD-123.mp4"),
    _Case("SD-123.mp4"),
    _Case("HD_123.mp4"),
    _Case("SD_123.mp4"),
    _Case("ABC-123.FHD.mp4", number="ABC-123"),
    _Case("ABC-123.UHD.mp4", number="ABC-123"),
    # 字幕反例: -CD / -CS 不是 -C
    _Case("MIDV-123-CS.mp4", number="MIDV-123"),
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
    # getchu 必须整段相等; 子串不分类
    _Case("/media/forgetchu/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    _Case("/media/getchu-docs/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    _Case("/media/my-getchu/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    # 里番/欧美须在段首; 嵌在其它词里不算
    _Case("/media/里番合集/something-123.mp4", content_type=ContentType.HENTAI),
    _Case("/media/欧美片商/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.WESTERN),
    _Case("/media/非欧美/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    _Case("/media/这里番号/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
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
    # --- 完整路径: 马赛克可从目录名整段补; 清晰度仍只看文件名 ---
    _Case(
        "/media/uncensored/4K/MIDV-123.mp4",
        mosaic="uncensored",
        number="MIDV-123",
        content_type=ContentType.CENSORED,
    ),
    _Case(
        "/media/cracked/1080p/ABC-123.mp4",
        mosaic="cracked",
        number="ABC-123",
        content_type=ContentType.CENSORED,
    ),
    _Case("/library/censored/HD/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    _Case("/media/Uncensored/MIDV-123.mp4", mosaic="uncensored", number="MIDV-123"),
    _Case("/media/无码/MIDV-123.mp4", mosaic="uncensored", number="MIDV-123"),
    _Case("/media/無碼/MIDV-123.mp4", mosaic="uncensored", number="MIDV-123"),
    _Case("/media/LEAKED/MIDV-123.mp4", mosaic="cracked", number="MIDV-123"),
    _Case("/media/破解/MIDV-123.mp4", mosaic="cracked", number="MIDV-123"),
    _Case("/media/流出/MIDV-123.mp4", mosaic="cracked", number="MIDV-123"),
    _Case("/media/[无码]/MIDV-123.mp4", mosaic="uncensored", number="MIDV-123"),
    _Case("/media/【破解】/MIDV-123.mp4", mosaic="cracked", number="MIDV-123"),
    # 文件名优先于目录
    _Case("/media/cracked/MIDV-123-无码.mp4", mosaic="uncensored", number="MIDV-123"),
    _Case("/media/uncensored/MIDV-123-LEAKED.mp4", mosaic="cracked", number="MIDV-123"),
    # 近目录优先
    _Case("/media/uncensored/流出/MIDV-123.mp4", mosaic="cracked", number="MIDV-123"),
    _Case("/media/流出/uncensored/MIDV-123.mp4", mosaic="uncensored", number="MIDV-123"),
    # 马赛克目录反例: 子串 / 复合名 / 非词表 / -UC 当目录
    _Case("/media/uncensored-guide/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/JAV-uncensored/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/leaked-info/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/无码破解/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/流出物/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/UC/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/documentation/MIDV-123.mp4", number="MIDV-123"),
    # 清晰度不从目录读 (盘符/分类夹)
    _Case("/media/4K/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/1080p/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/HD/MIDV-123.mp4", number="MIDV-123"),
    _Case("/Volumes/4K/JAV/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/720p/ABC-123.mp4", number="ABC-123"),
    # 字幕不从目录读 (中文路径尤其不能用字符类)
    _Case("/media/字幕/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/中文字幕/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/中文/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/字幕/MIDV-123-C.mp4", has_subtitle=True, number="MIDV-123"),
    # --- 完整路径: 番号从父目录回退; 文件名已命中则不管目录 ---
    _Case("/media/MIDV-123/video.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    _Case("/media/SSIS-456/MIDV-123.mp4", number="MIDV-123", content_type=ContentType.CENSORED),
    _Case("/media/FC2-1234567/movie.mkv", number="FC2-1234567", content_type=ContentType.FC2),
    _Case("/media/HEYZO-1234/clip.mp4", number="HEYZO-1234", content_type=ContentType.UNCENSORED),
    _Case("/media/MIDV-123-uncensored/video.mp4", number="MIDV-123"),
    _Case("/media/Studio Name/video.mp4", number="VIDEO"),
    _Case("/media/downloads/video.mp4", number="VIDEO"),
    _Case("/media/Season02/video.mp4", number="VIDEO"),
    _Case("/media/2024-01/video.mp4", number="VIDEO"),
    _Case("/media/HDR10/video.mp4", number="VIDEO"),
    _Case("/media/DISC01/video.mp4", number="VIDEO"),
    _Case("/media/Vol.12/video.mp4", number="VIDEO"),
    _Case("/media/FC2-1111111/MIDV-123/video.mp4", number="MIDV-123"),
    _Case("/media/n1234/clip.mp4", number="n1234", content_type=ContentType.UNCENSORED),
    # --- 完整路径: 分集只认直接父目录 CDn/PARTn ---
    _Case("/media/MIDV-123/CD1/video.mp4", cd=1, number="MIDV-123"),
    _Case("/media/MIDV-123/PART2/video.mp4", cd=2, number="MIDV-123"),
    _Case("/media/cd2/MIDV-123.mp4", cd=2, number="MIDV-123"),
    _Case("/media/CD10/MIDV-123.mp4", cd=10, number="MIDV-123"),
    _Case("/media/CD01/MIDV-123.mp4", cd=1, number="MIDV-123"),
    _Case("/media/CD1/MIDV-123-CD2.mp4", cd=2, number="MIDV-123"),
    # 分集目录反例: 祖先 / 连字符 / 盘名 / -A / 裸数字 / CD0
    _Case("/media/CD1/other/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/CD-1/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/disc1/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/A/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/1/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/CD0/MIDV-123.mp4", number="MIDV-123"),
    _Case("/media/CDs/MIDV-123.mp4", number="MIDV-123"),
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
    _Case(
        "/media/lib/MIDV-123-UC-CD1.mp4",
        cd=1,
        has_subtitle=True,
        mosaic="uncensored",
        number="MIDV-123",
        content_type=ContentType.CENSORED,
    ),
    _Case(
        "/incoming/MIDV-123_4K_无码.mp4",
        mosaic="uncensored",
        definition="4K",
        number="MIDV-123",
        content_type=ContentType.CENSORED,
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


# --- 番号级谓词 / 前缀 / 分类 (无路径) ---

UNCENSORED_CASES: list[tuple[str, bool]] = [
    ("HEYZO-1234", True),
    ("S2M-001", True),
    ("CWP-123", True),
    ("n1234", True),
    ("BT-123", True),
    ("SKY-001", True),
    ("XXX-AV-12345", True),
    ("MKBD-S120", True),
    ("vixen.23.04.15", True),
    ("MIDV-123", False),
    ("SSIS-456", False),
    ("ABP-789", False),
    ("IPX-001", False),
    ("FC2-1234567", False),
]


@pytest.mark.parametrize(("number", "expected"), UNCENSORED_CASES)
def test_is_uncensored(number: str, expected: bool) -> None:
    assert is_uncensored(number) is expected


AMATEUR_CASES: list[tuple[str, bool]] = [
    ("SIRO-1234", True),
    ("LUXU-1234", True),
    ("259LUXU-1456", True),
    ("MIDV-123", False),
]


@pytest.mark.parametrize(("number", "expected"), AMATEUR_CASES)
def test_is_amateur(number: str, expected: bool) -> None:
    assert is_amateur(number) is expected


PREFIX_CASES: list[tuple[str, str]] = [
    ("MIDV-123", "MIDV"),
    ("FC2-1234567", "FC2"),
    ("HEYZO-1234", "HEYZO"),
    ("vixen.23.04.15", "VIXEN"),
    ("MKY-HS-001", "MKY-HS"),
    ("H4610-ki123456", "H4610"),
]


@pytest.mark.parametrize(("number", "expected"), PREFIX_CASES)
def test_get_prefix(number: str, expected: str) -> None:
    assert get_prefix(number) == expected


CLASSIFY_NUMBER_CASES: list[tuple[str, ContentType]] = [
    ("MIDV-123", ContentType.CENSORED),
    ("SSIS-456", ContentType.CENSORED),
    ("FC2-PPV-1234567", ContentType.FC2),
    ("vixen.23.04.15", ContentType.WESTERN),
    ("HEYZO-1234", ContentType.UNCENSORED),
    ("SIRO-4567", ContentType.AMATEUR),
    ("MD-0123", ContentType.CHINESE),
    ("MD0165-1", ContentType.CHINESE),
    ("MKY-NS-012", ContentType.CHINESE),
    ("MDVR-0123", ContentType.CENSORED),
]


@pytest.mark.parametrize(("number", "expected"), CLASSIFY_NUMBER_CASES)
def test_classify_number(number: str, expected: ContentType) -> None:
    assert classify_number(number) == expected


INFER_CONTENT_TYPE_CASES: list[tuple[str, str | None, ContentType]] = [
    ("MIDV-123", "/media/欧美/MIDV-123.mp4", ContentType.WESTERN),
    ("MIDV-123", "/media/MIDV-123.mp4", ContentType.CENSORED),
    ("MIDV-123", None, ContentType.CENSORED),
    ("MD-0123", None, ContentType.CHINESE),
]


@pytest.mark.parametrize(("number", "file_path", "expected"), INFER_CONTENT_TYPE_CASES)
def test_infer_content_type(number: str, file_path: str | None, expected: ContentType) -> None:
    assert infer_content_type(number, file_path) == expected


EXTRACT_NUMBER_CASES: list[tuple[str, str | None]] = [
    ("[4K] MIDV-123 タイトル", "MIDV-123"),
    ("SSIS-456 FHD", "SSIS-456"),
    ("FC2-PPV-1234567 新作", "FC2-1234567"),
    ("HEYZO-1234", "HEYZO-1234"),
    ("259LUXU-1456", "259LUXU-1456"),
    ("今週の新作をお届けします", None),
    ("Weekly Update", None),
    ("", None),
    ("FC2 配信開始", None),
    ("just a movie title", None),
]


@pytest.mark.parametrize(("text", "expected"), EXTRACT_NUMBER_CASES)
def test_extract_number(text: str, expected: str | None) -> None:
    assert extract_number(text) == expected


EXTRACT_VS_PATH_CASES: list[tuple[str, str]] = [
    ("just a movie title", "just a movie title.mp4"),
]


@pytest.mark.parametrize(("text", "path"), EXTRACT_VS_PATH_CASES)
def test_extract_number_skips_filename_fallback(text: str, path: str) -> None:
    assert extract_number(text) is None
    assert parse_file_info(path).number


ESCAPE_STRING_CASES: list[tuple[str, list[str], str]] = [
    ("/media/MIDV-123-4K.mp4", ["something"], "MIDV-123"),
]


@pytest.mark.parametrize(("path", "extra", "number"), ESCAPE_STRING_CASES)
def test_parse_file_info_escape_strings(path: str, extra: list[str], number: str) -> None:
    assert parse_file_info(path, escape_strings=extra).number == number
