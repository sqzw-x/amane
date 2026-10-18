import re
from urllib.parse import quote, urljoin

from parsel import Selector

from ...enums import ActorGender, SiteName
from ...net.errors import FailureReason, SourceError
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, FilmActor, MediaMetadata, SearchQuery
from ..parsing import extract_all_texts, extract_text, parse_western_number, select_number_index


def _parse_actors(html: Selector) -> list[FilmActor]:
    """演員栏: ``a.actor-female`` 为女优; 同栏无该 class 的 ``a`` 为男优."""
    container = html.xpath('//strong[contains(text(),"演員")]/following-sibling::span[contains(@class,"value")][1]')
    if not container:
        return []
    actors: list[FilmActor] = []
    for node in container[0].xpath("./a"):
        name = (node.xpath("string()").get() or "").strip()
        if not name:
            continue
        classes = (node.root.get("class") or "").split()
        gender = ActorGender.FEMALE if "actor-female" in classes else ActorGender.MALE
        actors.append(FilmActor(name=name, gender=gender))
    return actors


def reject_login_page(html: Selector, *, url: str) -> None:
    """无码条目与无码演员档案要求登录: 未登录时站点重定向到登录页, 状态码与正常页同为 200.

    判据取登录表单自身 (会话表单的动作地址与密码框). 公开页面 (搜索页, 有码详情页) 的导航同样带「登入」
    字样, 按文案判定会误伤; 只认动作地址则可能把带注销控件的已登录页面判成登录页.
    """
    if html.xpath('//form[contains(@action, "user_sessions")]//input[@type="password"]'):
        raise SourceError(
            FailureReason.LOGIN_REQUIRED,
            url=url,
            detail="scraping.site_config.javdb.cookie",
        )


class JavDBCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.JAVDB, base_url="https://javdb.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """结果番号取自条目声明的 ``div.video-title/strong``, 与入参同一部才命中.

        站点的番号恒省略开头的数字段, 由 ``select_number_index`` 的宽松一档承接. 欧美日期号在站内两套
        年份写法并存, 检索词只命中其中一套, 因此未命中时用另一种写法再检索一次.
        """
        number = query.number
        terms = [number]
        western = parse_western_number(number)
        if western is not None and western.alternate != number:
            terms.append(western.alternate)

        for term in terms:
            url = await self._search_term(term, number)
            if url is not None:
                return url
        return None

    async def _search_term(self, term: str, number: str) -> str | None:
        search_url = f"{self.base_url}/search?q={quote(term)}&locale=zh"
        text = await self.client.get_html(search_url, cookies=self.cookies)
        html = Selector(text=text)
        reject_login_page(html, url=search_url)

        candidates = [
            (href, found)
            for item in html.xpath("//a[@class='box']")
            if (href := extract_text(item, "@href"))
            and (found := extract_text(item, "div[@class='video-title']/strong/text()"))
        ]
        index = select_number_index([found for _, found in candidates], number)
        return urljoin(self.base_url, candidates[index][0]) if index is not None else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        html = Selector(text=text)
        reject_login_page(html, url=url)

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
