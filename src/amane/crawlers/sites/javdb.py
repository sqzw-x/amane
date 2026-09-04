import re
from urllib.parse import urljoin

from parsel import Selector

from ...enums import ActorGender, SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, FilmActor, MediaMetadata, SearchQuery
from ..parsing import extract_all_texts, extract_text


def _parse_actors(html: Selector) -> list[FilmActor]:
    """演員栏: ``<a>名</a><strong class="symbol female|male">``. 标记在名字之后, 同容器内男女并存."""
    container = html.xpath('//strong[contains(text(),"演員")]/following-sibling::span[contains(@class,"value")][1]')
    if not container:
        return []
    actors: list[FilmActor] = []
    pending: str | None = None
    for node in container[0].xpath("./a | ./strong"):
        tag = node.root.tag
        if tag == "a":
            if pending:
                actors.append(FilmActor(name=pending))
            name = (node.xpath("string()").get() or "").strip()
            pending = name or None
            continue
        if tag == "strong" and pending:
            classes = node.root.get("class") or ""
            gender = ActorGender.UNKNOWN
            if "female" in classes.split():
                gender = ActorGender.FEMALE
            elif "male" in classes.split():
                gender = ActorGender.MALE
            actors.append(FilmActor(name=pending, gender=gender))
            pending = None
    if pending:
        actors.append(FilmActor(name=pending))
    return actors


class JavDBCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.JAVDB, base_url="https://javdb.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        search_url = f"{self.base_url}/search?q={number}&locale=zh"
        text = await self.client.get_html(search_url, cookies=self.cookies)
        html = Selector(text=text)

        results = html.xpath("//a[@class='box']")
        if not results:
            return None

        entries = []
        for item in results:
            href = extract_text(item, "@href")
            title = extract_text(item, "div[@class='video-title']/strong/text()")
            meta = extract_text(item, "div[@class='meta']/text()")
            if href:
                entries.append((href, title, meta))

        # 精确匹配番号.
        for href, title, meta in entries:
            if number.upper() in title.upper():
                return urljoin(self.base_url, href)

        # 去掉分隔符后再匹配.
        clean_number = number.upper().replace(".", "").replace("-", "").replace(" ", "")
        for href, title, meta in entries:
            clean_content = (title + meta).upper().replace("-", "").replace(".", "").replace(" ", "")
            if clean_number in clean_content:
                return urljoin(self.base_url, href)

        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        html = Selector(text=text)

        number = extract_text(html, '//a[@class="button is-white copy-to-clipboard"]/@data-clipboard-text')
        if not number:
            return None

        title = extract_text(html, 'string(//h2[@class="title is-4"]/strong[@class="current-title"])')

        studio = extract_text(
            html,
            '//strong[contains(text(),"片商:")]/following-sibling::span/a/text()',
            '//strong[contains(text(),"片商:")]/../span/a/text()',
        )
        publisher = extract_text(
            html,
            '//strong[contains(text(),"發行:")]/following-sibling::span/a/text()',
            '//strong[contains(text(),"發行:")]/../span/a/text()',
        )
        runtime_str = extract_text(
            html,
            '//strong[contains(text(),"時長")]/following-sibling::span/text()',
            '//strong[contains(text(),"時長")]/../span/text()',
        )
        runtime = self._parse_runtime(runtime_str)

        release = extract_text(
            html,
            '//strong[contains(text(),"日期:")]/following-sibling::span/text()',
            '//strong[contains(text(),"日期:")]/../span/text()',
        )
        series = extract_text(
            html,
            '//strong[contains(text(),"系列:")]/following-sibling::span/a/text()',
            '//strong[contains(text(),"系列:")]/../span/a/text()',
        )
        directors = extract_all_texts(
            html,
            '//strong[contains(text(),"導演:")]/following-sibling::span/a/text()',
            '//strong[contains(text(),"導演:")]/../span/a/text()',
        )
        tags = extract_all_texts(
            html,
            '//strong[contains(text(),"類別:")]/following-sibling::span/a/text()',
            '//strong[contains(text(),"類別:")]/../span/a/text()',
        )
        tags = [t.strip() for t in tags if t.strip()]

        actors = _parse_actors(html)

        thumb_url = extract_text(html, "//img[@class='video-cover']/@src")

        score_text = extract_text(html, "//span[@class='score-stars']/following-sibling::text()[1]")
        score = self._parse_score(score_text)

        extrafanart = extract_all_texts(html, "//div[@class='tile-images preview-images']/a[@class='tile-item']/@href")
        trailer_url = extract_text(html, "//video[@id='preview-video']/source/@src")

        return MediaMetadata(
            number=number,
            title=title,
            actors=actors,
            studio=studio or None,
            publisher=publisher or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
            series=series or None,
            directors=directors,
            thumb_urls=[thumb_url] if thumb_url else [],
            score=score,
            extrafanart=extrafanart,
            trailer_urls=[trailer_url] if trailer_url else [],
            source_url=url,
            external_id=url,
        )

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_score(text: str) -> float | None:
        if not text:
            return None
        match = re.search(r"(\d+\.?\d*)", text)
        return float(match.group(1)) if match else None
