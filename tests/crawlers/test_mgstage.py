"""mgstage 爬虫单元测试: 竖版海报地址的推导与守卫."""

import pytest

from amane.crawlers.sites.mgstage import _poster_url


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
