"""外部智能体接入: 复用助理工具层的目录与派发.

外部通道没有对话与界面, 因此:

- 目录按名字暴露工具的入参 / 返回 Schema, 由客户端自行组装调用;
- 派发始终以「已批准」的上下文执行, 审批只对内置助理生效 (界面能真的等用户点击);
- 依赖里的会话为空, 交付的查询预设因此与会话解耦并直接保留.

工具名即对外契约, 见 docs/dev/agent.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset

from .actor_ops import build_actor_ops_capability
from .facet_identity import build_facet_identity_capability
from .feed_ops import build_feed_ops_capability
from .library_ops import build_library_ops_capability
from .metadata_ops import build_metadata_ops_capability
from .schedule_ops import build_schedule_ops_capability
from .signature import signature, summary
from .task_ops import build_task_ops_capability
from .tools import AgentDeps, build_explore_toolset

EXPLORE_GROUP = "explore"
"""不属于任何工具组的只读探查工具 (sql_explore / sql_deliver / inspect_result)."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    group: str
    description: str
    params: dict[str, Any]
    """完整入参 Schema, 只在按名字取单个工具时给出."""
    signature: str
    """一行参数签名, 目录索引里用它代替完整 Schema."""
    summary: str
    """描述的首行, 目录索引里每个工具只占一行."""
    toolset: FunctionToolset[AgentDeps]


@dataclass(slots=True)
class ToolCallContext:
    """派发用的最小运行上下文.

    工具函数只读 ``deps`` / ``tool_call_id`` / ``tool_call_approved``, 其余属性由框架在回合内填充,
    外部通道用不到.
    """

    deps: AgentDeps
    tool_call_id: str = "external"
    tool_call_approved: bool = False
    """默认未批准: 只有派发层显式置真, 避免其它调用点无意绕过闸门."""


def build_capabilities() -> list[Capability[AgentDeps]]:
    return [
        build_metadata_ops_capability(),
        build_actor_ops_capability(),
        build_facet_identity_capability(),
        build_library_ops_capability(),
        build_feed_ops_capability(),
        build_schedule_ops_capability(),
        build_task_ops_capability(),
    ]


def _capability_toolset(cap: Capability[AgentDeps]) -> FunctionToolset[AgentDeps]:
    toolset = cap.get_toolset()
    if not isinstance(toolset, FunctionToolset):
        raise TypeError(f"工具组 {cap.id} 不是函数工具集")
    return toolset


def _capability_group(cap: Capability[AgentDeps]) -> str:
    return cap.id or EXPLORE_GROUP


def build_registry() -> dict[str, ToolSpec]:
    """按工具名索引全部可派发工具.

    名字冲突即装配错误: 名字是对外契约, 不允许两个工具共用.
    """
    sources: list[tuple[str, FunctionToolset[AgentDeps]]] = [
        (_capability_group(cap), _capability_toolset(cap)) for cap in build_capabilities()
    ]
    sources.append((EXPLORE_GROUP, build_explore_toolset()))

    registry: dict[str, ToolSpec] = {}
    for group, toolset in sources:
        for name, tool in toolset.tools.items():
            if name in registry:
                raise ValueError(f"工具名重复: {name}")
            schema = tool.function_schema
            description = (tool.description or "").strip()
            params = dict(schema.json_schema)
            registry[name] = ToolSpec(
                name=name,
                group=group,
                description=description,
                params=params,
                signature=signature(params),
                summary=summary(description),
                toolset=toolset,
            )
    return registry


def catalog(registry: dict[str, ToolSpec]) -> list[ToolSpec]:
    return [registry[name] for name in sorted(registry)]


async def dispatch(registry: dict[str, ToolSpec], name: str, args: dict[str, Any], ctx: ToolCallContext) -> Any:
    """校验入参后调用工具函数本体; 入参非法抛 ``ValidationError``, 未知工具抛 ``KeyError``.

    不捕获工具自身抛出的异常: 审批已被上下文绕过, 其余异常属实现缺陷, 交由调用方转成 500.
    """
    spec = registry[name]
    schema = spec.toolset.tools[name].function_schema
    validated = schema.validator.validate_python(args)
    return await schema.call(validated, cast(RunContext[Any], ctx))
