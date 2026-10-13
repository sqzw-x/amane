"""DMM 来源的图片候选地址与 cid → 番号.

分类目录改写、候选顺序与 cid 形态是 DMM 详情页与 r18 dump 共用的契约; 页面解析走 TOML 数据驱动.
"""

import pytest

from amane.crawlers.sites.dmm import DmmCrawler
from amane.crawlers.sites.dmm_cdn import image_candidates, poster_candidates


@pytest.mark.parametrize(
    ("image_url", "expected"),
    [
        (
            "https://pics.dmm.co.jp/mono/movie/adult/118abf295/118abf295pl.jpg",
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/118abf295/118abf295pl.jpg",
                "https://pics.dmm.co.jp/mono/movie/adult/118abf295/118abf295pl.jpg",
            ],
        ),
        (
            "https://pics.dmm.co.jp/mono/movie/118abf295r/118abf295rpl.jpg",
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/118abf295r/118abf295rpl.jpg",
                "https://pics.dmm.co.jp/mono/movie/118abf295r/118abf295rpl.jpg",
            ],
        ),
        (
            "https://pics.dmm.co.jp/mono/movie/japanese/n_617db0242/n_617db0242pl.jpg",
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/n_617db0242/n_617db0242pl.jpg",
                "https://pics.dmm.co.jp/mono/movie/japanese/n_617db0242/n_617db0242pl.jpg",
            ],
        ),
        (
            "https://pics.dmm.co.jp/digital/video/ssis00497/ssis00497pl.jpg",
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00497/ssis00497pl.jpg",
                "https://pics.dmm.co.jp/digital/video/ssis00497/ssis00497pl.jpg",
            ],
        ),
        (
            "https://pics.dmm.co.jp/digital/amateur/ako254/ako254jp.jpg",
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/digital/amateur/ako254/ako254jp.jpg",
                "https://pics.dmm.co.jp/digital/amateur/ako254/ako254jp.jpg",
            ],
        ),
        (
            "https://pics.dmm.co.jp/digital/e-book/b044appnt00019/b044appnt00019pl.jpg",
            ["https://pics.dmm.co.jp/digital/e-book/b044appnt00019/b044appnt00019pl.jpg"],
        ),
        ("https://pics.dmm.co.jp/p/ms/top/pic_packing.jpg", ["https://pics.dmm.co.jp/p/ms/top/pic_packing.jpg"]),
        # 已在高清 CDN 上的地址不重复推导.
        (
            "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/118abf295/118abf295pl.jpg",
            ["https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/118abf295/118abf295pl.jpg"],
        ),
        # 缩放查询串返回缩略图, 高清与标准地址都去掉.
        (
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00497/ssis00497pl.jpg?w=384&h=288&t=margin&f=webp",
            ["https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00497/ssis00497pl.jpg"],
        ),
        (
            "https://pics.dmm.co.jp/mono/movie/adult/118abf295/118abf295pl.jpg?w=384&h=288",
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/118abf295/118abf295pl.jpg",
                "https://pics.dmm.co.jp/mono/movie/adult/118abf295/118abf295pl.jpg",
            ],
        ),
        ("https://javdb.com/cover.jpg", ["https://javdb.com/cover.jpg"]),
        # 段数不足的异常路径仍构造高清候选, 由下载期按序回退.
        (
            "https://pics.dmm.co.jp/mono/movie/broken.jpg",
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/broken.jpg",
                "https://pics.dmm.co.jp/mono/movie/broken.jpg",
            ],
        ),
    ],
)
def test_image_candidates(image_url: str, expected: list[str]) -> None:
    assert image_candidates(image_url) == expected


@pytest.mark.parametrize(
    ("cover_url", "expected"),
    [
        (
            "https://pics.dmm.co.jp/mono/movie/adult/118abf295/118abf295pl.jpg",
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/118abf295/118abf295ps.jpg",
                "https://pics.dmm.co.jp/mono/movie/adult/118abf295/118abf295ps.jpg",
            ],
        ),
        # 已在高清 CDN 上的封面同样只换成 ps 形态.
        (
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00497/ssis00497pl.jpg",
            ["https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00497/ssis00497ps.jpg"],
        ),
        (
            "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/118abf295/118abf295pl.jpg?w=384&h=288&t=margin&f=webp",
            ["https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/118abf295/118abf295ps.jpg"],
        ),
        # 非 pl 形态 (含 ps 本身) 不给海报候选.
        ("https://pics.dmm.co.jp/mono/movie/adult/118abf295/118abf295ps.jpg", []),
        ("https://pics.dmm.co.jp/digital/video/ssis00497/ssis00497jp-1.jpg", []),
        ("", []),
    ],
)
def test_poster_candidates(cover_url: str, expected: list[str]) -> None:
    assert poster_candidates(cover_url) == expected


@pytest.mark.parametrize(
    ("cid", "retail", "expected"),
    [
        # 通販 / レンタル cid 在番号前带厂商代码, 番号按原位数保留.
        ("118abf295", True, "ABF-295"),
        ("118abf295r", True, "ABF-295"),
        ("118abf007r", True, "ABF-007"),
        ("1dldss542", True, "DLDSS-542"),
        ("1sods098", True, "SODS-098"),
        ("9spone001", True, "SPONE-001"),
        ("125umd1029", True, "UMD-1029"),
        ("h_1059godr1287", True, "GODR-1287"),
        # FANZA 限定版标记与番号后缀不参与番号.
        ("d2gvh891", True, "GVH-891"),
        ("1fns264tk1", True, "FNS-264"),
        ("1sgki108v", True, "SGKI-108"),
        # 配信 cid 无厂商代码, 编号是 5 位补零.
        ("ssis00497", False, "SSIS-497"),
        ("sora00652a", False, "SORA-652"),
        ("adn00431", False, "ADN-431"),
        # 数字开头的番号只出现在配信柜台.
        ("13dsvr01968", False, "13DSVR-1968"),
        # 无法判定时返回 cid 大写.
        ("abc", True, "ABC"),
        ("118", True, "118"),
        ("", True, ""),
    ],
)
def test_cid_to_number(cid: str, retail: bool, expected: str) -> None:
    assert DmmCrawler._cid_to_number(cid, retail=retail) == expected
