"""r18dev 爬虫单元测试.

测试图片 URL 补全, gallery 生成, 番号候选变体等纯函数逻辑.
因 r18dev 是无 HTTP 的 SQL 爬虫, 不使用 TOML 数据驱动, 直接测试映射层.
"""

from amane.crawlers.r18dev.mapper import _generate_gallery, to_metadata
from amane.crawlers.r18dev.models import R18Video, R18VideoDetail
from amane.crawlers.r18dev.repository import content_id_candidates

# --- 封面与海报 ---


class TestJacketUrls:
    def test_full_becomes_thumb_and_thumb_becomes_poster(self):
        """jacket_full 作封面, jacket_thumb 作海报, 两者都补高清候选."""
        video = R18Video(
            content_id="ssis00497",
            jacket_full_url="digital/video/ssis00497/ssis00497pl",
            jacket_thumb_url="digital/video/ssis00497/ssis00497ps",
        )
        meta = to_metadata(R18VideoDetail(video=video), "SSIS-497")
        assert meta.thumb_urls == [
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00497/ssis00497pl.jpg",
            "https://pics.dmm.co.jp/digital/video/ssis00497/ssis00497pl.jpg",
        ]
        assert meta.poster_urls == [
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00497/ssis00497ps.jpg",
            "https://pics.dmm.co.jp/digital/video/ssis00497/ssis00497ps.jpg",
        ]

    def test_missing_full_falls_back_to_thumb(self):
        """jacket_full 为空时封面与海报都取 jacket_thumb."""
        video = R18Video(content_id="n_617db0242", jacket_thumb_url="mono/movie/japanese/n_617db0242/n_617db0242pl")
        meta = to_metadata(R18VideoDetail(video=video), "N-617DB0242")
        expected = [
            "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/n_617db0242/n_617db0242pl.jpg",
            "https://pics.dmm.co.jp/mono/movie/japanese/n_617db0242/n_617db0242pl.jpg",
        ]
        assert meta.thumb_urls == expected
        assert meta.poster_urls == expected

    def test_missing_both_gives_empty_lists(self):
        """两列都为空时不给候选, 不产生空串地址."""
        meta = to_metadata(R18VideoDetail(video=R18Video(content_id="ssis00497")), "SSIS-497")
        assert meta.thumb_urls == []
        assert meta.poster_urls == []


# --- _generate_gallery ---


class TestGenerateGallery:
    def test_sequential_with_fallback(self):
        """正常顺序 first=-1 last=-3 → 3 张, 标准 CDN 单 URL (无 AWS)."""
        urls = _generate_gallery("digital/video/ssis00497/ssis00497jp-1", "digital/video/ssis00497/ssis00497jp-3")
        assert len(urls) == 3
        assert urls[0] == "https://pics.dmm.co.jp/digital/video/ssis00497/ssis00497jp-1.jpg"
        assert urls[2] == "https://pics.dmm.co.jp/digital/video/ssis00497/ssis00497jp-3.jpg"

    def test_single_image(self):
        """first=-1 last=-1 → 1 张标准 CDN URL."""
        urls = _generate_gallery("digital/video/abf00355/abf00355jp-1", "digital/video/abf00355/abf00355jp-1")
        assert len(urls) == 1
        assert urls[0] == "https://pics.dmm.co.jp/digital/video/abf00355/abf00355jp-1.jpg"

    def test_zero_last_fallback(self):
        """last=-0 (异常标记) → 仅保留 first, 标准 CDN."""
        urls = _generate_gallery("digital/video/41dmc00001/41dmc00001-1", "digital/video/41dmc00001/41dmc00001-0")
        assert len(urls) == 1
        assert urls[0] == "https://pics.dmm.co.jp/digital/video/41dmc00001/41dmc00001-1.jpg"

    def test_first_none_returns_empty(self):
        assert _generate_gallery(None, "anything") == []

    def test_last_none_fallback_to_first_only(self):
        urls = _generate_gallery("digital/video/test/testjp-1", None)
        assert len(urls) == 1
        assert urls[0] == "https://pics.dmm.co.jp/digital/video/test/testjp-1.jpg"

    def test_unparseable_first_fallback(self):
        """编号不可解析时返回单个标准 CDN URL."""
        urls = _generate_gallery("digital/video/test/testpl", "digital/video/test/testpl")
        assert len(urls) == 1
        assert urls[0] == "https://pics.dmm.co.jp/digital/video/test/testpl.jpg"

    def test_mono_gallery_single_url_per_image(self):
        """mono 路径使用标准 CDN, 无 AWS 候选."""
        urls = _generate_gallery("mono/movie/test/test-1", "mono/movie/test/test-3")
        assert len(urls) == 3
        assert urls == [
            "https://pics.dmm.co.jp/mono/movie/test/test-1.jpg",
            "https://pics.dmm.co.jp/mono/movie/test/test-2.jpg",
            "https://pics.dmm.co.jp/mono/movie/test/test-3.jpg",
        ]


# --- content_id_candidates ---


class TestContentIdCandidates:
    def test_standard_number(self):
        result = content_id_candidates("MIDV-123")
        assert "midv00123" in result
        assert "midv123" in result
        assert len(result) == 2

    def test_zero_padded_number(self):
        result = content_id_candidates("MLDE-013")
        assert "mlde00013" in result
        assert "mlde013" in result
        assert "mlde13" in result
        assert result[0] == "mlde00013"
        assert result[1] == "mlde013"
        assert result[2] == "mlde13"

    def test_two_digit_zero_padded(self):
        result = content_id_candidates("ZSND-01")
        assert "zsnd00001" in result
        assert "zsnd01" in result
        assert "zsnd1" in result

    def test_no_prefix(self):
        assert content_id_candidates("12345") == []

    def test_lowercase_prefix(self):
        result = content_id_candidates("Mide-013")
        assert result[0] == "mide00013"

    def test_no_zero_padding_when_not_needed(self):
        result = content_id_candidates("ABC-456")
        assert len(result) == 2
