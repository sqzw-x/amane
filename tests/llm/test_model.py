"""共享 ``Model`` 工厂: 协议映射, 调用方注入的 HTTP 客户端, 以及重试次数直通 SDK."""

import httpx2
import pytest
from pydantic_ai.direct import model_request
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

from amane.enums import ApiType
from amane.llm import build_model

_CHAT_COMPLETION = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "test-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_type", "model_cls"),
    [
        (ApiType.CHAT, OpenAIChatModel),
        (ApiType.RESPONSE, OpenAIResponsesModel),
        (ApiType.ANTHROPIC, AnthropicModel),
    ],
)
async def test_build_model_by_api_type(api_type: ApiType, model_cls: type[Model]) -> None:
    """三协议各自映射到对应模型类; 翻译携带的 httpx2 客户端必须被三个 provider 接受."""
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(60.0)) as client:
        model = build_model(
            api_type, base_url="https://api.example/v1", api_key="test-key", model="test-model", http_client=client
        )
    assert isinstance(model, model_cls)


@pytest.mark.asyncio
async def test_build_model_request_uses_injected_client() -> None:
    """请求必须经注入的客户端发出. proxy 与超时都挂在它上面, 被 provider 忽略即静默失效."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_CHAT_COMPLETION)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(
            ApiType.CHAT, base_url="https://api.example/v1", api_key="test-key", model="test-model", http_client=client
        )
        response = await model_request(model, [ModelRequest(parts=[UserPromptPart(content="hi")])])

    assert [str(request.url) for request in requests] == ["https://api.example/v1/chat/completions"]
    assert response.text == "OK"


@pytest.mark.asyncio
@pytest.mark.parametrize("max_retries,expected_requests", [(0, 1), (1, 2), (None, 3)])
async def test_max_retries_forwarded_to_sdk(max_retries: int | None, expected_requests: int) -> None:
    """``max_retries`` 直通 SDK: 上游持续返回 500 时请求次数为 1 + max_retries.

    ``None`` 不下发该参数 (助理走这条), 因此是 SDK 默认的 2 次重试.
    """
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        # retry-after 让 SDK 不需要真实退避等待.
        return httpx2.Response(500, json={"error": {"message": "boom"}}, headers={"retry-after": "0.01"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(
            ApiType.CHAT,
            base_url="https://api.example/v1",
            api_key="test-key",
            model="test-model",
            http_client=client,
            max_retries=max_retries,
        )
        with pytest.raises(ModelHTTPError, match="status_code: 500"):
            await model_request(model, [ModelRequest(parts=[UserPromptPart(content="hi")])])

    assert len(requests) == expected_requests
