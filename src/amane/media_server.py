"""Emby / Jellyfin 人物条目客户端: 读取, 名字匹配与安全写回.

两家的接口同源. 更新 ``POST /Items/{id}`` 是替换语义: 未提交的可写字段会被清空或解锁,
因此 ``update_person`` 先带上读取到的当前值, 再覆盖业务字段.

图片端点要求 Base64 文本而不是原始字节: Jellyfin 的 ``ImageController.SetItemImage`` 用
``FromBase64Transform`` 解码, Emby 文档也写明 must be base64 encoded.
"""

from __future__ import annotations

import unicodedata
from base64 import b64encode
from typing import TYPE_CHECKING, Any, Protocol, Self

import structlog
from httpx2 import AsyncClient, HTTPError, InvalidURL, Timeout
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from .config import MediaServerConfig

logger = structlog.get_logger()

_TIMEOUT_S = 30.0
"""单请求超时; 人物列表在大库上要现算, 取偏远的一侧."""

_TRANSPORT_ERRORS = (HTTPError, InvalidURL)

_PAGE_SIZE = 500
"""人物列表一页的条数; 按服务器实际返回条数推进 ``startIndex``."""

_FIELDS = (
    "Overview,OriginalTitle,PremiereDate,ProductionYear,ProductionLocations,"
    "CustomRating,Genres,Tags,People,Settings,ProviderIds"
)
"""经 ``fields`` 索取的可选字段.

人物的 ``PremiereDate`` / ``ProductionYear``、``Genres`` / ``Tags`` / ``People`` 并非默认返回;
不索取就写回会把它们清空. ``Settings`` 带 ``LockData``/``LockedFields``/``ForcedSortName``/
``PreferredMetadata*``; 没有 ``LockData`` 时无法安全写回 (省略它会让服务器解锁人物), 因此该字段
缺失按失败处理.
"""

_WRITE_KEYS: tuple[str, ...] = (
    "Name",
    "ForcedSortName",
    "OriginalTitle",
    "OriginalLanguage",
    "CriticRating",
    "CommunityRating",
    "IndexNumber",
    "ParentIndexNumber",
    "Overview",
    "Genres",
    "Taglines",
    "Studios",
    "DateCreated",
    "EndDate",
    "PremiereDate",
    "ProductionYear",
    "OfficialRating",
    "CustomRating",
    "Tags",
    "ProductionLocations",
    "PreferredMetadataCountryCode",
    "PreferredMetadataLanguage",
    "DisplayOrder",
    "AspectRatio",
    "LockData",
    "LockedFields",
    "ProviderIds",
    "RunTimeTicks",
    "Video3DFormat",
    "AlbumArtists",
    "ArtistItems",
    "AirDays",
    "AirTime",
    "SeriesName",
    "Status",
    "Album",
)
"""``POST /Items/{id}`` 会读取的字段; 只把这些字段原样写回.

``ImageTags`` / ``UserData`` / ``BackdropImageTags`` 等是读取模型, 没有写回语义; ``People`` 由
独立的 ``UpdatePeople`` 路径处理, 不随本体 DTO 写回.
"""

SEARCH_LIMIT = 50
"""逐名检索的单页上限; 达到上限时调用方回退到全量人物列表."""


class MediaServerError(Exception):
    """服务器不可达 / 认证失败 / 响应不可解析; 说明面向日志与结果明细."""


class MediaServerClientLike(Protocol):
    """handler 使用的最小客户端契约; 测试以替身实现."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, *_: object) -> None: ...

    async def list_persons(self) -> list[MediaPerson]: ...

    async def search_persons(self, term: str) -> list[MediaPerson]: ...

    async def update_person(self, person: MediaPerson, updates: Mapping[str, object]) -> None: ...

    async def upload_primary_image(self, person_id: str, data: bytes, *, content_type: str) -> None: ...


class MediaPerson(BaseModel):
    """服务器上的人物条目与写回所需的当前 DTO."""

    id: str
    name: str
    has_primary_image: bool = False
    overview: str | None = None
    premiere_date: str | None = None
    production_year: int | None = None
    production_locations: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_dto(cls, value: Mapping[str, Any]) -> MediaPerson | None:
        """缺 Id / Name 的条目不可同步, 返回 None."""
        person_id = value.get("Id")
        name = value.get("Name")
        if not isinstance(person_id, str) or not person_id or not isinstance(name, str) or not name:
            return None
        image_tags = value.get("ImageTags")
        locations = value.get("ProductionLocations")
        production_year = value.get("ProductionYear")
        return cls(
            id=person_id,
            name=name,
            has_primary_image=isinstance(image_tags, dict) and bool(image_tags.get("Primary")),
            overview=_optional_str(value.get("Overview")),
            premiere_date=_optional_str(value.get("PremiereDate")),
            production_year=production_year if isinstance(production_year, int) else None,
            production_locations=[item for item in locations if isinstance(item, str)]
            if isinstance(locations, list)
            else [],
            raw=dict(value),
        )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def normalize_person_name(name: str) -> str:
    """去空白 + NFKC + casefold; 只把写法差异收敛, 不做模糊匹配."""
    return "".join(unicodedata.normalize("NFKC", name).split()).casefold()


def index_persons(persons: Iterable[MediaPerson]) -> dict[str, list[MediaPerson]]:
    """归一后的名字 → 人物条目; 同名条目保留多条, 由调用方按 Id 去重."""
    index: dict[str, list[MediaPerson]] = {}
    for person in persons:
        index.setdefault(normalize_person_name(person.name), []).append(person)
    return index


def find_persons(index: Mapping[str, list[MediaPerson]], names: Sequence[str]) -> list[MediaPerson]:
    """按名字列表在索引里取人物, 按输入顺序去重."""
    found: list[MediaPerson] = []
    seen: set[str] = set()
    for name in names:
        for person in index.get(normalize_person_name(name), []):
            if person.id not in seen:
                seen.add(person.id)
                found.append(person)
    return found


def match_persons(names: Sequence[str], persons: Iterable[MediaPerson]) -> list[MediaPerson]:
    """服务器检索是模糊的; 结果按归一后的名字精确过滤, 按输入顺序去重."""
    wanted = {normalize_person_name(name) for name in names if name.strip()}
    if not wanted:
        return []
    matched: list[MediaPerson] = []
    seen: set[str] = set()
    for person in persons:
        if person.id in seen or normalize_person_name(person.name) not in wanted:
            continue
        seen.add(person.id)
        matched.append(person)
    return matched


class MediaServerClient:
    """单服务器的客户端; 用 ``async with`` 或显式 ``close`` 释放连接池."""

    def __init__(self, config: MediaServerConfig, *, client: AsyncClient | None = None) -> None:
        if not config.url:
            raise MediaServerError("media_server.url 未配置")
        self._base = config.url.rstrip("/")
        self._api_key = config.api_key or ""
        self._client = client or AsyncClient(
            timeout=Timeout(_TIMEOUT_S), verify=config.verify_tls, follow_redirects=True
        )

    async def __aenter__(self) -> MediaServerClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def list_persons(self) -> list[MediaPerson]:
        """分页取回服务器上的全部人物.

        只以空页或读到 ``TotalRecordCount`` 作为结束: Jellyfin 的 ``GetPersons`` 未指定排序时,
        ``StartIndex`` 只作用于当次查询结果, 后一页可能重复已读 Id, 同时仍有未读条目. 出现
        重复页、空页提前结束或总数异常时抛出 ``MediaServerError``, 不允许带着残缺索引继续.
        """
        persons: list[MediaPerson] = []
        seen: set[str] = set()
        start = 0
        while True:
            payload = await self._get_json(
                "/Persons",
                {
                    "startIndex": start,
                    "limit": _PAGE_SIZE,
                    "fields": _FIELDS,
                    "enableImages": "true",
                },
            )
            items = payload.get("Items")
            if not isinstance(items, list):
                raise MediaServerError("人物列表响应缺少 Items")
            total = payload.get("TotalRecordCount")
            if not items:
                # 空服务器合法地返回 TotalRecordCount=0; 首屏空页且无任何条目视为完整.
                if not persons and total == 0:
                    return []
                if isinstance(total, int) and total > 0 and len(persons) >= total:
                    return persons
                raise MediaServerError("人物列表分页不完整: 空页时仍未读满 TotalRecordCount")
            if not isinstance(total, int) or total <= 0:
                raise MediaServerError("人物列表响应缺少有效的 TotalRecordCount")
            fresh = [
                person
                for item in items
                if isinstance(item, dict) and (person := MediaPerson.from_dto(item)) and person.id not in seen
            ]
            if not fresh:
                raise MediaServerError("人物列表分页重复: 本页没有新条目, 无法确认完整性")
            seen.update(person.id for person in fresh)
            persons.extend(fresh)
            if len(persons) > total:
                raise MediaServerError("人物列表分页数量超过 TotalRecordCount")
            if len(persons) == total:
                return persons
            start += len(items)

    async def search_persons(self, term: str, *, limit: int = SEARCH_LIMIT) -> list[MediaPerson]:
        """按名字检索; 服务器检索是模糊的, 精确匹配由调用方完成."""
        payload = await self._get_json(
            "/Persons",
            {"searchTerm": term, "limit": limit, "fields": _FIELDS, "enableImages": "true"},
        )
        items = payload.get("Items")
        if not isinstance(items, list):
            raise MediaServerError("人物检索响应缺少 Items")
        return [person for item in items if isinstance(item, dict) and (person := MediaPerson.from_dto(item))]

    async def update_person(self, person: MediaPerson, updates: Mapping[str, object]) -> None:
        """以白名单字段的当前值为底, 覆盖业务字段后整体写回.

        ``LockData`` 缺失时拒绝写入: 参数缺省会被服务器当成 ``false``, 把人物解锁.
        """
        if not isinstance(person.raw.get("LockData"), bool):
            raise MediaServerError("人物响应缺少 LockData, 无法安全写回")
        payload: dict[str, object] = {key: person.raw[key] for key in _WRITE_KEYS if key in person.raw}
        payload["Name"] = person.name
        payload.update(updates)
        await self._post_json(f"/Items/{person.id}", payload)

    async def upload_primary_image(self, person_id: str, data: bytes, *, content_type: str) -> None:
        """上传主图; 重复上传覆盖服务器上的现有图片."""
        await self._post(
            f"/Items/{person_id}/Images/Primary",
            content=b64encode(data),
            headers={"Content-Type": content_type},
        )

    async def _get_json(self, path: str, params: Mapping[str, str | int]) -> dict[str, Any]:
        try:
            response = await self._client.get(self._base + path, params=dict(params), headers=self._headers())
        except _TRANSPORT_ERRORS as exc:
            raise MediaServerError(f"连接失败: {exc} (检查 media_server.url)") from exc
        self._raise_for_status(response.status_code, response.text)
        try:
            payload = response.json()
        except ValueError as exc:
            raise MediaServerError(f"响应不是 JSON (HTTP {response.status_code})") from exc
        if not isinstance(payload, dict):
            raise MediaServerError("响应不是对象")
        return payload

    async def _post_json(self, path: str, payload: Mapping[str, object]) -> None:
        await self._post(path, json=dict(payload))

    async def _post(
        self,
        path: str,
        *,
        json: dict[str, object] | None = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        try:
            response = await self._client.post(
                self._base + path,
                json=json,
                content=content,
                headers={**self._headers(), **(headers or {})},
            )
        except _TRANSPORT_ERRORS as exc:
            raise MediaServerError(f"连接失败: {exc} (检查 media_server.url)") from exc
        self._raise_for_status(response.status_code, response.text)

    def _headers(self) -> dict[str, str]:
        """两家认证头同时给出; 密钥不进查询串."""
        return {"Authorization": f'MediaBrowser Token="{self._api_key}"', "X-Emby-Token": self._api_key}

    @staticmethod
    def _raise_for_status(status: int, body: str) -> None:
        if status < 300:
            return
        if status == 401:
            raise MediaServerError("认证失败 (HTTP 401): media_server.api_key 无效")
        if status == 403:
            raise MediaServerError("权限不足 (HTTP 403): media_server.api_key 需要管理员权限")
        raise MediaServerError(f"HTTP {status}: {body[:200]}")
