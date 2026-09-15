from ...enums import SiteName
from ..base import CrawlerProfile
from .wp_works import WordPressWorksCrawler


class DahliaCrawler(WordPressWorksCrawler):
    """DAHLIA 官网. 站内只有单一厂牌, 片商与发行商同名."""

    studio = "DAHLIA"
    default_publisher = "DAHLIA"

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.DAHLIA, base_url="https://dahlia-av.jp")
