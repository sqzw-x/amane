"""独立翻译端点 (``/api/translation``) 的 HTTP 行为."""

from typing import Any

import pytest
from httpx2 import AsyncClient
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from amane.db.models import FacetKind
from amane.db.repository import Repository
from amane.enums import ActorField, MetadataField
from amane.llm import LLMTranslator


class RecordingTranslator:
    """记录调用并按注入的返回值应答的协议级替身."""

    def __init__(self, result: str | None = "译文", *, raises: bool = False) -> None:
        self.result = result
        self.raises = raises
        self.calls: list[tuple[str, str, str, bool]] = []

    async def translate(self, text: str, target: Any, field: Any, *, use_cache: bool = True) -> str | None:
        self.calls.append((text, str(target), str(field), use_cache))
        if self.raises:
            raise RuntimeError("llm down")
        return self.result


def _install(app: Any, translator: Any) -> None:
    app.state.runtime.translator = translator


def _unreachable_model() -> FunctionModel:
    """简繁转换路径不得请求上游; 被调用即断言失败."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:  # pragma: no cover - 不应执行
        raise AssertionError("简繁转换不应请求上游模型")

    return FunctionModel(respond)


async def _seed_actor(repo: Repository, name: str, overview: str) -> int:
    await repo.upsert_metadata(number=f"TR-A-{name}", actors=[name])
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    actor = await repo.get_actor(next(facet.id for facet in actors if facet.name == name))
    assert actor is not None and actor.id is not None
    actor.overview = overview
    await repo.save_actor(actor)
    return actor.id


class TestMetadataTranslation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_translates_and_writes_back(self, app: Any, client: AsyncClient, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="TR-001", title="似鳥の告白", plot="日本語のあらすじ")
        assert meta.id is not None
        stub = RecordingTranslator("译文")
        _install(app, stub)

        resp = await client.post(f"translation/metadata/{meta.id}")

        assert resp.status_code == 200
        assert resp.json()["outcomes"] == [
            {"field": "title", "status": "translated"},
            {"field": "plot", "status": "translated"},
        ]
        stored = await repo.get_metadata(meta.id)
        assert stored is not None
        assert (stored.title, stored.plot) == ("译文", "译文")
        # 手动入口强制跳过缓存读取, 但仍回写.
        assert [(text, use_cache) for text, _target, _field, use_cache in stub.calls] == [
            ("似鳥の告白", False),
            ("日本語のあらすじ", False),
        ]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_simplified_traditional_conversion_is_written(
        self, app: Any, client: AsyncClient, repo: Repository
    ) -> None:
        """中文简繁由翻译器内部的 zhconv 分支处理, 不得预筛成 UNCHANGED."""
        meta = await repo.upsert_metadata(number="TR-002", title="後愛上你")
        assert meta.id is not None
        _install(app, LLMTranslator(_unreachable_model(), rate_limit=100.0))

        resp = await client.post(f"translation/metadata/{meta.id}")

        assert resp.json()["outcomes"] == [{"field": "title", "status": "translated"}]
        stored = await repo.get_metadata(meta.id)
        assert stored is not None
        assert stored.title == "后爱上你"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_locked_field_is_skipped(self, app: Any, client: AsyncClient, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="TR-003", title="似鳥の告白", plot="日本語のあらすじ")
        assert meta.id is not None
        await repo.set_metadata_locks(meta.id, {MetadataField.TITLE})
        stub = RecordingTranslator("译文")
        _install(app, stub)

        resp = await client.post(f"translation/metadata/{meta.id}")

        assert resp.json()["outcomes"] == [
            {"field": "title", "status": "locked"},
            {"field": "plot", "status": "translated"},
        ]
        assert [field for _text, _target, field, _c in stub.calls] == ["plot"]
        stored = await repo.get_metadata(meta.id)
        assert stored is not None
        assert (stored.title, stored.plot) == ("似鳥の告白", "译文")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_identical_translation_is_not_written(self, app: Any, client: AsyncClient, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="TR-004", title="似鳥の告白")
        assert meta.id is not None
        _install(app, RecordingTranslator("似鳥の告白"))

        resp = await client.post(f"translation/metadata/{meta.id}")

        assert resp.json()["outcomes"] == [{"field": "title", "status": "unchanged"}]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_failure_keeps_original(self, app: Any, client: AsyncClient, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="TR-005", title="似鳥の告白", plot="日本語のあらすじ")
        assert meta.id is not None
        _install(app, RecordingTranslator(raises=True))

        resp = await client.post(f"translation/metadata/{meta.id}")

        assert resp.status_code == 200
        assert [item["status"] for item in resp.json()["outcomes"]] == ["failed", "failed"]
        stored = await repo.get_metadata(meta.id)
        assert stored is not None
        assert (stored.title, stored.plot) == ("似鳥の告白", "日本語のあらすじ")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_text_is_not_reported(self, app: Any, client: AsyncClient, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="TR-006", title="似鳥の告白")
        assert meta.id is not None
        _install(app, RecordingTranslator("译文"))

        resp = await client.post(f"translation/metadata/{meta.id}")

        assert resp.json()["outcomes"] == [{"field": "title", "status": "translated"}]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_already_target_language_is_unchanged(self, app: Any, client: AsyncClient, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="TR-007", title="中文标题")
        assert meta.id is not None
        _install(app, RecordingTranslator(None))

        resp = await client.post(f"translation/metadata/{meta.id}")

        assert resp.json()["outcomes"] == [{"field": "title", "status": "unchanged"}]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_id_is_404(self, app: Any, client: AsyncClient) -> None:
        _install(app, RecordingTranslator("译文"))

        resp = await client.post("translation/metadata/999999")

        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_translator_disabled_is_503(self, client: AsyncClient, repo: Repository) -> None:
        """llm.enabled 默认关闭: runtime.translator 为 None, 端点没有可执行的动作."""
        meta = await repo.upsert_metadata(number="TR-008", title="似鳥の告白")
        assert meta.id is not None

        resp = await client.post(f"translation/metadata/{meta.id}")

        assert resp.status_code == 503


class TestActorTranslation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_translates_overview(self, app: Any, client: AsyncClient, repo: Repository) -> None:
        actor_id = await _seed_actor(repo, "似鳥", "似鳥は日本の女優。")
        stub = RecordingTranslator("译文")
        _install(app, stub)

        resp = await client.post(f"translation/actors/{actor_id}")

        assert resp.status_code == 200
        assert resp.json()["outcomes"] == [{"field": "overview", "status": "translated"}]
        stored = await repo.get_actor(actor_id)
        assert stored is not None
        assert stored.overview == "译文"
        assert [(text, target, field) for text, target, field, _c in stub.calls] == [
            ("似鳥は日本の女優。", "zh_cn", "overview")
        ]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_locked_overview_is_skipped(self, app: Any, client: AsyncClient, repo: Repository) -> None:
        actor_id = await _seed_actor(repo, "似鳥", "似鳥は日本の女優。")
        await repo.set_actor_locks(actor_id, {ActorField.OVERVIEW})
        stub = RecordingTranslator("译文")
        _install(app, stub)

        resp = await client.post(f"translation/actors/{actor_id}")

        assert resp.json()["outcomes"] == [{"field": "overview", "status": "locked"}]
        assert stub.calls == []
        stored = await repo.get_actor(actor_id)
        assert stored is not None
        assert stored.overview == "似鳥は日本の女優。"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_id_is_404(self, app: Any, client: AsyncClient) -> None:
        _install(app, RecordingTranslator("译文"))

        resp = await client.post("translation/actors/999999")

        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_translator_disabled_is_503(self, client: AsyncClient, repo: Repository) -> None:
        actor_id = await _seed_actor(repo, "似鳥", "似鳥は日本の女優。")

        resp = await client.post(f"translation/actors/{actor_id}")

        assert resp.status_code == 503


class TestTranslatorOwnership:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_rebuild_key(self, app: Any, client: AsyncClient) -> None:
        """LLM 无关的热配置变更不换翻译面; llm 段或代理变更才换."""
        runtime = app.state.runtime
        assert runtime.translator is None

        resp = await client.patch("config", json={"llm": {"enabled": True, "api_key": "k", "model": "m"}})
        assert resp.status_code == 200
        first = runtime.translator
        assert first is not None

        resp = await client.patch("config", json={"logging": {"level": "DEBUG"}})
        assert resp.status_code == 200
        assert runtime.translator is first

        resp = await client.patch("config", json={"llm": {"model": "m2"}})
        assert resp.status_code == 200
        assert runtime.translator is not first
        assert runtime.translator is not None
