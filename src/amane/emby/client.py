"""Emby / Jellyfin HTTP 客户端: 人物检索, 头像上传, 人物字段回写.

两家的接口同源. 认证同时给出 Jellyfin 的 ``Authorization: MediaBrowser Token`` 与 Emby 的
``X-Emby-Token`` 头, 不用查询参数 (密钥会进服务器访问日志); 检索用的 ``searchTerm`` 只能走查询参数,
演员名因此会落进访问日志, 这里接受该代价.

方法只做传输与解析, 不做重试: 单个失败由调用方记入结果, 整任务不因一个人物失败而中断.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
from httpx2 import AsyncClient, HTTPError, InvalidURL, Timeout

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..config import EmbyConfig

logger = structlog.get_logger()

_TRANSPORT_ERRORS = (HTTPError, InvalidURL)
"""要转成 ``EmbyError`` 的传输层异常. ``InvalidURL`` 不在 ``HTTPError`` 之下: ``emby.url`` 写错端口
(``http://host:port``) 时它在构造请求阶段就抛出, 漏掉会让原始异常穿到调用方."""

_PAGE_SIZE = 500
"""人物列表一页的条数; 逐页取到没有新条目为止."""

_PERSON_FIELDS = "Overview,ProductionLocations"
"""经 ``fields`` 索取的两个字段. 生日 (``PremiereDate``) 无法这样索取, 它是否随列表默认返回也未验证,
因此写回判据不依赖它."""


class EmbyError(Exception):
    """服务器不可达 / 认证失败 / 响应不可解析; 说明面向日志与结果明细."""


@dataclass(frozen=True, slots=True)
class EmbyPerson:
    """服务器上的人物条目."""

    id: str
    name: str
    has_primary_image: bool
    overview: str | None = None
    premiere_date: str | None = None
    """服务器返回的生日; 它是否随人物列表返回未验证, 因此不参与写回判据 (见 ``EmbySyncHandler``)."""
    production_locations: list[str] = field(default_factory=list)

    @classmethod
    def from_dto(cls, raw: Mapping[str, Any]) -> EmbyPerson | None:
        """缺 Id 或 Name 的条目视为不可同步, 返回 None."""
        person_id = raw.get("Id")
        name = raw.get("Name")
        if not isinstance(person_id, str) or not person_id or not isinstance(name, str) or not name:
            return None
        image_tags = raw.get("ImageTags")
        locations = raw.get("ProductionLocations")
        premiere = raw.get("PremiereDate")
        return cls(
            id=person_id,
            name=name,
            has_primary_image=isinstance(image_tags, dict) and bool(image_tags.get("Primary")),
            overview=raw.get("Overview") if isinstance(raw.get("Overview"), str) else None,
            premiere_date=premiere if isinstance(premiere, str) else None,
            production_locations=[str(item) for item in locations] if isinstance(locations, list) else [],
        )


class EmbyClient:
    """单服务器的客户端; 用 ``async with`` 或显式 ``close`` 释放连接池."""

    def __init__(self, config: EmbyConfig, *, client: AsyncClient | None = None) -> None:
        self._base = (config.url or "").rstrip("/")
        self._api_key = config.api_key or ""
        self._client = client or AsyncClient(
            timeout=Timeout(config.timeout), verify=config.verify_tls, follow_redirects=True
        )

    async def __aenter__(self) -> EmbyClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def list_persons(self) -> list[EmbyPerson]:
        """取回服务器上的全部人物条目.

        以响应里的 ``TotalRecordCount`` 判定读完, 并按本次**实际返回**的条数推进 ``startIndex``:
        服务器可以把 ``limit`` 收到自己的上限, 按请求值推进会跳过中间的人物. 只看总数与条数会在
        「服务器每页都返回整页、声明的总数大于实际可取条数」时不终止, 因此**连续两次没有新条目**也结束 —
        重复返回同一页说明服务器的分页到此为止. 要连续两次是因为服务器的分页顺序未必稳定, 单次没有新条目
        可能只是同一批条目换了顺序, 后一页仍可能带出未读到的部分.
        """
        persons: list[EmbyPerson] = []
        seen: set[str] = set()
        start = 0
        stalled = False
        while True:
            payload = await self._get_json(
                "/Persons",
                {
                    "startIndex": start,
                    "limit": _PAGE_SIZE,
                    "fields": _PERSON_FIELDS,
                    "enableImages": "true",
                },
            )
            items = payload.get("Items")
            if not isinstance(items, list):
                raise EmbyError("人物列表响应缺少 Items")
            if not items:
                return persons
            fresh = [
                person
                for item in items
                if isinstance(item, dict) and (person := EmbyPerson.from_dto(item)) and person.id not in seen
            ]
            if not fresh:
                if stalled:
                    return persons
                stalled = True
            else:
                stalled = False
                seen.update(person.id for person in fresh)
                persons.extend(fresh)
            total = payload.get("TotalRecordCount")
            if isinstance(total, int) and len(persons) >= total:
                return persons
            start += len(items)

    async def search_persons(self, term: str, *, limit: int = 50) -> list[EmbyPerson]:
        """按名字检索人物; 服务器的检索是模糊的, 精确匹配由调用方完成."""
        payload = await self._get_json(
            "/Persons",
            {"searchTerm": term, "limit": limit, "fields": _PERSON_FIELDS, "enableImages": "true"},
        )
        items = payload.get("Items")
        if not isinstance(items, list):
            raise EmbyError("人物检索响应缺少 Items")
        return [person for item in items if isinstance(item, dict) and (person := EmbyPerson.from_dto(item))]

    async def upload_primary_image(self, person_id: str, data: bytes, *, content_type: str) -> None:
        """上传头像; 重复上传覆盖服务器上的现有图片.

        body 是 Base64 文本而不是原始字节: 两家的该端点都按 Base64 解码 (Jellyfin
        ``ImageController.SetItemImage`` 读的是 ``FromBase64Transform`` 流), 原始字节会被解成错误内容,
        而接口仍返回成功.
        """
        await self._post(
            f"/Items/{person_id}/Images/Primary", content=b64encode(data), headers={"Content-Type": content_type}
        )

    async def update_person(self, person_id: str, fields: Mapping[str, Any]) -> None:
        """回写人物字段; 只发要改的字段, 未给出的字段服务器保持不变."""
        await self._post(f"/Items/{person_id}", json=dict(fields))

    async def _get_json(self, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.get(self._base + path, params=dict(params), headers=self._headers())
        except _TRANSPORT_ERRORS as exc:
            raise EmbyError(f"连接失败: {exc} (检查 emby.url)") from exc
        self._raise_for_status(resp.status_code, resp.text)
        try:
            payload = resp.json()
        except ValueError as exc:  # 非 JSON 响应 (反向代理的错误页等)
            raise EmbyError(f"响应不是 JSON (HTTP {resp.status_code})") from exc
        if not isinstance(payload, dict):
            raise EmbyError("响应不是对象")
        return payload

    async def _post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        try:
            resp = await self._client.post(
                self._base + path, json=json, content=content, headers={**self._headers(), **(headers or {})}
            )
        except _TRANSPORT_ERRORS as exc:
            raise EmbyError(f"连接失败: {exc} (检查 emby.url)") from exc
        self._raise_for_status(resp.status_code, resp.text)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f'MediaBrowser Token="{self._api_key}"', "X-Emby-Token": self._api_key}

    @staticmethod
    def _raise_for_status(status: int, body: str) -> None:
        if status < 300:
            return
        # 401 与 403 分开说明: 前者是密钥本身无效, 后者是密钥有效但账号权限不够 (两个写接口要求
        # 管理员级密钥); 合成一条会让用户以为密钥写错了.
        if status == 401:
            raise EmbyError("认证失败 (HTTP 401): emby.api_key 无效")
        if status == 403:
            raise EmbyError("权限不足 (HTTP 403): emby.api_key 需要来自管理员账号")
        raise EmbyError(f"HTTP {status}: {body[:200]}")
