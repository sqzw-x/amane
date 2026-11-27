"""mgstage 爬虫单元测试: 竖版海报推导, 商品目录索引的解析与去歧义."""

import gzip

import pytest

from amane.crawlers.sites.mgstage import CatalogUnusable, _poster_url, parse_catalog


def _urlset(*numbers: str) -> bytes:
    body = "".join(f"<url><loc>https://www.mgstage.com/product/product_detail/{n}/</loc></url>" for n in numbers)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    ).encode()


@pytest.mark.parametrize(
    ("cover_url", "expected"),
    [
        (
            "https://image.mgstage.com/images/prestige/abf/355/pb_e_abf-355.jpg",
            "https://image.mgstage.com/images/prestige/abf/355/pf_e_abf-355.jpg",
        ),
        (
            "https://image.mgstage.com/images/shirouto/siro/3759/pb_e_siro-3759.jpg",
            "https://image.mgstage.com/images/shirouto/siro/3759/pf_e_siro-3759.jpg",
        ),
        # 详情页的图片兜底位给的不是横版放大图, 这些形态不推导.
        ("https://image.mgstage.com/images/prestige/abf/355/pf_o1_abf-355.jpg", None),
        ("https://image.mgstage.com/images/shirouto/siro/3759/pb_p_siro-3759.jpg", None),
        ("pb_e_abf-355.jpg", None),
        ("", None),
    ],
)
def test_poster_url(cover_url: str, expected: str | None) -> None:
    assert _poster_url(cover_url) == expected


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        # 开头带数字段: 去掉数字段后可对齐裸番号写法.
        ("STKO-005", "107STKO-005"),
        ("107STKO-005", "107STKO-005"),
        # 裸番号不进索引: 由直连那一档处理.
        ("ABF-355", None),
        ("SIRO-3759", None),
    ],
)
def test_catalog_keys_on_tail(number: str, expected: str | None) -> None:
    index = parse_catalog([_urlset("107STKO-005", "ABF-355", "SIRO-3759")])
    found = index.candidates(number)
    assert (found[0] if found else None) == expected


def test_catalog_keeps_ambiguous_entries() -> None:
    """同一尾号在站点上确有两部作品时两条都保留, 由调用方判为无法确认而不是取其一."""
    index = parse_catalog([_urlset("483SGK-004", "902SGK-004")])
    assert index.candidates("SGK-004") == ("483SGK-004", "902SGK-004")
    assert index.candidates("483SGK-004") == ("483SGK-004", "902SGK-004")


def test_catalog_merges_shards() -> None:
    index = parse_catalog([_urlset("107STKO-005"), _urlset("107STARS-016")])
    assert index.candidates("STKO-005") == ("107STKO-005",)
    assert index.candidates("STARS-016") == ("107STARS-016",)


def test_catalog_accepts_gzip_shard() -> None:
    """目录端点按 application/x-gzip 原样下发."""
    assert parse_catalog([gzip.compress(_urlset("107STKO-005"))]).candidates("STKO-005") == ("107STKO-005",)


@pytest.mark.parametrize(
    ("payloads", "message"),
    [
        # 不是 sitemap (拦截页 / 首页) 或分片损坏: 这一档不可用, 调用方退到直连与检索.
        ([b"<html><body>maintenance</body></html>"], "no <loc>"),
        ([b"\x1f\x8bnot a gzip stream"], "not valid gzip"),
        ([], "no sitemap shard"),
    ],
)
def test_catalog_rejects_unusable(payloads: list[bytes], message: str) -> None:
    with pytest.raises(CatalogUnusable, match=message):
        parse_catalog(payloads)


def test_catalog_without_leading_digits_is_empty_not_unusable() -> None:
    """目录全都是裸番号时索引为空, 不是失败: 那是「目录里没有这类番号」."""
    index = parse_catalog([_urlset("ABF-355", "SIRO-3759")])
    assert index.candidates("ABF-355") == ()
