import re
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from parsel import Selector

from ...enums import SiteName
from ...net.errors import FailureReason, SourceError
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text

if TYPE_CHECKING:
    from typing import Protocol

    from ...config import SiteConfig
    from ..http import HttpClient

    class _ResponseHeaders(Protocol):
        """响应头的取值面: 出站通道的 ``Headers`` 与离线回放的响应替代实现都满足."""

        def get_list(self, key: str) -> list[str | None]: ...


# 站点把搜索结果放在 ``div.thumBox`` 内, 条目的商品链接固定指向商品页; 锚点顺序不固定, 按前缀过滤.
_PRODUCT_PATH = "/product/index.php"
# 年龄确认入口: 没领到它的会话时, 检索与商品页都换成年龄页 (状态码仍是 200).
_AGE_GATE_PATH = "/cookie_set.php"
_SESSION_COOKIE = "PHPSESSID"
# 入口带着旧会话访问时只删掉它, 那一次不下发会话; 因此领到会话最多要两次请求.
_AGE_GATE_ATTEMPTS = 2


def _is_age_gate(text: str) -> bool:
    """响应是不是年龄页. 它整页换成确认表单, 指向入口的「はい」链接是它与内容页的区别.

    站点对未确认的会话以 200 回年龄页, 与检索页同状态码; 零结果页同样没有商品链接, 因此判据取入口链接.
    """
    return bool(Selector(text=text).xpath(f'//a[contains(@href, "{_AGE_GATE_PATH}")]'))


def _issued_session(headers: _ResponseHeaders) -> bool:
    """入口响应是否下发了会话 cookie; 请求带着旧会话时它只回 ``PHPSESSID=deleted`` 与确认标记."""
    issued = f"{_SESSION_COOKIE}="
    deleted = f"{_SESSION_COOKIE}=deleted"
    for header in headers.get_list("set-cookie"):
        if header is not None and header.startswith(issued) and not header.startswith(deleted):
            return True
    return False


class GigaCrawler(Crawler):
    def __init__(self, client: HttpClient, config: SiteConfig | None = None) -> None:
        super().__init__(client, config=config)
        # 领取会换掉站点当前会话, 因此每个实例只主动领一次; 会话失效由响应里的年龄页触发重领.
        self._session_claimed = False

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.GIGA, base_url="https://www.giga-web.jp")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        await self._ensure_session()
        text = await self._get_html(f"{self.base_url}/search/?keyword={query.number}")
        if not text:
            return None

        html = Selector(text=text)
        results = html.xpath('//div[contains(@class,"thumBox")]//a/@href').getall()
        urls = [urljoin(self.base_url, r) for r in results if _PRODUCT_PATH in r]
        return urls[0] if urls else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        await self._ensure_session()
        text = await self._get_html(url)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//div[@id="works_pic"]//h5/text()')
        if not title:
            return None

        actors = extract_all_texts(html, '//div[@id="works_txt"]//a[contains(@href,"actor_id")]/text()')
        cover = extract_text(html, '//div[@id="works_pic"]//img/@src')
        extrafanart = extract_all_texts(html, '//div[contains(@class,"gasatsu_images_pc")]//img/@src')

        return MediaMetadata(
            number=extract_text(html, self._work_field("作品番号")),
            title=title,
            actors=film_actors(actors),
            studio="GIGA",
            release=extract_text(html, self._work_field("DVDリリース日")) or None,
            runtime=self._parse_runtime(extract_text(html, self._work_field("収録時間"))),
            thumb_urls=[urljoin(url, cover)] if cover else [],
            extrafanart=[urljoin(url, u) for u in extrafanart],
            source_url=url,
        )

    async def _ensure_session(self) -> None:
        """首次取页前领取年龄确认会话; 之后只在响应仍是年龄页时重领."""
        if not self._session_claimed:
            await self._claim_session()

    async def _claim_session(self) -> None:
        """经年龄确认入口领取会话; cookie 按 host 落进出站通道, 后续请求自动带上.

        判据是入口响应有没有下发会话, 不是罐里有没有这个名字的 cookie: 罐里留着旧会话时,
        第一次入口调用只把它删掉并回确认标记, 要再领一次才拿到可用会话.
        """
        self._session_claimed = True
        for _ in range(_AGE_GATE_ATTEMPTS):
            if await self._request_age_gate():
                return
        self.logger.warning("age gate issued no session")

    async def _request_age_gate(self) -> bool:
        """请求一次入口, 返回站点是否下发了会话 cookie.

        入口调用失败不得忽略: 302 不带 ``Location`` 时出站通道判失败并抛 ``RequestError``, 该响应下发的
        会话读不到, 再请求一次入口只会把它删掉. 该形态线上未观察到.
        """
        resp = await self.client.web_client.request(
            "GET", f"{self.base_url}{_AGE_GATE_PATH}", headers=self._referer(), allow_redirects=False
        )
        return _issued_session(resp.headers)

    async def _get_html(self, url: str) -> str:
        """取检索页或商品页; 命中年龄页时重领会话并重试一次, 重试仍命中即按来源失败上报."""
        text = await self.client.get_html(url, headers=self._referer())
        if not _is_age_gate(text):
            return text
        await self._claim_session()
        retry = await self.client.get_html(url, headers=self._referer())
        if _is_age_gate(retry):
            # 会话拿不到与「站上没有这个番号」必须分开上报, 否则只剩 no_usable_metadata.
            raise SourceError(FailureReason.AGE_VERIFICATION, url=url, detail="年龄确认会话未被站点接受")
        return retry

    @staticmethod
    def _work_field(label: str) -> str:
        """作品信息里 ``<dt>标签</dt><dd>值</dd>`` 的取值路径; ``dd`` 是 ``dt`` 的兄弟, 不是子节点."""
        return f'//div[@id="works_txt"]//dl[dt[text()="{label}"]]/dd/text()'

    def _referer(self) -> dict[str, str]:
        """站内请求要求带 Referer; 缺失时搜索与商品页都返回首页."""
        return {"Referer": f"{self.base_url}/"}

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
