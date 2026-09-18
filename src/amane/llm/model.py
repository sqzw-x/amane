"""大模型构造: 翻译与助理 (agent) 共用同一套 provider 映射, 各自的凭据与配置仍分离."""

from urllib.parse import urlsplit

import httpx2
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from ..enums import ApiType

_RESPONSES_PROFILE = OpenAIModelProfile(tool_addition_mode=None)
"""``additional_tools`` 是 OpenAI 自家 Responses 端点专有: 兼容端点 (DeepSeek 等) 静默丢弃它,
延迟载入的 capability 工具就此永远到不了模型. 关掉后追加的工具改在 ``tools`` 里声明."""


def _responses_profile(base_url: str) -> OpenAIModelProfile | None:
    """只在非自家端点关掉 ``additional_tools``; 自家端点保留该追加通道 (不扰动 prompt 前缀缓存)."""
    return None if urlsplit(base_url).hostname == "api.openai.com" else _RESPONSES_PROFILE


def build_model(
    api_type: ApiType,
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    http_client: httpx2.AsyncClient | None = None,
) -> Model:
    """按 ``api_type`` 构造 ``Model``. ``base_url`` / ``api_key`` / ``model`` 原样交给 provider, 不做改写.

    ``http_client`` 决定传输客户端归属: 传入时 provider 用它构造 SDK 客户端 (翻译路径由此携带
    proxy 与超时, 生命周期归调用方); 不传时由 pydantic-ai 构造并托管默认客户端 (助理路径).
    不检查 ``api_key``: 为空时由 provider 回退环境变量.
    """
    match api_type:
        case ApiType.CHAT:
            return OpenAIChatModel(
                model, provider=OpenAIProvider(base_url=base_url, api_key=api_key, http_client=http_client)
            )
        case ApiType.RESPONSE:
            return OpenAIResponsesModel(
                model,
                provider=OpenAIProvider(base_url=base_url, api_key=api_key, http_client=http_client),
                profile=_responses_profile(base_url),
            )
        case ApiType.ANTHROPIC:
            return AnthropicModel(
                model, provider=AnthropicProvider(api_key=api_key, base_url=base_url, http_client=http_client)
            )
