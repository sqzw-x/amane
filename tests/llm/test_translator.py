"""LLM 翻译器单元测试.

请求经 pydantic-ai ``FunctionModel`` 或 SDK 客户端的 MockTransport 驱动, 不触网. 覆盖:
- 跨语系走 LLM, 中文走 zhconv, 已是目标语言不动 的分流
- 重试次数直通 SDK, 重试耗尽返回 None (不抛异常), 思维链剥离, 空结果
- build_translator 的装配开关与代理客户端
- 自定义提示词进入请求, 以及提示词变化后的缓存失效
"""

from collections.abc import Mapping, Sequence

import aiosqlite
import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, SystemPromptPart, TextPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from amane.enums import ApiType, Language, MetadataField
from amane.llm import LLMTranslator, TranslationCache, build_system_prompt, build_translator
from amane.llm import translator as translator_module

_SYSTEM_ZH = build_system_prompt(Language.ZH_CN, MetadataField.TITLE)


def _prompts(messages: Sequence[ModelMessage]) -> tuple[str, str]:
    """取出最后一次请求的 system 与 user 文本."""
    request = messages[-1]
    assert isinstance(request, ModelRequest)
    system = "".join(part.content for part in request.parts if isinstance(part, SystemPromptPart))
    user = "".join(
        part.content for part in request.parts if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )
    return system, user


def _recording_model(result: str | None = "TRANSLATED") -> tuple[FunctionModel, list[tuple[str, str]]]:
    """记录请求内容并返回固定正文的模型."""
    calls: list[tuple[str, str]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(_prompts(messages))
        return ModelResponse(parts=[TextPart(result or "")])

    return FunctionModel(respond), calls


def _translator(
    model: Model,
    cache: TranslationCache | None = None,
    *,
    system_prompt: str | None = None,
    field_prompts: Mapping[MetadataField, str] | None = None,
) -> LLMTranslator:
    """rate_limit 拉高: 用例内的连续调用不等待限速."""
    return LLMTranslator(model, cache, rate_limit=100.0, system_prompt=system_prompt, field_prompts=field_prompts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,target,model_called",
    [
        # 跨语系 → 调模型
        ("こんにちは世界", Language.ZH_CN, True),
        ("Hello world", Language.ZH_CN, True),
        # 中文内部 → zhconv, 不调模型
        ("後愛上你", Language.ZH_CN, False),
        ("你好世界", Language.ZH_CN, False),
        # 中文源 + 非中文目标: 设计不覆盖该方向 (现实目标皆中文), 不调模型
        ("你好世界", Language.JP, False),
        # 已是目标语言 → 不调模型
        ("Hello world", Language.EN, False),
        ("こんにちは", Language.JP, False),
        # 空串 → 不调模型
        ("", Language.ZH_CN, False),
        ("   ", Language.ZH_CN, False),
    ],
)
async def test_translate_routing(text, target, model_called):
    model, calls = _recording_model()
    t = _translator(model)
    await t.translate(text, target, MetadataField.TITLE)
    assert bool(calls) == model_called


@pytest.mark.asyncio
async def test_zhconv_conversion():
    """繁→简由 zhconv 完成, 不经模型."""
    model, calls = _recording_model()
    t = _translator(model)
    assert await t.translate("後愛上你", Language.ZH_CN, MetadataField.TITLE) == "后爱上你"
    assert not calls


@pytest.mark.asyncio
async def test_common_chars_return_none():
    """共用字 (简繁同形): 转换后等于原文 → None (调用方保留原值)."""
    t = _translator(_recording_model()[0])
    assert await t.translate("你好世界", Language.ZH_CN, MetadataField.TITLE) is None


@pytest.mark.asyncio
async def test_already_target_lang_returns_none():
    t = _translator(_recording_model()[0])
    assert await t.translate("Hello world", Language.EN, MetadataField.TITLE) is None
    assert await t.translate("こんにちは", Language.JP, MetadataField.PLOT) is None


@pytest.mark.asyncio
async def test_llm_path_returns_model_result():
    t = _translator(_recording_model("译文")[0])
    assert await t.translate("Hello", Language.ZH_CN, MetadataField.TITLE) == "译文"


@pytest.mark.asyncio
async def test_empty_model_result_returns_none():
    """模型返回空正文 → translate 返回 None, 不抛."""
    t = _translator(_recording_model("")[0])
    assert await t.translate("Hello", Language.ZH_CN, MetadataField.TITLE) is None


@pytest.mark.asyncio
async def test_request_failure_returns_none(monkeypatch):
    """请求持续失败时 translate 返回 None (调用方保留原值), 不向上抛异常."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        # retry-after 让 SDK 不需要真实退避等待.
        return httpx2.Response(500, json={"error": {"message": "blocked"}}, headers={"retry-after": "0.01"})

    def mock_client(
        *, proxy: str | None = None, timeout: httpx2.Timeout | None = None, follow_redirects: bool = False
    ) -> httpx2.AsyncClient:
        """顶替翻译自建的传输客户端: 请求由 MockTransport 承载, 不触网."""
        return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(translator_module, "AsyncClient", mock_client)
    t = build_translator(
        enabled=True,
        api_type=ApiType.CHAT,
        api_key="test-key",
        base_url="https://api.example/v1",
        model="test-model",
        rate_limit=100.0,
    )
    assert t is not None
    assert await t.translate("Hello", Language.ZH_CN, MetadataField.TITLE) is None


@pytest.mark.asyncio
async def test_think_block_stripped():
    """思维链写在正文里 (不是 ``ThinkingPart``) 时剥离, 只留译文."""
    t = _translator(_recording_model("<think>先分析</think> 译文 ")[0])
    assert await t.translate("Hello", Language.ZH_CN, MetadataField.TITLE) == "译文"


@pytest.mark.parametrize(
    "enabled,api_key,expected_none",
    [
        (False, "key", True),  # 未启用
        (True, None, True),  # 缺密钥
        (True, "", True),  # 空密钥
        (True, "key", False),  # 正常装配
    ],
)
def test_build_translator_gating(enabled, api_key, expected_none):
    t = build_translator(
        enabled=enabled,
        api_type=ApiType.CHAT,
        api_key=api_key,
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        rate_limit=2.0,
    )
    assert (t is None) == expected_none


@pytest.mark.asyncio
async def test_build_translator_builds_proxy_client(monkeypatch):
    """proxy 与超时进入翻译自建的传输客户端, 并作为 ``http_client`` 交给共享 model 工厂."""
    captured: dict[str, object] = {}
    real_client = httpx2.AsyncClient

    def recording_client(
        *, proxy: str | None = None, timeout: httpx2.Timeout | None = None, follow_redirects: bool = False
    ) -> httpx2.AsyncClient:
        client = real_client(proxy=proxy, timeout=timeout, follow_redirects=follow_redirects)
        captured.update(proxy=proxy, client=client)
        return client

    def fake_build_model(
        api_type: ApiType,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        http_client: httpx2.AsyncClient | None = None,
    ) -> FunctionModel:
        captured.update(api_type=api_type, http_client=http_client)
        return _recording_model()[0]

    monkeypatch.setattr(translator_module, "AsyncClient", recording_client)
    monkeypatch.setattr(translator_module, "build_model", fake_build_model)
    t = build_translator(
        enabled=True,
        api_type=ApiType.ANTHROPIC,
        api_key="key",
        base_url="https://api.example/v1",
        model="test-model",
        rate_limit=2.0,
        proxy="http://proxy.example:8080",
    )
    assert t is not None
    client = captured["client"]
    assert isinstance(client, httpx2.AsyncClient)
    assert captured["proxy"] == "http://proxy.example:8080"
    assert client.timeout == httpx2.Timeout(60.0)
    assert captured["api_type"] is ApiType.ANTHROPIC
    assert captured["http_client"] is client
    await client.aclose()


# --- 译文缓存 ---


@pytest.fixture
def cache(tmp_path):
    return TranslationCache(tmp_path / "translations.db")


@pytest.mark.asyncio
async def test_cache_roundtrip(cache):
    assert await cache.get("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) is None
    await cache.put("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH, "你好")
    assert await cache.get("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) == "你好"
    await cache.close()


@pytest.mark.asyncio
async def test_cache_key_components(cache):
    """target / field / 提示词 都参与键: 任一不同即为独立条目."""
    await cache.put("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH, "标题译")
    assert await cache.get("Hello", Language.ZH_TW, MetadataField.TITLE, _SYSTEM_ZH) is None  # target 不同
    assert await cache.get("Hello", Language.ZH_CN, MetadataField.PLOT, _SYSTEM_ZH) is None  # field 不同
    assert await cache.get("World", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) is None  # 文本不同
    # 提示词不同: 同一文本在另一份提示词下没有可用译文
    other = build_system_prompt(Language.ZH_CN, MetadataField.TITLE, system_prompt="只用中性词汇.")
    assert await cache.get("Hello", Language.ZH_CN, MetadataField.TITLE, other) is None
    await cache.close()


@pytest.mark.asyncio
async def test_legacy_cache_schema_reset(tmp_path):
    """旧版表不含提示词列: 打开时整表重建, 不因缺列报错."""
    path = tmp_path / "translations.db"
    conn = await aiosqlite.connect(path)
    await conn.execute(
        "CREATE TABLE translations ("
        " text_hash TEXT NOT NULL, target TEXT NOT NULL, field TEXT NOT NULL,"
        " translation TEXT NOT NULL, PRIMARY KEY (text_hash, target, field))"
    )
    await conn.execute(
        "INSERT INTO translations (text_hash, target, field, translation) VALUES (?, ?, ?, ?)",
        ("deadbeef", Language.ZH_CN, MetadataField.TITLE, "旧译文"),
    )
    await conn.commit()
    await conn.close()

    cache = TranslationCache(path)
    assert await cache.get("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) is None
    await cache.put("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH, "新译文")
    assert await cache.get("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) == "新译文"
    await cache.close()


@pytest.mark.asyncio
async def test_cache_persists_across_connections(tmp_path):
    """缓存落盘: 新建连接 (模拟重启) 仍可命中."""
    path = tmp_path / "translations.db"
    c1 = TranslationCache(path)
    await c1.put("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH, "你好")
    await c1.close()
    c2 = TranslationCache(path)
    assert await c2.get("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) == "你好"
    await c2.close()


@pytest.mark.asyncio
async def test_translator_uses_cache_on_repeat(cache):
    """重复翻译同一文本只调一次模型 - 这是全缓存重刮不重译的核心保证."""
    model, calls = _recording_model("译文")
    t = _translator(model, cache)
    first = await t.translate("Hello world", Language.ZH_CN, MetadataField.TITLE)
    second = await t.translate("Hello world", Language.ZH_CN, MetadataField.TITLE)
    assert first == second == "译文"
    assert len(calls) == 1  # 第二次命中缓存
    await cache.close()


@pytest.mark.asyncio
async def test_translator_custom_prompt_reaches_model(cache):
    """自定义指令与字段说明进入 system 提示词, 原文仍作 user 提示词."""
    model, calls = _recording_model("译文")
    t = _translator(
        model,
        system_prompt="只用中性词汇.",
        field_prompts={MetadataField.TITLE: "标题不超过 30 字."},
    )
    await t.translate("Hello world", Language.ZH_CN, MetadataField.TITLE)
    system, user = calls[0]
    assert "只用中性词汇." in system
    assert "标题不超过 30 字." in system
    assert user == "Hello world"


@pytest.mark.asyncio
async def test_translator_prompt_change_invalidates_cache(cache):
    """改提示词后同一文本重译: 旧译文不再命中, 新译文写入缓存."""
    model, calls = _recording_model("译文")
    before = _translator(model, cache)
    assert await before.translate("Hello world", Language.ZH_CN, MetadataField.TITLE) == "译文"
    assert len(calls) == 1

    after = _translator(model, cache, system_prompt="只用中性词汇.")
    assert await after.translate("Hello world", Language.ZH_CN, MetadataField.TITLE) == "译文"
    assert len(calls) == 2

    # 变回内置提示词: 旧条目仍在, 不重复调用模型
    assert await before.translate("Hello world", Language.ZH_CN, MetadataField.TITLE) == "译文"
    assert len(calls) == 2
    await cache.close()


@pytest.mark.asyncio
async def test_zhconv_path_skips_cache(cache):
    """中文简繁转换不经缓存 (zhconv 本身廉价且确定)."""
    model, calls = _recording_model()
    t = _translator(model, cache)
    assert await t.translate("後愛上你", Language.ZH_CN, MetadataField.TITLE) == "后爱上你"
    assert not calls
    # 缓存中不应留下中文转换条目
    assert await cache.get("後愛上你", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) is None
    await cache.close()


@pytest.mark.asyncio
async def test_failed_translation_not_cached(cache):
    """模型返回空 → 不写缓存, 下次仍会重试."""
    model, _ = _recording_model(None)
    t = _translator(model, cache)
    assert await t.translate("Hello", Language.ZH_CN, MetadataField.TITLE) is None
    assert await cache.get("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) is None
    await cache.close()


@pytest.mark.asyncio
async def test_use_cache_false_bypasses_read_but_refreshes(cache):
    """use_cache=False: 跳过缓存读取强制重译, 但仍回写刷新缓存."""
    await cache.put("Hello world", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH, "旧译文")
    model, calls = _recording_model("新译文")
    t = _translator(model, cache)
    # 强制重译: 忽略缓存里的"旧译文", 调模型
    result = await t.translate("Hello world", Language.ZH_CN, MetadataField.TITLE, use_cache=False)
    assert result == "新译文"
    assert len(calls) == 1
    # 缓存被刷新为新译文
    assert await cache.get("Hello world", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) == "新译文"
    await cache.close()
