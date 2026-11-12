import re
from urllib.parse import urljoin, urlparse

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text, is_same_number


class Jav321Crawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.JAV321, base_url="https://www.jav321.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """番号检索走站点的 POST ``sn`` 表单; 命中时站点 301 到详情页, 未命中停留在检索页.

        详情地址是站内 content id, 不能由番号拼出. 站点的模糊检索会把相近番号重定向到别的详情页,
        因此必须按详情页的 品番 确认, 不能直接采用重定向结果.
        """
        text = await self.client.post_html(
            f"{self.base_url}/search",
            data={"sn": query.number},
            headers={"Referer": f"{self.base_url}/"},
        )
        html = Selector(text=text)
        if not is_same_number(_field_text(html, "品番"), query.number):
            return None
        href = html.xpath('//ul[contains(@class, "dropdown-menu")]//a[contains(@href, "/video/")]/@href').get()
        if not href:
            return None
        return urljoin(self.base_url, urlparse(href).path)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        html = Selector(text=text)

        title = extract_text(html, '//div[@class="panel-heading"]/h3/text()')
        number = _field_text(html, "品番")
        if not title or not number:
            return None

        studio = _field_links(html, "メーカー")
        series = _field_links(html, "シリーズ")
        # 信息面板首图是竖版缩略 (DMM 系 ps.jpg / Prestige 系 pf_o1), 只能作海报.
        panel_image = html.xpath('//div[@class="panel-body"]//img/@src').get()
        # 快照序列首张是横版封面, 其余是剧照; 缺图的编号指向 now_printing, 由下载层按重定向终址拒收.
        samples = [_clean_url(url) for url in extract_all_texts(html, '//a[starts-with(@href,"/snapshot/")]/img/@src')]
        # 没有快照区的条目退回面板图, 与本层补齐前的取值一致.
        cover = samples[0] if samples else panel_image
        plot = extract_text(html, '//div[@class="panel-body"]/div[@class="row"]/div[@class="col-md-12"]/text()')

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(_field_links(html, "出演者")),
            studio=studio[0] if studio else None,
            release=_field_text(html, "配信開始日") or None,
            runtime=self._parse_runtime(_field_text(html, "収録時間")),
            tags=_field_links(html, "ジャンル"),
            series=series[0] if series else None,
            thumb_urls=[cover] if cover else [],
            poster_urls=[panel_image] if panel_image else [],
            extrafanart=samples[1:],
            plot=plot or None,
            source_url=url,
        )

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None


def _clean_url(url: str) -> str:
    """折叠 host 之后多余的前导斜杠: 页面里的图片地址有 ``pics.dmm.co.jp//digital/...`` 这种写法."""
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    host, slash, path = rest.partition("/")
    return f"{scheme}://{host}/{path.lstrip('/')}" if slash else url


def _field_text(html: Selector, label: str) -> str:
    """取信息表 ``<b>标签</b>: 值<br>`` 的文本值; 到下一个 ``<b>`` 为止."""
    text = extract_text(html, f'//b[contains(text(),"{label}")]/following-sibling::text()[1]')
    return text.lstrip(":： ").strip()


def _field_links(html: Selector, label: str) -> list[str]:
    """取信息表里某字段的链接文本, 只取到下一个 ``<b>`` 为止.

    各字段的 ``b`` / ``a`` 是同一层兄弟节点. 以「在该标签之后、preceding-sibling 中 b 的数量相同」
    确定链接归属, 否则会把后续字段的片商 / 类型 / 系列链接一并收进取.
    """
    labels = html.xpath(f'//b[contains(text(),"{label}")]')
    if not labels:
        return []
    level = int(float(labels[0].xpath("count(preceding-sibling::b)").get() or 0)) + 1
    return extract_all_texts(
        html, f'//b[contains(text(),"{label}")]/following-sibling::a[count(preceding-sibling::b)={level}]/text()'
    )
