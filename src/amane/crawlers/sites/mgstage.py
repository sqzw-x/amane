import re
from html import unescape as html_unescape

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text, number_variants, select_number_index

#: 竖版海报与横版放大图同目录同名, 只差文件名前缀; 详情页只引用后者, 海报地址由封面推导.
_COVER_PREFIX = "pb_e_"
_POSTER_PREFIX = "pf_e_"
#: 素人条目的出演栏写的是站点的代名 (名字后跟年龄与职业), 不是可对齐的演员身份.
_AMATEUR_TAG = "素人"
#: 番号尾部的数字段, 供站点多写了一段前导数字时反查.
_TAIL_DIGITS = re.compile(r"(\d+)\s*$")
#: 详情页只引用播放器页, pid 在其路径末段; 真正的 mp4 地址要再用 pid 换一次.
_SAMPLE_PID = re.compile(r"/sampleplayer/sampleplayer\.html/([^/?#\"']+)")
_SAMPLE_RESPONSE = "https://www.mgstage.com/sampleplayer/sampleRespons.php?pid={pid}"
#: 站点播放器脚本把 ``.ism/request?...`` 改写成 ``.mp4``; 服务端拿到的是播放列表形态.
_ISM_REQUEST = re.compile(r"\.ism/request.*$")


def _poster_url(cover_url: str) -> str | None:
    """横版封面地址 → 同目录竖版海报地址; 非 ``pb_e_`` 形态返回 ``None``."""
    directory, _, filename = cover_url.rpartition("/")
    if not directory or not filename.startswith(_COVER_PREFIX):
        return None
    return f"{directory}/{_POSTER_PREFIX}{filename.removeprefix(_COVER_PREFIX)}"


def _sample_mp4(url: str) -> str:
    """播放器声明的样品地址 → 可直接下载的 mp4; 站点在 JSON 里把 ``&`` 转义成 ``&amp;``."""
    resolved = _ISM_REQUEST.sub(".mp4", html_unescape(url))
    # 早期条目在 ``dl`` 子域, 播放器脚本同样换成 ``chdl``.
    return resolved.replace("http://dl", "http://chdl")


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
        # 站点在番号前多写一段数字时 (529STCV-605 对 STCV-605) 上面几条都落空: 站内检索只做
        # token 前缀匹配 (529 / 529STCV / 605 能查到, STCV 查不到), 前缀又无法推导, 因此用尾号再检一次.
        tail = _TAIL_DIGITS.search(query.number)
        if tail is not None and tail.group(1) != query.number:
            return await self._search_keyword(tail.group(1), query.number)
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
        return await self._search_keyword(number, query_number)

    async def _search_keyword(self, number: str, query_number: str) -> str | None:
        # 检索结果按条目声明的番号确认, 不取首条.
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

        tags = extract_all_texts(html, '//th[contains(text(),"ジャンル")]/following-sibling::td/a/text()')

        # 素人条目的出演值是站点代名; 演员字段按首个非空来源锁定, 写入即顶掉 javdb 记的本人, 故不取.
        actors = (
            []
            if any(_AMATEUR_TAG in tag for tag in tags)
            else extract_all_texts(html, '//th[contains(text(),"出演")]/following-sibling::td/a/text()')
        )
        studio = extract_text(html, '//th[contains(text(),"メーカー")]/following-sibling::td/a/text()')
        publisher = extract_text(html, '//th[contains(text(),"レーベル")]/following-sibling::td/a/text()')
        series = extract_text(html, '//th[contains(text(),"シリーズ")]/following-sibling::td/a/text()')

        runtime_raw = extract_text(html, '//th[contains(text(),"収録時間")]/following-sibling::td/text()')
        runtime = self._parse_runtime(runtime_raw)

        release_raw = extract_text(html, '//th[contains(text(),"配信開始日")]/following-sibling::td/text()')
        release = release_raw.strip() if release_raw else None

        cover = extract_text(html, '//a[@id="EnlargeImage"]/@href', '//img[@class="enlarge_image"]/@src')
        thumb_url = cover or None
        poster_url = _poster_url(cover)

        extrafanart = extract_all_texts(html, '//dl[@id="sample-photo"]//li/a/@href')

        # 样品片需要再换一次地址; 取不到时为空, 不阻断其余字段.
        trailer_urls = await self._trailer_urls(html)

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
            trailer_urls=trailer_urls,
            extrafanart=extrafanart,
            source_url=url,
            external_id=url,
        )

    async def _trailer_urls(self, html: Selector) -> list[str]:
        """预告片.

        详情页只给出播放器页地址, 真正的 mp4 要拿 pid 换 ``sampleRespons.php``; 站点播放器脚本
        再把 ``.ism/request?...`` 改写成 ``.mp4``, 这层改写必须在服务端完成, 否则拿到的是播放列表.
        早期条目没有样品, 不按命名规则构造地址.
        """
        href = extract_text(html, '//p[contains(@class, "sample_movie_btn")]/a/@href')
        found = _SAMPLE_PID.search(href or "")
        if found is None:
            return []
        try:
            payload = await self.client.get_json(_SAMPLE_RESPONSE.format(pid=found[1]), cookies=self.cookies)
        except RequestError:
            # 样品是附加资源: 取不到按没有处理, 不让整条刮削失败.
            self.logger.debug("sample player unavailable", pid=found[1])
            return []
        if not isinstance(payload, dict):
            return []
        url = payload.get("url")
        return [_sample_mp4(url)] if isinstance(url, str) and url else []

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
