from collections.abc import Mapping
from typing import ClassVar

from ...enums import SiteName
from ..base import CrawlerProfile
from .wp_works import WordPressWorksCrawler


class FalenoCrawler(WordPressWorksCrawler):
    """FALENO 集团官网. 同站还发布 maryGOLD 与 JimmyScandal 番号, 发行商按番号前缀判定."""

    rest_prefix = "/top"
    studio = "FALENO"
    default_publisher = "FALENOstar"
    publisher_prefixes: ClassVar[Mapping[str, str]] = {"mgold": "maryGOLD", "jimmy": "JimmyScandal"}

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.FALENO, base_url="https://faleno.jp")
