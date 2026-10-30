import re
from typing import TYPE_CHECKING, Any, override
from urllib.parse import urlsplit

from ...enums import ActorGender, SiteName
from ...net.connectivity import ConnectivityOutcome, SkipReason, assess_response
from ...net.errors import FailureReason, RequestError
from ...plugins.models import SourceTrait
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, FilmActor, MediaMetadata, SearchQuery
from ..parsing import fold_number, fold_studio, is_same_number, parse_western_number
from .kin8 import movie_id as kin8_movie_id

_PERFORMER_GENDER: dict[str, ActorGender] = {
    "FEMALE": ActorGender.FEMALE,
    "MALE": ActorGender.MALE,
}

# kin8 条目的 ``code`` 是站内 4 位 id, 补零规则只在 ``sites/kin8.py::movie_id`` 维护;
# 他厂会用同一数字当番号, 因此该形态的命中必须同时限定片商.
_KIN8_STUDIO = fold_studio("Kin8tengoku")

if TYPE_CHECKING:
    from ...parsing.file_info import ContentType

_SCENE_FIELDS = """
    id title code date duration director details
    studio { name }
    tags { name }
    performers { as performer { name gender } }
    images { url }
"""

_SEARCH_QUERY = f"""
query Search($term: String!) {{
  searchScene(term: $term) {{{_SCENE_FIELDS}
  }}
}}"""

# stash-box 上游已删除单条指纹查询, 批量形态是唯一入口: 外层按入参逐条对应, 内层可空.
# TPDB 是自研兼容层, 可空标注比上游宽松, 因此参数按上游最严的形态传, 应答按可空解析.
_FINGERPRINT_QUERY = f"""
query Find($fingerprints: [[FingerprintQueryInput!]!]!) {{
  findScenesBySceneFingerprints(fingerprints: $fingerprints) {{{_SCENE_FIELDS}
  }}
}}"""

_FIND_BY_ID_QUERY = f"""
query FindByID($id: ID!) {{
  findScene(id: $id) {{{_SCENE_FIELDS}
  }}
}}"""

_TYPE_FILTER: dict[str, str] = {
    "censored": "JAV",
    "uncensored": "Scene",
    "western": "Scene",
    "fc2": "Scene",
    "amateur": "Scene",
    "hentai": "JAV",
}


class ThePornDBCrawler(Crawler):
    """Stash-box GraphQL. ``content_type`` 映射到 ``?type=``; 缺省不加 filter."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.THEPORNDB,
            base_url="https://theporndb.net/graphql",
            traits=frozenset({SourceTrait.USES_FILE_HASH}),
        )

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        """真发一次 GraphQL 请求: 本源只有带 token 才会请求, 探测必须同样验证凭据与连通性.

        查一个不存在的 id: 应答便宜, 且能区分「token 被拒」与「站点不可达」. 地址经 ``_gql_url``
        取: ``?type=`` 是每次请求的过滤条件, 探测验证的是可达性与凭据, 不加该条件.
        """
        token = self.config.api_token if self.config else None
        if not token:
            return ConnectivityOutcome.skipped(SkipReason.MISSING_CREDENTIAL, "api_token")
        url = self._gql_url(None)
        try:
            resp = await self.client.web_client.request(
                "POST",
                url,
                json={"query": _FIND_BY_ID_QUERY, "variables": {"id": "0"}},
                headers={"Authorization": f"Bearer {token}"},
                max_attempts=1,
            )
        except RequestError as exc:
            return ConnectivityOutcome.failed(exc.reason, url=url, http_status=exc.http_status)

        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload.get("errors") and not payload.get("data"):
            # HTTP 状态正常而在 GraphQL 层失败: 原因不写在状态码上. 上游的错误文本是英文原文, 截断后作为
            # 可行动信息报给用户 (不含 Authorization 头), 完整应答写入日志.
            errors = payload["errors"]
            self.logger.warning("theporndb graphql errors", errors=errors)
            message = ""
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = str(errors[0].get("message") or "")
            return ConnectivityOutcome.failed(FailureReason.API_ERROR, url=url, detail=message[:200] or None)
        return assess_response(url, resp)

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        """检索应答已含详情字段, 因此不必再按 id 取一次.

        指定网址时由基类直接取该场景, 不经过检索.
        """
        if query.url:
            return await super().fetch(query, options)

        token = self.config.api_token if self.config else None
        if not token:
            return None

        scene = await self._search_scene(query, token)
        return self._scene_to_metadata(scene) if scene is not None else None

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """与 ``fetch`` 共用检索流程与命中判据."""
        token = self.config.api_token if self.config else None
        if not token:
            return None

        return _scene_url(await self._search_scene(query, token))

    async def _search_scene(self, query: SearchQuery, token: str) -> dict[str, Any] | None:
        """指纹优先于文本检索; 命中判据一律经 ``pick_scene``."""
        headers = {"Authorization": f"Bearer {token}"}
        gql_url = self._gql_url(query.content_type)

        if query.file_hash:
            data = await self.client.post_json(
                gql_url,
                json={
                    "query": _FINGERPRINT_QUERY,
                    "variables": {"fingerprints": [[{"hash": query.file_hash, "algorithm": "OSHASH"}]]},
                },
                headers=headers,
            )
            scene = pick_fingerprint_scene(_gql_field(data, "findScenesBySceneFingerprints"))
            if scene is not None:
                return scene

        data = await self.client.post_json(
            gql_url,
            # 检索词保留番号原样: kin8 的站前缀才是命中它的条件, 换成裸 ``movie_id`` 后 TPDB 返回 0 条.
            json={"query": _SEARCH_QUERY, "variables": {"term": query.number}},
            headers=headers,
        )
        return pick_scene(_gql_field(data, "searchScene"), query.number)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        token = self.config.api_token if self.config else None
        scene_id = _scene_id_from_url(url)
        if not token or scene_id is None:
            return None

        headers = {"Authorization": f"Bearer {token}"}

        data = await self.client.post_json(
            self.base_url,
            json={
                "query": _FIND_BY_ID_QUERY,
                "variables": {"id": scene_id},
            },
            headers=headers,
        )

        scene = _gql_field(data, "findScene")
        if not isinstance(scene, dict):
            return None
        metadata = self._scene_to_metadata(scene)
        # 欧美条目的 code 是 ``studio:title-slug``, 与文件名里的 ``Studio.YY.MM.DD`` 不同构;
        # 指定网址时条目按后者建立, 否则会生成一个与文件番号对不上的新条目.
        western_number = _western_number(scene)
        return metadata.model_copy(update={"number": western_number}) if western_number else metadata

    def _gql_url(self, content_type: ContentType | None) -> str:
        if content_type:
            gql_type = _TYPE_FILTER.get(str(content_type))
            if gql_type:
                return f"{self.base_url}?type={gql_type}"
        return self.base_url

    @staticmethod
    def _scene_to_metadata(scene: dict[str, Any]) -> MediaMetadata:
        number = scene.get("code") or scene.get("title", "")

        studio = None
        if isinstance(scene.get("studio"), dict):
            studio = scene["studio"].get("name")

        actors: list[FilmActor] = []
        for pa in scene.get("performers", []) or []:
            perf = (pa or {}).get("performer", {}) or {}
            name = perf.get("name") or pa.get("as", "")
            if not name:
                continue
            gender = _PERFORMER_GENDER.get(str(perf.get("gender") or ""))
            actors.append(FilmActor(name=name, gender=gender or ActorGender.UNKNOWN))

        tags = [t.get("name", "") for t in (scene.get("tags") or []) if t.get("name")]

        images = scene.get("images") or []
        thumb_url = images[0]["url"] if images else None

        return MediaMetadata(
            number=number,
            title=scene.get("title") or None,
            actors=actors,
            studio=studio,
            release=scene.get("date") or None,
            runtime=_runtime_minutes(scene.get("duration")),
            tags=tags,
            directors=[scene["director"]] if scene.get("director") else [],
            plot=scene.get("details") or None,
            thumb_urls=[thumb_url] if thumb_url else [],
            external_id=scene.get("id") or None,
            source_url=f"https://theporndb.net/scenes/{scene['id']}" if scene.get("id") else None,
        )


def pick_scene(results: object, number: str) -> dict[str, Any] | None:
    """检索结果 → 命中的条目; 确认不了返回 None, 不回退首条.

    ``code`` 与入参按同一番号判定, 欧美日期号的两种年份写法等价, 命中取应答里的首条.
    欧美条目的 ``code`` 是 ``studio:title-slug``, 与文件名番号不同构, 这类日期号按
    片商 + 发布日期确认, 只认唯一命中. ``KIN8-####`` 按站内 4 位 id 判定.
    """
    scenes = [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []
    kin8_id = kin8_movie_id(number)
    for scene in scenes:
        code = scene.get("code")
        if not isinstance(code, str) or not code:
            continue
        if is_same_number(code, number):
            return scene
        if kin8_id is not None and _is_kin8_scene(scene, code, kin8_id):
            return scene

    western = parse_western_number(number)
    if western is None:
        return None
    hits = [
        scene
        for scene in scenes
        if _scene_date(scene) == western.date and fold_studio(_scene_studio(scene)) == western.studio
    ]
    return hits[0] if len(hits) == 1 else None


def pick_fingerprint_scene(results: object) -> dict[str, Any] | None:
    """批量指纹应答 → 首条条目; 只发一个指纹, 因此只看外层第一组.

    上游把内外两层都声明为非空, TPDB 的兼容实现两层都可空, 逐层按可空展开,
    结构不符或没有命中都返回 None.
    """
    groups = _as_list(results)
    if not groups:
        return None
    for scene in _as_list(groups[0]):
        if isinstance(scene, dict):
            return scene
    return None


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _is_kin8_scene(scene: dict[str, Any], code: str, kin8_id: str) -> bool:
    return fold_number(code) == kin8_id and fold_studio(_scene_studio(scene)) == _KIN8_STUDIO


def _scene_studio(scene: dict[str, Any]) -> str:
    studio = scene.get("studio")
    name = studio.get("name") if isinstance(studio, dict) else None
    return name if isinstance(name, str) else ""


def _scene_date(scene: dict[str, Any]) -> str:
    date = scene.get("date")
    return date if isinstance(date, str) else ""


def _runtime_minutes(duration: object) -> int | None:
    """秒 → 分钟; 缺省与 0 视为没有时长."""
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        return None
    return duration // 60


def _scene_url(scene: dict[str, Any] | None) -> str | None:
    scene_id = scene.get("id") if scene is not None else None
    return f"gql://scene/{scene_id}" if isinstance(scene_id, str) and scene_id else None


def _scene_id_from_url(url: str) -> str | None:
    """内部检索地址 ``gql://scene/<id>`` 或公开作品页 ``/scenes/<id>``."""
    internal_prefix = "gql://scene/"
    if url.startswith(internal_prefix):
        return url.removeprefix(internal_prefix) or None
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) >= 2 and segments[0] == "scenes":
        return segments[1]
    return None


def _western_number(scene: dict[str, Any]) -> str | None:
    """欧美条目 → ``Studio.YY.MM.DD``; code 不带 ``:`` 时不是欧美条目, 返回 ``None``."""
    code = scene.get("code")
    date = scene.get("date")
    if not isinstance(code, str) or ":" not in code or not isinstance(date, str):
        return None
    studio, _, _ = code.partition(":")
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", date)
    if not studio or match is None:
        return None
    return f"{studio}.{match.group(1)[2:]}.{match.group(2)}.{match.group(3)}".capitalize()


def _gql_field(payload: object, key: str) -> object:
    """取 GraphQL 应答的 ``data.<key>``; 结构不符返回 None."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data.get(key) if isinstance(data, dict) else None
