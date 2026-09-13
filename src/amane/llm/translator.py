"""跨语系经缓存后经 pydantic-ai ``Model`` 翻译; 简繁经 ``zhconv``, 不经 LLM、不写入缓存.

已是目标语言返回 ``None``. ``build_translator`` 在 enabled=False 或缺 api_key 时返回 ``None``.
"""

import re
from collections.abc import Mapping

import structlog
import zhconv
from aiolimiter import AsyncLimiter
from httpx2 import AsyncClient, Timeout
from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart, UserPromptPart
from pydantic_ai.models import Model

from ..enums import ApiType, Language, MetadataField
from ..utils.language import needs_llm_translation
from .cache import TranslationCache
from .model import build_model

logger = structlog.get_logger()

_TIMEOUT = 60.0
"""LLM 请求超时 (秒). 重试由 SDK 按其默认策略承担, 翻译器不配置次数."""

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
"""去除提供商写进正文的思维链, 仅保留最终答案. ``ModelResponse.text`` 已排除 ``ThinkingPart``."""

# Language 枚举 → zhconv locale 代码 (仅中文变体).
_ZHCONV_LOCALE: dict[Language, str] = {
    Language.ZH_CN: "zh-cn",
    Language.ZH_TW: "zh-tw",
}

_LANG_NAME: dict[Language, str] = {
    Language.ZH_CN: "简体中文",
    Language.ZH_TW: "繁體中文",
    Language.JP: "日本語",
    Language.EN: "English",
}

_FIELD_HINT: dict[MetadataField, str] = {
    MetadataField.TITLE: "这是一部影片的标题, 翻译应简洁自然, 保留专有名词与番号.",
    MetadataField.PLOT: "这是一部影片的简介, 完整通顺地翻译全部内容.",
}

TARGET_LANG_PLACEHOLDER = "{target_lang}"
"""自定义提示词中引用目标语言名的占位符."""

_DEFAULT_SYSTEM_PROMPT = f"你是专业的影视元数据翻译. 将用户提供的文本翻译为{TARGET_LANG_PLACEHOLDER}."

_OUTPUT_CONSTRAINT = "只输出译文本身, 不要解释、不要引号、不要附加任何内容."


def build_system_prompt(
    target: Language,
    field: MetadataField,
    *,
    system_prompt: str | None = None,
    field_prompts: Mapping[MetadataField, str] | None = None,
) -> str:
    """组装 system 提示词 = 指令 + 字段说明 + 固定输出约束.

    ``system_prompt`` 覆盖内置指令, 空白时回退内置; ``field_prompts`` 逐字段覆盖内置说明,
    无覆盖或覆盖值为空白时回退内置. 输出约束不允许配置: 译文写入标量字段, 附加解释会污染元数据.
    占位符仅替换 ``{target_lang}``, 其余花括号按字面保留.
    """
    instruction = (system_prompt or "").strip() or _DEFAULT_SYSTEM_PROMPT
    override = (field_prompts or {}).get(field, "")
    hint = override.strip() or _FIELD_HINT.get(field, "")
    parts = [_render_placeholders(instruction, target)]
    if hint:
        parts.append(_render_placeholders(hint, target))
    parts.append(_OUTPUT_CONSTRAINT)
    return " ".join(parts)


def _render_placeholders(template: str, target: Language) -> str:
    """不使用 ``str.format``: 提示词允许出现 JSON 示例等花括号."""
    return template.replace(TARGET_LANG_PLACEHOLDER, _LANG_NAME[target])


class LLMTranslator:
    def __init__(
        self,
        model: Model,
        cache: TranslationCache | None = None,
        *,
        rate_limit: float = 2.0,
        system_prompt: str | None = None,
        field_prompts: Mapping[MetadataField, str] | None = None,
    ) -> None:
        self._model = model
        self._cache = cache
        # LLM 端点独立限速, 与站点 host 限速隔离: 桶容量 1, 严格平滑.
        # 包住一次翻译调用; 该调用内部的重试由 SDK 客户端承担.
        self._limiter = AsyncLimiter(1, 1 / rate_limit)
        self._system_prompt = system_prompt
        self._field_prompts: Mapping[MetadataField, str] = field_prompts or {}

    async def translate(
        self, text: str, target: Language, field: MetadataField, *, use_cache: bool = True
    ) -> str | None:
        text = text.strip()
        if not text:
            return None

        if needs_llm_translation(text, target):
            system = build_system_prompt(
                target,
                field,
                system_prompt=self._system_prompt,
                field_prompts=self._field_prompts,
            )
            # 缓存命中即跳过 LLM: 全缓存重刮, 配置不变时不重复翻译, 也避免 temperature 漂移.
            # 键含 system 提示词, 改提示词后旧译文不再命中. use_cache=False 时跳过读取
            # (强制重译), 但仍回写以刷新缓存.
            if self._cache is not None and use_cache:
                cached = await self._cache.get(text, target, field, system)
                if cached is not None:
                    return cached
            result = await self._ask(system_prompt=system, user_prompt=text)
            if not result:
                return None
            if self._cache is not None:
                await self._cache.put(text, target, field, system, result)
            return result

        # 中文文本: 简繁字形转换 (幂等); 共用字/已是目标变体则结果等于原文 → 返回 None 省去写回.
        locale = _ZHCONV_LOCALE.get(target)
        if locale is not None:
            converted = zhconv.convert(text, locale)
            return converted if converted != text else None

        return None

    async def _ask(self, *, system_prompt: str, user_prompt: str) -> str | None:
        """单轮请求. 重试由 SDK 客户端承担; 无内容或抛异常时返回 ``None``, 不抛异常."""
        messages: list[ModelMessage] = [
            ModelRequest(parts=[SystemPromptPart(content=system_prompt), UserPromptPart(content=user_prompt)])
        ]
        async with self._limiter:
            try:
                response = await model_request(self._model, messages)
            except Exception as e:
                logger.warning("LLM request failed, retries exhausted", error=str(e))
                return None
        text = response.text
        return _THINK.sub("", text).strip() if text else None


def build_translator(
    *,
    enabled: bool,
    api_type: ApiType,
    api_key: str | None,
    base_url: str,
    model: str,
    rate_limit: float,
    proxy: str | None = None,
    system_prompt: str | None = None,
    field_prompts: Mapping[MetadataField, str] | None = None,
    cache: TranslationCache | None = None,
) -> LLMTranslator | None:
    """未启用或缺密钥时返回 ``None``. ``cache`` 热重载时复用同一实例."""
    if not enabled or not api_key:
        return None
    # 翻译路径自建传输客户端 (proxy + 超时) 并交给 provider; 助理不传, 由 pydantic-ai 构造默认客户端.
    http_client = AsyncClient(proxy=proxy, timeout=Timeout(_TIMEOUT), follow_redirects=True)
    return LLMTranslator(
        build_model(api_type, base_url=base_url, api_key=api_key, model=model, http_client=http_client),
        cache,
        rate_limit=rate_limit,
        system_prompt=system_prompt,
        field_prompts=field_prompts,
    )
