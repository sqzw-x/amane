"""JSON API: ``/api/sku/item/{skuId}`` → ``parentProduct.uuid`` → ``/api/product/{uuid}``.

``/api/search`` 只索引在售商品且有模糊匹配, 不允许用作检索.
站点限定日本 IP: 非日本 IP 下 curl_cffi 得 CloudFront 403, 系统 curl 得地域限制文案.
"""

from typing import Any

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors


class PrestigeCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.PRESTIGE, base_url="https://www.prestige-av.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number.upper()

        # 候选 SKU: 带横线 / 无横线 / GOOE 特典前缀.
        number_no_dash = number.replace("-", "")
        candidates = [
            number,  # ABW-350
            number_no_dash,  # ABW350
            f"GOOE{number}",  # GOOEABW-350
            f"GOOE{number_no_dash}",  # GOOEABW350
        ]

        last_error: RequestError | None = None
        any_ok = False
        for sku_id in candidates:
            sku_url = f"{self.base_url}/api/sku/item/{sku_id}"
            try:
                data = await self.client.get_json(sku_url)
            except RequestError as exc:
                last_error = exc
                continue
            any_ok = True

            if not isinstance(data, dict):
                continue

            parent = data.get("parentProduct")
            if isinstance(parent, dict) and parent.get("uuid"):
                uuid = parent["uuid"]
                self.logger.debug("search hit via SKU", number=number, sku_id=sku_id, product_uuid=uuid)
                return f"{self.base_url}/api/product/{uuid}"

        if not any_ok and last_error is not None:
            raise last_error
        self.logger.debug("search miss: SKU not found", number=number, candidates=candidates)
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        data = await self.client.get_json(url, headers={"Accept": "application/json"})
        if not data or not isinstance(data, dict):
            return None

        # 番号取非 GOOE/GOO 前缀的通常版 SKU; 特典 SKU 不当作番号.
        sku_list: list[dict[str, Any]] = data.get("sku") or []
        number = ""
        for sku in sku_list:
            sid: str = sku.get("deliveryItemId", "")
            if not sid.startswith("GOOE") and not sid.startswith("GOO"):
                number = sid
                break
        if not number and sku_list:
            number = sku_list[0].get("deliveryItemId", "")
        if not number:
            return None

        title = data.get("title")
        release = data.get("mgsStartAt") or data.get("createdAt")
        if release and "T" in release:
            release = release.split("T")[0]

        runtime = data.get("playTime")

        actors: list[str] = []
        for a in data.get("actress") or []:
            name = a.get("name", "") if isinstance(a, dict) else str(a)
            if name:
                actors.append(name)

        directors: list[str] = []
        for d in data.get("directors") or []:
            name = d.get("name", "") if isinstance(d, dict) else str(d)
            if name:
                directors.append(name)

        tags: list[str] = []
        for g in data.get("genre") or []:
            name = g.get("name", "") if isinstance(g, dict) else str(g)
            if name:
                tags.append(name)

        # maker/label/series 可能是 dict 或 list.
        def _extract_name(value: Any) -> str | None:
            if isinstance(value, dict):
                return value.get("name") or None
            if isinstance(value, list) and value:
                v0 = value[0]
                return v0.get("name") if isinstance(v0, dict) else str(v0)
            if isinstance(value, str):
                return value
            return None

        studio = "プレステージ"
        maker_name = _extract_name(data.get("maker"))
        label_name = _extract_name(data.get("label"))
        series_name = _extract_name(data.get("series"))

        def _image_url(path: str | None) -> str | None:
            if not path:
                return None
            return f"https://image.prestige-av.com/{path}" if not path.startswith("http") else path

        thumb_path = (data.get("thumbnail") or {}).get("path") if isinstance(data.get("thumbnail"), dict) else None
        package_path = (
            (data.get("packageImage") or {}).get("path") if isinstance(data.get("packageImage"), dict) else None
        )
        thumb_url = _image_url(thumb_path)
        poster_url = _image_url(package_path)

        extrafanart: list[str] = []
        for img_list_key in ("media",):
            for img in data.get(img_list_key) or []:
                path = img.get("path") if isinstance(img, dict) else None
                img_url = _image_url(path)
                if img_url:
                    extrafanart.append(img_url)

        external_id = data.get("uuid", "")

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio,
            publisher=label_name or maker_name or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
            series=series_name or None,
            plot=data.get("body") or None,
            poster_urls=[poster_url] if poster_url else [],
            thumb_urls=[thumb_url] if thumb_url else [],
            directors=directors,
            extrafanart=extrafanart,
            source_url=url,
            external_id=external_id or None,
        )
