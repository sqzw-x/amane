"""Agent 上游模型工厂表测试."""

from __future__ import annotations

import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

from amane.agent.runtime import build_agent, build_model, parse_session_thinking, resolve_model_settings
from amane.config import AgentApiType, AgentConfig, AgentThinkingMode


@pytest.mark.parametrize(
    ("api_type", "model_cls"),
    [
        (AgentApiType.CHAT, OpenAIChatModel),
        (AgentApiType.RESPONSE, OpenAIResponsesModel),
        (AgentApiType.ANTHROPIC, AnthropicModel),
    ],
)
def test_build_model_by_api_type(api_type: AgentApiType, model_cls: type) -> None:
    config = AgentConfig(api_key="test-key", api_type=api_type, model="test-model")
    model = build_model(config)
    assert isinstance(model, model_cls)


@pytest.mark.parametrize(
    "config",
    [
        AgentConfig(api_key=None),
        AgentConfig(api_key=""),
    ],
)
def test_build_agent_returns_none_without_api_key(config: AgentConfig) -> None:
    assert build_agent(config) is None


def test_build_agent_ignores_legacy_enabled_field() -> None:
    config = AgentConfig.model_validate({"enabled": False, "api_key": "test-key"})
    assert build_agent(config) is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("medium", AgentThinkingMode.MEDIUM),
        ("off", AgentThinkingMode.OFF),
        ("nope", None),
        (1, None),
    ],
)
def test_parse_session_thinking(raw: object, expected: AgentThinkingMode | None) -> None:
    assert parse_session_thinking(raw) is expected


@pytest.mark.parametrize(
    ("global_thinking", "session_thinking", "expected_thinking"),
    [
        (None, None, None),
        (AgentThinkingMode.LOW, None, "low"),
        (AgentThinkingMode.LOW, AgentThinkingMode.HIGH, "high"),
        (AgentThinkingMode.MEDIUM, AgentThinkingMode.OFF, False),
        (None, AgentThinkingMode.MINIMAL, "minimal"),
    ],
)
def test_resolve_model_settings(
    global_thinking: AgentThinkingMode | None,
    session_thinking: AgentThinkingMode | None,
    expected_thinking: object | None,
) -> None:
    config = AgentConfig(thinking=global_thinking, max_tokens=65_536)
    settings = resolve_model_settings(config, session_thinking=session_thinking)
    assert settings.get("max_tokens") == 65_536
    if expected_thinking is None:
        assert "thinking" not in settings
    else:
        assert settings.get("thinking") == expected_thinking
