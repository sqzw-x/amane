"""LLM 翻译器单元测试.

后端用 fake 注入, 不触网. 覆盖:
- 跨语系走 LLM, 中文走 zhconv, 已是目标语言不动 的分流
- 后端失败 (返回 None / 抛异常) 下的健壮性
- build_translator 的装配开关
- 自定义提示词进入后端, 以及提示词变化后的缓存失效
"""

import aiosqlite
import pytest

from amane.enums import Language, MetadataField
from amane.llm import LLMTranslator, TranslationCache, build_system_prompt, build_translator

_SYSTEM_ZH = build_system_prompt(Language.ZH_CN, MetadataField.TITLE)


class _FakeBackend:
    """记录调用并返回预置结果的 fake 后端."""

    def __init__(self, result: str | None = "TRANSLATED") -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def ask(self, *, system_prompt: str, user_prompt: str) -> str | None:
        self.calls.append((system_prompt, user_prompt))
        return self.result


class _RaisingBackend:
    async def ask(self, *, system_prompt: str, user_prompt: str) -> str | None:
        raise RuntimeError("boom")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,target,backend_called",
    [
        # 跨语系 → 调后端
        ("こんにちは世界", Language.ZH_CN, True),
        ("Hello world", Language.ZH_CN, True),
        # 中文内部 → zhconv, 不调后端
        ("後愛上你", Language.ZH_CN, False),
        ("你好世界", Language.ZH_CN, False),
        # 中文源 + 非中文目标: 设计不覆盖该方向 (现实目标皆中文), 不调后端
        ("你好世界", Language.JP, False),
        # 已是目标语言 → 不调后端
        ("Hello world", Language.EN, False),
        ("こんにちは", Language.JP, False),
        # 空串 → 不调后端
        ("", Language.ZH_CN, False),
        ("   ", Language.ZH_CN, False),
    ],
)
async def test_translate_routing(text, target, backend_called):
    backend = _FakeBackend()
    t = LLMTranslator(backend)
    await t.translate(text, target, MetadataField.TITLE)
    assert bool(backend.calls) == backend_called


@pytest.mark.asyncio
async def test_zhconv_conversion():
    """繁→简由 zhconv 完成, 不经后端."""
    backend = _FakeBackend()
    t = LLMTranslator(backend)
    assert await t.translate("後愛上你", Language.ZH_CN, MetadataField.TITLE) == "后爱上你"
    assert not backend.calls


@pytest.mark.asyncio
async def test_common_chars_return_none():
    """共用字 (简繁同形): 转换后等于原文 → None (调用方保留原值)."""
    t = LLMTranslator(_FakeBackend())
    assert await t.translate("你好世界", Language.ZH_CN, MetadataField.TITLE) is None


@pytest.mark.asyncio
async def test_already_target_lang_returns_none():
    t = LLMTranslator(_FakeBackend())
    assert await t.translate("Hello world", Language.EN, MetadataField.TITLE) is None
    assert await t.translate("こんにちは", Language.JP, MetadataField.PLOT) is None


@pytest.mark.asyncio
async def test_llm_path_returns_backend_result():
    t = LLMTranslator(_FakeBackend("译文"))
    assert await t.translate("Hello", Language.ZH_CN, MetadataField.TITLE) == "译文"


@pytest.mark.asyncio
async def test_backend_none_result_returns_none():
    """后端返回 None/空 → translate 返回 None, 不抛."""
    t = LLMTranslator(_FakeBackend(None))
    assert await t.translate("Hello", Language.ZH_CN, MetadataField.TITLE) is None
    t2 = LLMTranslator(_FakeBackend(""))
    assert await t2.translate("Hello", Language.ZH_CN, MetadataField.TITLE) is None


@pytest.mark.asyncio
async def test_backend_exception_propagates():
    """后端抛异常时 translate 不吞 (由管线层做机会主义降级)."""
    t = LLMTranslator(_RaisingBackend())
    with pytest.raises(RuntimeError):
        await t.translate("Hello", Language.ZH_CN, MetadataField.TITLE)


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
        api_key=api_key,
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        max_retries=3,
        rate_limit=2.0,
    )
    assert (t is None) == expected_none


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
    """重复翻译同一文本只调一次后端 - 这是全缓存重刮不重译的核心保证."""
    backend = _FakeBackend("译文")
    t = LLMTranslator(backend, cache)
    first = await t.translate("Hello world", Language.ZH_CN, MetadataField.TITLE)
    second = await t.translate("Hello world", Language.ZH_CN, MetadataField.TITLE)
    assert first == second == "译文"
    assert len(backend.calls) == 1  # 第二次命中缓存
    await cache.close()


@pytest.mark.asyncio
async def test_translator_custom_prompt_reaches_backend(cache):
    """自定义指令与字段说明进入 system 提示词, 原文仍作 user 提示词."""
    backend = _FakeBackend("译文")
    t = LLMTranslator(
        backend,
        system_prompt="只用中性词汇.",
        field_prompts={MetadataField.TITLE: "标题不超过 30 字."},
    )
    await t.translate("Hello world", Language.ZH_CN, MetadataField.TITLE)
    system, user = backend.calls[0]
    assert "只用中性词汇." in system
    assert "标题不超过 30 字." in system
    assert user == "Hello world"


@pytest.mark.asyncio
async def test_translator_prompt_change_invalidates_cache(cache):
    """改提示词后同一文本重译: 旧译文不再命中, 新译文写入缓存."""
    backend = _FakeBackend("译文")
    before = LLMTranslator(backend, cache)
    assert await before.translate("Hello world", Language.ZH_CN, MetadataField.TITLE) == "译文"
    assert len(backend.calls) == 1

    after = LLMTranslator(backend, cache, system_prompt="只用中性词汇.")
    assert await after.translate("Hello world", Language.ZH_CN, MetadataField.TITLE) == "译文"
    assert len(backend.calls) == 2

    # 变回内置提示词: 旧条目仍在, 不重复调用后端
    assert await before.translate("Hello world", Language.ZH_CN, MetadataField.TITLE) == "译文"
    assert len(backend.calls) == 2
    await cache.close()


@pytest.mark.asyncio
async def test_zhconv_path_skips_cache(cache):
    """中文简繁转换不经缓存 (zhconv 本身廉价且确定)."""
    backend = _FakeBackend()
    t = LLMTranslator(backend, cache)
    assert await t.translate("後愛上你", Language.ZH_CN, MetadataField.TITLE) == "后爱上你"
    assert not backend.calls
    # 缓存中不应留下中文转换条目
    assert await cache.get("後愛上你", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) is None
    await cache.close()


@pytest.mark.asyncio
async def test_failed_translation_not_cached(cache):
    """后端返回空 → 不写缓存, 下次仍会重试."""
    backend = _FakeBackend(None)
    t = LLMTranslator(backend, cache)
    assert await t.translate("Hello", Language.ZH_CN, MetadataField.TITLE) is None
    assert await cache.get("Hello", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) is None
    await cache.close()


@pytest.mark.asyncio
async def test_use_cache_false_bypasses_read_but_refreshes(cache):
    """use_cache=False: 跳过缓存读取强制重译, 但仍回写刷新缓存."""
    await cache.put("Hello world", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH, "旧译文")
    backend = _FakeBackend("新译文")
    t = LLMTranslator(backend, cache)
    # 强制重译: 忽略缓存里的"旧译文", 调后端
    result = await t.translate("Hello world", Language.ZH_CN, MetadataField.TITLE, use_cache=False)
    assert result == "新译文"
    assert len(backend.calls) == 1
    # 缓存被刷新为新译文
    assert await cache.get("Hello world", Language.ZH_CN, MetadataField.TITLE, _SYSTEM_ZH) == "新译文"
    await cache.close()
