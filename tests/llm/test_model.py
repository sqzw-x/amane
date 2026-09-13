"""共享 ``Model`` 工厂: 协议映射与传输客户端归属."""

import httpx2
import pytest
from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

from amane.enums import ApiType
from amane.llm import build_model

_ENDPOINT = "https://api.example/v1"

_CHAT_COMPLETION = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "test-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("with_http_client", [False, True])
@pytest.mark.parametrize(
    ("api_type", "model_cls"),
    [
        (ApiType.CHAT, OpenAIChatModel),
        (ApiType.RESPONSE, OpenAIResponsesModel),
        (ApiType.ANTHROPIC, AnthropicModel),
    ],
)
async def test_build_model_by_api_type(api_type: ApiType, model_cls: type[Model], with_http_client: bool) -> None:
    """三协议各自映射到对应模型类; 传入与不传传输客户端 (助理路径) 都必须构造成功."""
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(60.0)) as client:
        model = build_model(
            api_type,
            base_url=_ENDPOINT,
            api_key="test-key",
            model="test-model",
            http_client=client if with_http_client else None,
        )
    assert isinstance(model, model_cls)


@pytest.mark.asyncio
async def test_build_model_request_uses_injected_client() -> None:
    """传入传输客户端时请求必须经它发出. proxy 与超时都挂在它上面, 被 provider 忽略即静默失效."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_CHAT_COMPLETION)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(
            ApiType.CHAT, base_url=_ENDPOINT, api_key="test-key", model="test-model", http_client=client
        )
        response = await model_request(model, [ModelRequest(parts=[UserPromptPart(content="hi")])])

    assert [str(request.url) for request in requests] == [f"{_ENDPOINT}/chat/completions"]
    assert response.text == "OK"
