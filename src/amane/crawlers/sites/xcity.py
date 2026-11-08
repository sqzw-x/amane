import re
from urllib.parse import urljoin

from parsel import Selector

from ...enums import SiteName
from ...net.connectivity import ConnectivityOutcome, probe_get
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text, is_same_number

#: 检索与详情都要求站内 Referer; 缺它时站点把查询词换成默认专题再回 200, 表现为检索未命中.
_REFERER = "https://xcity.jp/main/"
#: 检索结果行只有标题, 没有品番, 只能取候选详情页的品番确认; 多查几页没有意义, 取前三条.
_SEARCH_CANDIDATES = 3


class XCityCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.XCITY, base_url="https://xcity.jp")

    async def check_connectivity(self) -> ConnectivityOutcome:
        """根路径是年龄确认页, 被推去默认专题的检索页也不能代表可达, 因此探站内主入口."""
        return await probe_get(self.client.web_client, f"{self.base_url}/main/")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        # 站内检索按品番的紧凑写法匹配, 带不带短横线都能命中.
        search_url = f"{self.base_url}/result_published/?q={query.number.replace('-', '')}&num=30"
        text = await self.client.get_html(search_url, headers=self._headers())
        html = Selector(text=text)
        links = dict.fromkeys(
            extract_all_texts(html, '//table[contains(@class,"resultList")]//a[contains(@href,"/avod/detail/")]/@href')
        )
        for href in list(links)[:_SEARCH_CANDIDATES]:
            url = urljoin(self.base_url, href)
            detail = Selector(text=await self.client.get_html(url, headers=self._headers()))
            if is_same_number(extract_text(detail, '//span[@id="hinban"]/text()'), query.number):
                return url
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url, headers=self._headers())
        html = Selector(text=text)

        title = extract_text(html, '//span[@id="program_detail_title"]/text()')
        number = extract_text(html, '//span[@id="hinban"]/text()')
        if not title or not number:
            return None

        actors = extract_all_texts(
            html, '//li[span[normalize-space()="出演"]]//a[contains(@href,"/idol/detail/")]/text()'
        )
        tags = extract_all_texts(
            html, '//li[span[normalize-space()="ジャンル"]]//a[contains(@href,"/avod/genre/")]/text()'
        )
        studio = extract_text(html, '//span[@id="program_detail_maker_name"]/text()')
        publisher = extract_text(html, '//span[@id="program_detail_label_name"]/text()')
        series = extract_text(html, '//li[span[normalize-space()="シリーズ"]]//a//text()[normalize-space()]')
        release = extract_text(html, '//li[span[normalize-space()="発売日"]]/text()[normalize-space()]')
        runtime = self._parse_runtime(
            extract_text(html, '//li[span[normalize-space()="収録時間"]]/text()[normalize-space()]')
        )
        plot = "\n".join(extract_all_texts(html, '//p[contains(@class,"lead")]//text()'))

        # 封面是 package-b 的横版大图 (锚点 href), 面板里的 img 是竖版缩略.
        cover = _absolute(extract_text(html, '//div[@class="photo"]//a[contains(@href,"/package-b/")]/@href'))
        poster = _absolute(extract_text(html, '//div[@class="photo"]//img/@src'))
        # サンプル画像是隐藏区的缩略, 同名大图在 scene/large 下.
        stills = [
            _absolute(url).replace("/scene/small/", "/scene/large/")
            for url in extract_all_texts(html, '//div[@id="sample_images"]//a[contains(@href,"/scene/")]/@href')
        ]

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(actors),
            studio=studio or None,
            publisher=publisher or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
            series=series or None,
            plot=plot or None,
            thumb_urls=[cover] if cover else [],
            poster_urls=[poster] if poster else [],
            extrafanart=stills,
            source_url=url,
        )

    def _headers(self) -> dict[str, str]:
        return {"Referer": _REFERER}

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None


def _absolute(url: str) -> str:
    """协议相对地址 (``//faws.xcity.jp/...``) 补成 https."""
    return f"https:{url}" if url.startswith("//") else url
