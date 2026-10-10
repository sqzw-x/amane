"""EmbyClient: 上传的 Base64 形态, 身份头, 分页与错误映射."""

import json
from base64 import b64decode
from typing import Any

import pytest
from httpx2 import AsyncClient, ConnectError, MockTransport, Request, Response

from amane.config import EmbyConfig
from amane.emby import EmbyClient, EmbyError, EmbyPerson

_URL = "http://emby.local:8096"
_KEY = "secret-key"


def _config(**overrides: Any) -> EmbyConfig:
    return EmbyConfig(url=_URL, api_key=_KEY, **overrides)


def _client(handler, config: EmbyConfig | None = None) -> EmbyClient:
    """注入 MockTransport: 只验证出站形态, 不联网."""
    return EmbyClient(config or _config(), client=AsyncClient(transport=MockTransport(handler)))


def _ok(request: Request) -> Response:
    return Response(200, json={"Items": []})


class TestAuth:
    @pytest.mark.asyncio
    async def test_sends_both_token_headers(self):
        seen: list[Request] = []

        def handler(request: Request) -> Response:
            seen.append(request)
            return _ok(request)

        async with _client(handler) as client:
            await client.list_persons()

        assert seen[0].headers["x-emby-token"] == _KEY
        assert seen[0].headers["authorization"] == f'MediaBrowser Token="{_KEY}"'
        # 密钥不进查询串 (会落进服务器访问日志)
        assert "api_key" not in seen[0].url.params

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, "emby.api_key 无效"),
            (403, "需要来自管理员账号"),
        ],
    )
    async def test_auth_errors_distinguish_key_from_permission(self, status: int, expected: str):
        """401 与 403 的处置不同: 换密钥 vs 提权, 文案不能合并."""

        def handler(request: Request) -> Response:
            return Response(status, text="denied")

        async with _client(handler) as client:
            with pytest.raises(EmbyError, match=expected):
                await client.list_persons()

    @pytest.mark.asyncio
    async def test_other_status_keeps_body(self):
        def handler(request: Request) -> Response:
            return Response(500, text="boom")

        async with _client(handler) as client:
            with pytest.raises(EmbyError, match="HTTP 500: boom"):
                await client.list_persons()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", ["http://host:port", "http://[::1"])
    async def test_malformed_url_becomes_emby_error(self, url: str):
        """InvalidURL 不在 HTTPError 之下: 端口写错时不能让它穿到调用方."""
        transport = MockTransport(lambda request: Response(200, json={"Items": []}))
        async with EmbyClient(EmbyConfig(url=url, api_key=_KEY), client=AsyncClient(transport=transport)) as client:
            with pytest.raises(EmbyError, match=r"emby\.url"):
                await client.list_persons()


class TestUploadPrimaryImage:
    @pytest.mark.asyncio
    async def test_body_is_base64_text(self):
        """两家的该端点按 Base64 解码; 原始字节会被解成错误内容而接口仍返回成功."""
        seen: list[Request] = []

        def handler(request: Request) -> Response:
            seen.append(request)
            return Response(204)

        async with _client(handler) as client:
            await client.upload_primary_image("abc", b"jpeg-bytes", content_type="image/jpeg")

        request = seen[0]
        assert request.method == "POST"
        assert request.url.path == "/Items/abc/Images/Primary"
        assert request.headers["content-type"] == "image/jpeg"
        assert b64decode(request.content) == b"jpeg-bytes"

    @pytest.mark.asyncio
    async def test_failure_raises_with_status(self):
        def handler(request: Request) -> Response:
            return Response(500, text="boom")

        async with _client(handler) as client:
            with pytest.raises(EmbyError, match="HTTP 500"):
                await client.upload_primary_image("abc", b"x", content_type="image/jpeg")


class TestListPersons:
    @pytest.mark.asyncio
    async def test_paginates_and_parses_person_fields(self):
        pages = [
            {
                "Items": [
                    {
                        "Id": f"id-{i}",
                        "Name": f"P{i}",
                        "ImageTags": {"Primary": "3ad658cbfb0173e14bb09d255e84d64a"},
                        "Overview": "bio",
                    }
                    for i in range(500)
                ]
            },
            {
                "Items": [
                    {
                        "Id": "last",
                        "Name": "Last",
                        "ProductionLocations": ["Tokyo"],
                        "PremiereDate": "1990-01-02",
                    }
                ]
            },
            {"Items": []},
        ]
        starts: list[str] = []

        def handler(request: Request) -> Response:
            starts.append(request.url.params["startIndex"])
            assert request.url.params["fields"] == "Overview,ProductionLocations"
            assert request.url.params["enableImages"] == "true"
            return Response(200, json=pages[min(len(starts) - 1, len(pages) - 1)])

        async with _client(handler) as client:
            persons = await client.list_persons()

        assert starts == ["0", "500", "501"]
        assert len(persons) == 501
        assert persons[0].has_primary_image is True
        assert persons[0].overview == "bio"
        assert persons[-1].production_locations == ["Tokyo"]
        assert persons[-1].premiere_date == "1990-01-02"

    @pytest.mark.asyncio
    async def test_missing_items_key_raises(self):
        def handler(request: Request) -> Response:
            return Response(200, json={"TotalRecordCount": 3})

        async with _client(handler) as client:
            with pytest.raises(EmbyError, match="Items"):
                await client.list_persons()

    @pytest.mark.asyncio
    async def test_capped_page_size_does_not_skip_persons(self):
        """服务器把 limit 收到自己的上限时按实际条数推进, 不能按请求值跳过中间的人物."""
        all_persons = [{"Id": f"id-{i}", "Name": f"P{i}"} for i in range(250)]
        seen_starts: list[str] = []

        def handler(request: Request) -> Response:
            start = int(request.url.params["startIndex"])
            seen_starts.append(request.url.params["startIndex"])
            return Response(200, json={"Items": all_persons[start : start + 100], "TotalRecordCount": len(all_persons)})

        async with _client(handler) as client:
            persons = await client.list_persons()

        assert seen_starts == ["0", "100", "200"]
        assert [p.id for p in persons] == [f"id-{i}" for i in range(250)]

    @pytest.mark.asyncio
    async def test_capped_page_size_without_total_is_not_truncated(self):
        """没有总数、页面又被服务器截断时, 继续按实际条数取到空页为止."""
        all_persons = [{"Id": f"id-{i}", "Name": f"P{i}"} for i in range(120)]
        seen_starts: list[str] = []

        def handler(request: Request) -> Response:
            start = int(request.url.params["startIndex"])
            seen_starts.append(request.url.params["startIndex"])
            return Response(200, json={"Items": all_persons[start : start + 100]})

        async with _client(handler) as client:
            persons = await client.list_persons()

        assert seen_starts == ["0", "100", "120"]
        assert len(persons) == 120

    @pytest.mark.asyncio
    async def test_repeated_page_ends_iteration(self):
        """服务器重复返回同一整页而声明的总数取不到时, 不能一直请求下去."""
        page = [{"Id": f"id-{i}", "Name": f"P{i}"} for i in range(100)]
        calls: list[str] = []

        def handler(request: Request) -> Response:
            calls.append(request.url.params["startIndex"])
            return Response(200, json={"Items": page, "TotalRecordCount": 10_000})

        async with _client(handler) as client:
            persons = await client.list_persons()

        assert calls == ["0", "100", "200"]
        assert len(persons) == 100

    @pytest.mark.asyncio
    async def test_single_page_without_new_persons_does_not_end_iteration(self):
        """分页顺序不稳定时, 单次没有新条目仍要继续 — 后一页可能带出未读到的部分."""
        first = [{"Id": f"id-{i}", "Name": f"P{i}"} for i in range(100)]
        rest = [{"Id": f"id-{i}", "Name": f"P{i}"} for i in range(100, 110)]
        pages = [first, first, rest, []]
        starts: list[str] = []

        def handler(request: Request) -> Response:
            starts.append(request.url.params["startIndex"])
            return Response(200, json={"Items": pages[min(len(starts) - 1, len(pages) - 1)]})

        async with _client(handler) as client:
            persons = await client.list_persons()

        assert starts == ["0", "100", "200", "210"]
        assert len(persons) == 110

    @pytest.mark.asyncio
    async def test_empty_page_ends_iteration(self):
        """没有总数时, 空页也终止循环, 不会一直请求下去."""
        calls: list[str] = []

        def handler(request: Request) -> Response:
            calls.append(request.url.params["startIndex"])
            return Response(200, json={"Items": []})

        async with _client(handler) as client:
            assert await client.list_persons() == []

        assert calls == ["0"]

    @pytest.mark.asyncio
    async def test_transport_error_becomes_emby_error(self):
        """传输层异常统一转成 EmbyError: 调用方只需要处理一种异常."""

        def handler(request: Request) -> Response:
            raise ConnectError("connection refused", request=request)

        async with _client(handler) as client:
            with pytest.raises(EmbyError, match="连接失败"):
                await client.list_persons()

    @pytest.mark.asyncio
    async def test_non_json_response_raises(self):
        def handler(request: Request) -> Response:
            return Response(200, text="<html>proxy error</html>")

        async with _client(handler) as client:
            with pytest.raises(EmbyError, match="JSON"):
                await client.list_persons()


class TestSearchPersons:
    @pytest.mark.asyncio
    async def test_passes_search_term_and_limit(self):
        seen: list[Request] = []

        def handler(request: Request) -> Response:
            seen.append(request)
            return Response(200, json={"Items": [{"Id": "1", "Name": "Alice"}]})

        async with _client(handler) as client:
            persons = await client.search_persons("Alice", limit=20)

        assert seen[0].url.params["searchTerm"] == "Alice"
        assert seen[0].url.params["limit"] == "20"
        assert [p.id for p in persons] == ["1"]


class TestUpdatePerson:
    @pytest.mark.asyncio
    async def test_posts_only_given_fields(self):
        seen: list[Request] = []

        def handler(request: Request) -> Response:
            seen.append(request)
            return Response(204)

        async with _client(handler) as client:
            await client.update_person("abc", {"Overview": "bio", "ProductionLocations": ["Osaka"]})

        request = seen[0]
        assert request.method == "POST"
        assert request.url.path == "/Items/abc"
        assert json.loads(request.content) == {"Overview": "bio", "ProductionLocations": ["Osaka"]}


class TestFromDto:
    def test_parses_person(self):
        person = EmbyPerson.from_dto(
            {"Id": "1", "Name": "Alice", "ImageTags": {"Primary": "x"}, "Overview": "bio", "PremiereDate": "1990-01-02"}
        )
        assert person is not None
        assert (person.id, person.name, person.has_primary_image, person.premiere_date) == (
            "1",
            "Alice",
            True,
            "1990-01-02",
        )

    @pytest.mark.parametrize(
        "raw",
        [
            {"Name": "no id"},
            {"Id": "1"},
            {"Id": "", "Name": "blank id"},
            {"Id": 1, "Name": "typed id"},
            {"Id": "1", "Name": 2},
        ],
    )
    def test_skips_incomplete_entries(self, raw: dict):
        assert EmbyPerson.from_dto(raw) is None

    def test_missing_image_tags_means_no_image(self):
        person = EmbyPerson.from_dto({"Id": "1", "Name": "Alice"})
        assert person is not None and person.has_primary_image is False
