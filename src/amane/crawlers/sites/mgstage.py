import re

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text, number_variants, select_number_index

#: 竖版海报与横版放大图同目录同名, 只差文件名前缀; 详情页只引用后者, 海报地址由封面推导.
_COVER_PREFIX = "pb_e_"
_POSTER_PREFIX = "pf_e_"


def _poster_url(cover_url: str) -> str | None:
    """横版封面地址 → 同目录竖版海报地址; 非 ``pb_e_`` 形态返回 ``None``."""
    directory, _, filename = cover_url.rpartition("/")
    if not directory or not filename.startswith(_COVER_PREFIX):
        return None
    return f"{directory}/{_POSTER_PREFIX}{filename.removeprefix(_COVER_PREFIX)}"


class MGStageCrawler(Crawler):
    #: 详情页地址里的番号段, 检索结果与直达地址共用同一形态.
    _DETAIL_PATH = re.compile(r"/product/product_detail/([^/]+)/")

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.MGSTAGE, base_url="https://www.mgstage.com", cookies={"adc": "1"})

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """站内品番逐部不同地写不写开头的数字段, 因此按 ``number_variants`` 逐个形态尝试."""
        for number in number_variants(query.number):
            url = await self._search_number(number, query.number)
            if url is not None:
                return url
        return None

    async def _search_number(self, number: str, query_number: str) -> str | None:
        # 先尝试直接访问详情页.
        direct_url = f"{self.base_url}/product/product_detail/{number}/"
        try:
            text = await self.client.get_html(direct_url, cookies=self.cookies)
        except RequestError:
            text = None
        if text:
            html = Selector(text=text)
            title = extract_text(html, '//*[@id="center_column"]/div[1]/h1/text()')
            if title:
                return direct_url

        # 未命中则回退搜索; 结果按条目声明的番号确认, 不取首条.
        search_url = f"{self.base_url}/search/cSearch.php?search_word={number}"
        text = await self.client.get_html(search_url, cookies=self.cookies)
        html = Selector(text=text)
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw in html.xpath('//a[contains(@href, "/product/product_detail/")]/@href').getall():
            url = f"{self.base_url}{raw}" if raw.startswith("/") else raw
            match = self._DETAIL_PATH.search(url)
            if match is None or url in seen:
                continue
            seen.add(url)
            candidates.append((url, match[1]))
        index = select_number_index([found for _, found in candidates], query_number)
        return candidates[index][0] if index is not None else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//*[@id="center_column"]/div[1]/h1/text()')
        if not title:
            return None

        number_match = re.search(r"/product_detail/([^/]+)", url)
        number = number_match.group(1) if number_match else ""

        actors = extract_all_texts(html, '//th[contains(text(),"出演")]/following-sibling::td/a/text()')
        studio = extract_text(html, '//th[contains(text(),"メーカー")]/following-sibling::td/a/text()')
        publisher = extract_text(html, '//th[contains(text(),"レーベル")]/following-sibling::td/a/text()')
        series = extract_text(html, '//th[contains(text(),"シリーズ")]/following-sibling::td/a/text()')

        runtime_raw = extract_text(html, '//th[contains(text(),"収録時間")]/following-sibling::td/text()')
        runtime = self._parse_runtime(runtime_raw)

        release_raw = extract_text(html, '//th[contains(text(),"配信開始日")]/following-sibling::td/text()')
        release = release_raw.strip() if release_raw else None

        tags = extract_all_texts(html, '//th[contains(text(),"ジャンル")]/following-sibling::td/a/text()')

        cover = extract_text(html, '//a[@id="EnlargeImage"]/@href', '//img[@class="enlarge_image"]/@src')
        thumb_url = cover or None
        poster_url = _poster_url(cover)

        extrafanart = extract_all_texts(html, '//dl[@id="sample-photo"]//li/a/@href')

        # 商品紹介的正文有两种标记 (直接在 p.txt.introduction 内, 或嵌在其中的 <p> 被解析器拍平为兄弟节点),
        # 两者都收, 只排除「すべてを見る」.
        plot = "\n".join(
            extract_all_texts(html, '//dl[@id="introduction"]/dd/p[not(contains(@class, "more"))]//text()')
        )

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(actors),
            studio=studio or None,
            publisher=publisher or None,
            release=release,
            runtime=runtime,
            tags=tags,
            series=series or None,
            plot=plot or None,
            poster_urls=[poster_url] if poster_url else [],
            thumb_urls=[thumb_url] if thumb_url else [],
            extrafanart=extrafanart,
            source_url=url,
            external_id=url,
        )

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
