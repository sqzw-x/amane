"""直调 Capability 工具函数的替身与取用.

工具函数只读 ``ctx.deps`` 与 ``ctx.tool_call_id`` / ``ctx.tool_call_approved``, 运行时的 RunContext 因此
可以用 ``ToolCallContext`` 代替; 这类调用绕开模型, 直接覆盖工具自身的分支.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset

from amane.agent.tools import AgentDeps


class ToolCallContext:
    def __init__(
        self,
        deps: AgentDeps,
        *,
        tool_call_id: str = "tc-test",
        tool_call_approved: bool = False,
    ) -> None:
        self.deps = deps
        self.tool_call_id = tool_call_id
        self.tool_call_approved = tool_call_approved


def cap_toolset(cap: Capability[AgentDeps]) -> FunctionToolset[AgentDeps]:
    """取 Capability 的工具集."""
    toolset = cap.get_toolset()
    assert isinstance(toolset, FunctionToolset)
    return toolset


def tool_fn(cap: Capability[AgentDeps], name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    """取工具集里的裸函数 (未包 RunContext 的那一层)."""
    return cast(Callable[..., Awaitable[dict[str, Any]]], cap_toolset(cap).tools[name].function)
