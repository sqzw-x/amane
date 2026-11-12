"""avbase 图片候选链的边界分支: 地址去重 / 仅海报商品 / 无图字段 / DMM 海报推导."""

from __future__ import annotations

from typing import Any

import pytest

from amane.crawlers.sites.avbase import _build_images

MGS_COVER = "https://image.mgstage.com/images/sample/pake.jpg"
MGS_POSTER = "https://image.mgstage.com/images/sample/h1.jpg"


@pytest.mark.parametrize(
    ("products", "expected_poster", "expected_thumb"),
    [
        # 同一地址只在链里出现一次.
        (
            [
                {"image_url": MGS_COVER, "thumbnail_url": MGS_POSTER},
                {"image_url": MGS_COVER},
            ],
            [MGS_POSTER],
            [MGS_COVER],
        ),
        # DMM 封面按 pl → ps 推导海报, 两者各自带高清候选.
        (
            [
                {
                    "image_url": "https://pics.dmm.co.jp/digital/video/abc001/abc001pl.jpg",
                    "thumbnail_url": "https://pics.dmm.co.jp/digital/video/abc001/abc001ps.jpg",
                }
            ],
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/abc001/abc001ps.jpg",
                "https://pics.dmm.co.jp/digital/video/abc001/abc001ps.jpg",
            ],
            [
                "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/abc001/abc001pl.jpg",
                "https://pics.dmm.co.jp/digital/video/abc001/abc001pl.jpg",
            ],
        ),
        # 只有海报的商品只补海报链.
        (
            [{"thumbnail_url": MGS_POSTER}],
            [MGS_POSTER],
            [],
        ),
    ],
)
def test_build_images_edges(
    products: list[dict[str, Any]], expected_poster: list[str], expected_thumb: list[str]
) -> None:
    poster, thumb, _ = _build_images(products)
    assert poster == expected_poster
    assert thumb == expected_thumb


def test_no_image_fields_returns_empty() -> None:
    products = [{"sample_image_urls": [{"l": "https://pic.duga.jp/u/sample/1/cap/1.jpg"}]}]
    assert _build_images(products) == ([], [], [])
