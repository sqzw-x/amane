"""大模型构造: 翻译与助理 (agent) 共用同一套 provider 映射, 各自的凭据与配置仍分离."""

import httpx2
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from ..enums import ApiType


def build_model(
    api_type: ApiType,
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    http_client: httpx2.AsyncClient | None = None,
    max_retries: int | None = None,
) -> Model:
    """按 ``api_type`` 构造 ``Model``. ``base_url`` / ``api_key`` / ``model`` 原样交给 SDK 客户端, 不做改写.

    SDK 客户端由本函数构造后交给 provider: ``max_retries`` 直通 SDK, 重试哪些状态码、退避与
    ``Retry-After`` 都属 SDK 策略; ``None`` 表示不指定, 沿用 SDK 默认.
    传入 ``http_client`` 时由调用方持有其生命周期; 不检查 ``api_key``: 为空时由 SDK 回退环境变量.
    """
    match api_type:
        case ApiType.CHAT:
            return OpenAIChatModel(model, provider=_openai_provider(base_url, api_key, http_client, max_retries))
        case ApiType.RESPONSE:
            return OpenAIResponsesModel(model, provider=_openai_provider(base_url, api_key, http_client, max_retries))
        case ApiType.ANTHROPIC:
            return AnthropicModel(model, provider=_anthropic_provider(base_url, api_key, http_client, max_retries))


def _openai_provider(
    base_url: str, api_key: str | None, http_client: httpx2.AsyncClient | None, max_retries: int | None
) -> OpenAIProvider:
    """``max_retries=None`` 时不下发该参数, 走 SDK 默认.

    SDK 运行期签名只接受 ``int``: 显式传入 ``NOT_GIVEN`` 会在请求时以 ``NotGiven + int`` 失败.
    """
    if max_retries is None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, http_client=http_client)
    else:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, http_client=http_client, max_retries=max_retries)
    return OpenAIProvider(openai_client=client)


def _anthropic_provider(
    base_url: str, api_key: str | None, http_client: httpx2.AsyncClient | None, max_retries: int | None
) -> AnthropicProvider:
    """``max_retries=None`` 时不下发该参数, 走 SDK 默认; 理由同 ``_openai_provider``."""
    if max_retries is None:
        client = AsyncAnthropic(base_url=base_url, api_key=api_key, http_client=http_client)
    else:
        client = AsyncAnthropic(base_url=base_url, api_key=api_key, http_client=http_client, max_retries=max_retries)
    return AnthropicProvider(anthropic_client=client)
