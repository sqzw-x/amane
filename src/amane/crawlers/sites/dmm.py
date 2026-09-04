"""按 URL 分类路由到 digital / mono / rental / Fanza TV / DMM TV 解析器."""

import json
import re
from enum import StrEnum

from parsel import Selector

from ...enums import ActorGender, SiteName
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import CSSSelector, extract_all_texts, extract_text
from .dmm_api import DigitalResponse, DmmTvResponse, FanzaTvResponse, digital_payload, dmm_tv_payload, fanza_tv_payload


class Category(StrEnum):
    FANZA_TV = "fanza_tv"
    DMM_TV = "dmm_tv"
    DIGITAL = "digital"
    PRIME = "prime"
    MONTHLY = "monthly"
    MONO = "mono"
    RENTAL = "rental"
    OTHER = "other"


_CATEGORY_PRIORITY: dict[Category, int] = {
    Category.DIGITAL: 100,
    Category.FANZA_TV: 90,
    Category.DMM_TV: 80,
    Category.MONO: 60,
    Category.PRIME: 50,
    Category.MONTHLY: 40,
    Category.RENTAL: 30,
    Category.OTHER: 0,
}


def _parse_category(url: str) -> Category:
    if "tv.dmm.co.jp" in url:
        return Category.FANZA_TV
    if "tv.dmm.com" in url:
        return Category.DMM_TV
    if "/digital/" in url or "video.dmm.co.jp" in url:
        return Category.DIGITAL
    if "/prime/" in url:
        return Category.PRIME
    if "/monthly/" in url:
        return Category.MONTHLY
    if "/mono/" in url:
        return Category.MONO
    if "/rental/" in url:
        return Category.RENTAL
    return Category.OTHER


class DmmCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.DMM,
            base_url="https://www.dmm.co.jp",
            urls=[
                "https://api.video.dmm.co.jp",
                "https://api.tv.dmm.co.jp",
                "https://api.tv.dmm.com",
            ],
            cookies={"age_check_done": "1"},
        )

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        number_lower = number.lower()

        # 拆出前缀与数字.
        match = re.search(r"([a-z]+)-?(\d+)", number_lower)
        if not match:
            return None
        prefix = match.group(1)
        digits = match.group(2)

        # 数字 >=5 位且以 00 开头时去掉前导 00 (DMM cid 填充).
        if len(digits) >= 5 and digits.startswith("00"):
            digits = digits[2:]

        # 搜索变体: 五位零填充与去填充.
        number_00 = f"{prefix}{digits:0>5}"
        number_no_00 = f"{prefix}{digits}"

        search_urls = [
            f"https://www.dmm.co.jp/search/=/searchstr={number_00}/sort=ranking/",
            f"https://www.dmm.co.jp/search/=/searchstr={number_no_00}/sort=ranking/",
        ]

        all_urls: set[str] = set()
        last_error: RequestError | None = None
        any_ok = False
        for search_url in search_urls:
            try:
                text = await self.client.get_html(search_url, cookies=self.cookies)
            except RequestError as exc:
                last_error = exc
                continue
            any_ok = True
            html = Selector(text=text)
            found = self._parse_search_results(html, prefix, digits)
            all_urls.update(found)
            if all_urls:
                break

        if not all_urls:
            if not any_ok and last_error is not None:
                raise last_error
            return None

        # 按分类优先级选取.
        sorted_urls = sorted(all_urls, key=lambda u: _CATEGORY_PRIORITY.get(_parse_category(u), 0), reverse=True)
        return sorted_urls[0] if sorted_urls else None

    def _parse_search_results(self, html: Selector, prefix: str, digits: str) -> list[str]:
        # 详情 URL 嵌在 script 里, 同时试转义与非转义两种 JSON 写法.
        raw_urls = set(html.re(r'detailUrl\\":\\"(.*?)\\"'))
        if not raw_urls:
            raw_urls = set(html.re(r'"detailUrl":"(.*?)"'))

        if not raw_urls:
            return []

        n_padded = f"{prefix}{digits:0>5}"
        n_short = f"{prefix}{digits}"

        results = []
        for raw_url in raw_urls:
            try:
                url = raw_url.encode("utf-8").decode("unicode_escape")
            except UnicodeDecodeError, ValueError:
                url = raw_url
            if re.search(rf"[^a-z]{re.escape(n_padded)}[^0-9]", url) or re.search(
                rf"[^a-z]{re.escape(n_short)}[^0-9]", url
            ):
                results.append(url)

        return results

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        category = _parse_category(url)
        match category:
            case Category.FANZA_TV:
                return await self._scrape_fanza_tv(url)
            case Category.DMM_TV:
                return await self._scrape_dmm_tv(url)
            case Category.DIGITAL:
                return await self._scrape_digital(url, self.cookies)
            case Category.MONO | Category.PRIME | Category.MONTHLY:
                return await self._scrape_mono(url, self.cookies)
            case Category.RENTAL:
                return await self._scrape_rental(url, self.cookies)
            case _:
                return None

    async def _scrape_digital(self, url: str, cookies: dict[str, str]) -> MediaMetadata | None:
        cid_match = re.search(r"(?:cid|id)=([^/&?]+)", url)
        if not cid_match:
            return None
        cid = cid_match.group(1)

        response = await self.client.post_json("https://api.video.dmm.co.jp/graphql", json=digital_payload(cid))

        try:
            resp = DigitalResponse.model_validate(response)
        except (ValueError, TypeError) as e:
            self.logger.debug("digital response parse failed", error=str(e))
            return None

        data = resp.data.ppvContent
        if not data.title:
            return None

        runtime = int(data.duration / 60) if data.duration else None

        extrafanart = [si.largeImageUrl for si in data.sampleImages if si.largeImageUrl]

        number = self._cid_to_number(cid)

        return MediaMetadata(
            number=number,
            title=data.title or None,
            actors=film_actors(a.name for a in data.actresses if a.name),
            studio=data.maker.name if data.maker else None,
            publisher=data.label.name if data.label else None,
            release=data.deliveryStartDate or None,
            runtime=runtime,
            tags=[g.name for g in data.genres if g.name],
            series=data.series.name if data.series else None,
            plot=data.description or None,
            thumb_urls=[data.packageImage.largeUrl] if data.packageImage.largeUrl else [],
            poster_urls=[data.packageImage.mediumUrl] if data.packageImage.mediumUrl else [],
            trailer_urls=[data.sample2DMovie.highestMovieUrl] if data.sample2DMovie.highestMovieUrl else [],
            score=resp.data.reviewSummary.average if resp.data.reviewSummary else None,
            directors=[d.name for d in data.directors if d.name],
            extrafanart=extrafanart,
            source_url=url,
            external_id=url,
        )

    # following-sibling::dd[1] 只取紧邻的 dd, 避免跨字段匹配.
    _DD_SPAN = "following-sibling::dd[1]//span[@class='content-detail__text']/text()"
    _DD_TEXT = "following-sibling::dd[1]/text()"
    # monthly 同时渲染 #multi-column / #single-column 两套相同信息; 全页 //dt 会翻倍.
    # extract_* 按选择器顺序取首个有结果者, 先 multi 再 single, 最后回退旧 table.
    _DETAIL_MULTI = "//*[@id='multi-column']"
    _DETAIL_SINGLE = "//*[@id='single-column']"

    async def _scrape_mono(self, url: str, cookies: dict[str, str]) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=cookies)
        html = Selector(text=text)

        cid_match = re.search(r"(?:cid|id)=([^/&?]+)", url)
        number_raw = cid_match.group(1) if cid_match else ""
        number = self._cid_to_number(number_raw)

        _dd_span = self._DD_SPAN
        _dd_text = self._DD_TEXT
        _multi = self._DETAIL_MULTI
        _single = self._DETAIL_SINGLE

        # 标题; 无 h1 时回退 JSON-LD.
        title = extract_text(html, "//h1/text()", '//h1[@id="title"]/text()', '//h1[@class="item fn bold"]/text()')
        if not title:
            json_ld_text = extract_text(html, '//script[@type="application/ld+json"]/text()')
            if json_ld_text:
                try:
                    ld = json.loads(json_ld_text)
                    title = ld.get("name", "")
                except KeyError, TypeError, ValueError:
                    pass

        release_raw = extract_text(
            html,
            f"{_multi}//dt[contains(text(),'配信開始日')]/{_dd_span}",
            f"{_single}//dt[contains(text(),'配信開始日')]/{_dd_span}",
            f"{_multi}//dt[contains(text(),'発売日')]/{_dd_span}",
            f"{_single}//dt[contains(text(),'発売日')]/{_dd_span}",
            "//td[contains(text(),'発売日')]/following-sibling::td/text()",
            "//th[contains(text(),'発売日')]/following-sibling::td/text()",
            "//td[contains(text(),'配信開始日')]/following-sibling::td/text()",
        )
        release = self._parse_release(release_raw)

        runtime = self._parse_runtime(
            extract_text(
                html,
                f"{_multi}//dt[contains(text(),'収録時間')]/{_dd_text}",
                f"{_single}//dt[contains(text(),'収録時間')]/{_dd_text}",
                "//td[contains(text(),'収録時間')]/following-sibling::td/text()",
                "//th[contains(text(),'収録時間')]/following-sibling::td/text()",
            )
        )

        studio = extract_text(
            html,
            f"{_multi}//dt[contains(text(),'メーカー')]/{_dd_span}",
            f"{_single}//dt[contains(text(),'メーカー')]/{_dd_span}",
            "//td[contains(text(),'メーカー')]/following-sibling::td/a/text()",
        )
        publisher = extract_text(
            html,
            f"{_multi}//dt[contains(text(),'レーベル')]/{_dd_span}",
            f"{_single}//dt[contains(text(),'レーベル')]/{_dd_span}",
            "//td[contains(text(),'レーベル')]/following-sibling::td/a/text()",
        )
        series = extract_text(
            html,
            f"{_multi}//dt[contains(text(),'シリーズ')]/{_dd_span}",
            f"{_single}//dt[contains(text(),'シリーズ')]/{_dd_span}",
            "//td[contains(text(),'シリーズ')]/following-sibling::td/a/text()",
            "//th[contains(text(),'シリーズ')]/following-sibling::td/a/text()",
        )
        directors = extract_all_texts(
            html,
            f"{_multi}//dt[contains(text(),'監督')]/{_dd_span}",
            f"{_single}//dt[contains(text(),'監督')]/{_dd_span}",
            "//td[contains(text(),'監督')]/following-sibling::td/a/text()",
            "//th[contains(text(),'監督')]/following-sibling::td/a/text()",
        )
        actors = extract_all_texts(
            html,
            f"{_multi}//dt[contains(text(),'出演者')]/{_dd_span}",
            f"{_single}//dt[contains(text(),'出演者')]/{_dd_span}",
            "//span[@id='performer']/a/text()",
            "//td[@id='fn-visibleActor']/div/a/text()",
            "//td[contains(text(),'出演者')]/following-sibling::td/a/text()",
        )
        tags = extract_all_texts(
            html,
            f"{_multi}//dt[contains(text(),'ジャンル')]/{_dd_span}",
            f"{_single}//dt[contains(text(),'ジャンル')]/{_dd_span}",
            "//td[contains(text(),'ジャンル')]/following-sibling::td/a/text()",
        )

        # 封面: 优先 JSON-LD image, 再回退 og:image.
        thumb_url = ""
        json_ld_text = extract_text(html, '//script[@type="application/ld+json"]/text()')
        if json_ld_text:
            try:
                ld = json.loads(json_ld_text)
                img = ld.get("image", "")
                thumb_url = img if isinstance(img, str) else (img[0] if isinstance(img, list) and img else "")
            except KeyError, TypeError, ValueError:
                pass
        if not thumb_url:
            thumb_url = extract_text(html, '//meta[@property="og:image"]/@content') or ""
        if thumb_url:
            thumb_url = thumb_url.replace("ps.jpg", "pl.jpg")

        extrafanart_raw = extract_all_texts(
            html, "//div[@id='sample-image-block']/a/@href", "//a[@name='sample-image']/img/@data-lazy"
        )
        extrafanart = [re.sub(r"-(\d+)\.jpg", r"jp-\1.jpg", i) for i in extrafanart_raw]

        plot = "\n".join(extract_all_texts(html, CSSSelector(".wrapper-detailContents~div>p.mg-b20::text")))

        score_text = extract_text(
            html,
            "//p[contains(@class,'d-review__average')]/strong/text()",
            '//script[@type="application/ld+json"]/text()',
        )
        score = self._parse_score(score_text)
        if score is None and json_ld_text:
            try:
                ld = json.loads(json_ld_text)
                rating = ld.get("aggregateRating", {})
                if rating.get("ratingValue") is not None:
                    score = float(rating["ratingValue"])
            except KeyError, TypeError, ValueError:
                pass

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio or None,
            publisher=publisher or None,
            release=release,
            runtime=runtime,
            tags=tags,
            series=series or None,
            plot=plot or None,
            thumb_urls=[thumb_url] if thumb_url else [],
            score=score,
            directors=directors,
            extrafanart=extrafanart,
            source_url=url,
            external_id=url,
        )

    async def _scrape_rental(self, url: str, cookies: dict[str, str]) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=cookies)
        html = Selector(text=text)

        cid_match = re.search(r"(?:cid|id)=([^/&?]+)", url)
        number_raw = cid_match.group(1) if cid_match else ""
        number = self._cid_to_number(number_raw)

        title = extract_text(html, '//h1[@id="title"]/text()', '//h1[@class="item fn bold"]/text()')
        runtime = self._parse_runtime(
            extract_text(
                html,
                "//td[contains(text(),'収録時間')]/following-sibling::td/text()",
                "//th[contains(text(),'収録時間')]/following-sibling::td/text()",
            )
        )
        studio = extract_text(html, "//td[contains(text(),'メーカー')]/following-sibling::td/a/text()")
        publisher = extract_text(html, "//td[contains(text(),'レーベル')]/following-sibling::td/a/text()")
        series = extract_text(html, "//td[contains(text(),'シリーズ')]/following-sibling::td/a/text()")
        directors = extract_all_texts(html, "//td[contains(text(),'監督')]/following-sibling::td/a/text()")
        actors = extract_all_texts(
            html, "//span[@id='performer']/a/text()", "//td[contains(text(),'出演者')]/following-sibling::td/a/text()"
        )
        tags = extract_all_texts(html, "//td[contains(text(),'ジャンル')]/following-sibling::td/a/text()")

        thumb_url = extract_text(html, '//meta[@property="og:image"]/@content')
        if thumb_url:
            thumb_url = thumb_url.replace("ps.jpg", "pl.jpg")

        extrafanart = extract_all_texts(html, "//a[@name='sample-image']/img/@src")
        plot = extract_text(html, CSSSelector(".clear p::text"))
        score_text = extract_text(html, "//p[contains(@class,'d-review__average')]/strong/text()")
        score = self._parse_score(score_text)

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio or None,
            publisher=publisher or None,
            release=None,  # rental 页无有效发售日.
            runtime=runtime,
            tags=tags,
            series=series or None,
            plot=plot or None,
            thumb_urls=[thumb_url] if thumb_url else [],
            score=score,
            directors=directors,
            extrafanart=extrafanart,
            source_url=url,
            external_id=url,
        )

    async def _scrape_fanza_tv(self, url: str) -> MediaMetadata | None:
        cid_match = re.search(r"content=([^&/]+)", url)
        if not cid_match:
            return None
        cid = cid_match.group(1)

        response = await self.client.post_json("https://api.tv.dmm.co.jp/graphql", json=fanza_tv_payload(cid))

        try:
            resp = FanzaTvResponse.model_validate(response)
        except (ValueError, TypeError) as e:
            self.logger.debug("fanza tv response parse failed", error=str(e))
            return None

        data = resp.data.fanzaTvPlus.content
        if not data.title:
            return None

        trailer_url = self._derive_fanza_trailer(data.sampleMovie.url)

        runtime = int(data.playInfo.duration / 60) if data.playInfo.duration else None

        extrafanart = [sp.imageLarge for sp in data.samplePictures if sp.imageLarge]

        return MediaMetadata(
            number=cid,
            title=data.title,
            actors=film_actors(a.name for a in data.actresses if a.name),
            studio=data.maker.name if data.maker else None,
            publisher=data.label.name if data.label else None,
            release=data.startDeliveryAt or None,
            runtime=runtime,
            tags=[g.name for g in data.genres if g.name],
            series=data.series.name if data.series else None,
            plot=data.description or None,
            poster_urls=[data.packageImage] if data.packageImage else [],
            thumb_urls=[data.packageLargeImage] if data.packageLargeImage else [],
            trailer_urls=[trailer_url] if trailer_url else [],
            score=data.reviewSummary.averagePoint if data.reviewSummary else None,
            directors=[d.name for d in data.directors if d.name],
            extrafanart=extrafanart,
            source_url=url,
            external_id=url,
        )

    async def _scrape_dmm_tv(self, url: str) -> MediaMetadata | None:
        season_match = re.search(r"seasonId=(\d+)", url)
        if not season_match:
            return None
        season_id = season_match.group(1)

        response = await self.client.post_json("https://api.tv.dmm.com/graphql", json=dmm_tv_payload(season_id))

        try:
            resp = DmmTvResponse.model_validate(response)
        except (ValueError, TypeError) as e:
            self.logger.debug("dmm tv response parse failed", error=str(e))
            return None

        data = resp.data.video
        if not data.titleName:
            return None

        directors = [s.staffName for s in data.staffs if s.roleName == "監督"]
        studio_candidates = [
            s.staffName for s in data.staffs if s.roleName in ("制作プロダクション", "制作", "制作著作")
        ]
        studio = studio_candidates[0] if studio_candidates else None

        return MediaMetadata(
            number="",  # DMM TV 不暴露与其它分类相同的 cid.
            title=data.titleName,
            actors=film_actors((c.actorName for c in data.casts if c.actorName), gender=ActorGender.UNKNOWN),
            studio=studio,
            publisher=studio,
            release=data.startPublicAt or None,
            runtime=None,
            tags=[g.name for g in data.genres if g.name],
            plot=data.description or None,
            poster_urls=[data.keyVisualImage] if data.keyVisualImage else [],
            thumb_urls=[data.packageImage] if data.packageImage else [],
            score=data.reviewSummary.averagePoint if data.reviewSummary else None,
            directors=directors,
            source_url=url,
            external_id=url,
        )

    @staticmethod
    def _cid_to_number(cid: str) -> str:
        # 先去掉 n_709 / h_068 一类前缀, 再去掉 so/sp 等后缀, 最后拆出 PREFIX-数字.
        cleaned = re.sub(r"^[a-z]_\d+", "", cid)
        cleaned = re.sub(r"(so|sp|tk|hd|hhb|hib)$", "", cleaned)

        match = re.match(r"([a-z]+)0*(\d+)", cleaned)
        if not match:
            return cid.upper()
        prefix = match.group(1).upper()
        digits = match.group(2)
        return f"{prefix}-{digits}"

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_release(text: str) -> str | None:
        if not text:
            return None
        text = text.replace("/", "-")
        match = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", text)
        return match.group(1) if match else None

    @staticmethod
    def _parse_score(text: str) -> float | None:
        if not text:
            return None
        text = text.replace("点", "")
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _derive_fanza_trailer(hls_url: str) -> str | None:
        # HLS playlist → litevideo 直链 MP4: 换路径并把 playlist.m3u8 换成 {cid}_sm_w.mp4.
        if not hls_url:
            return None
        mp4_url = hls_url.replace("hlsvideo", "litevideo")
        cid_match = re.search(r"/([^/]+)/playlist\.m3u8", mp4_url)
        if not cid_match:
            return None
        cid = cid_match.group(1)
        return mp4_url.replace("playlist.m3u8", f"{cid}_sm_w.mp4")
