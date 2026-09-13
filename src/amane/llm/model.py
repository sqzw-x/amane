"""大模型构造: 翻译与助理 (agent) 共用同一套 provider 映射, 各自的凭据与配置仍分离."""

import httpx2
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
) -> Model:
    """按 ``api_type`` 构造 ``Model``. ``base_url`` / ``api_key`` / ``model`` 原样交给 provider, 不做改写.

    传入 ``http_client`` 时由调用方持有其生命周期, provider 不关闭它; 用于携带 proxy / 超时.
    不检查 ``api_key``: 缺省时由 provider 回退环境变量, 或按已填 ``base_url`` 使用占位密钥.
    """
    match api_type:
        case ApiType.CHAT:
            return OpenAIChatModel(
                model, provider=OpenAIProvider(base_url=base_url, api_key=api_key, http_client=http_client)
            )
        case ApiType.RESPONSE:
            return OpenAIResponsesModel(
                model, provider=OpenAIProvider(base_url=base_url, api_key=api_key, http_client=http_client)
            )
        case ApiType.ANTHROPIC:
            return AnthropicModel(
                model, provider=AnthropicProvider(api_key=api_key, base_url=base_url, http_client=http_client)
            )
