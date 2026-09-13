"""跨语系经缓存后调用 ``LLMBackend``; 简繁经 ``zhconv``, 不经 LLM、不写入缓存.

已是目标语言返回 ``None``. ``build_translator`` 在 enabled=False 或缺 api_key 时返回 ``None``.
"""

from collections.abc import Mapping

import structlog
import zhconv

from ..enums import Language, MetadataField
from ..utils.language import needs_llm_translation
from .backend import OpenAIBackend
from .cache import TranslationCache
from .protocol import LLMBackend

logger = structlog.get_logger()

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
        backend: LLMBackend,
        cache: TranslationCache | None = None,
        *,
        system_prompt: str | None = None,
        field_prompts: Mapping[MetadataField, str] | None = None,
    ) -> None:
        self._backend = backend
        self._cache = cache
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
            result = await self._backend.ask(system_prompt=system, user_prompt=text)
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


def build_translator(
    *,
    enabled: bool,
    api_key: str | None,
    base_url: str,
    model: str,
    max_retries: int,
    rate_limit: float,
    proxy: str | None = None,
    system_prompt: str | None = None,
    field_prompts: Mapping[MetadataField, str] | None = None,
    cache: TranslationCache | None = None,
) -> LLMTranslator | None:
    """未启用或缺密钥时返回 ``None``. ``cache`` 热重载时复用同一实例."""
    if not enabled or not api_key:
        return None
    backend = OpenAIBackend(
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_retries=max_retries,
        rate_limit=rate_limit,
        proxy=proxy,
    )
    return LLMTranslator(
        backend,
        cache,
        system_prompt=system_prompt,
        field_prompts=field_prompts,
    )
