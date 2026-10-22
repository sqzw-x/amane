"""面向外部智能体的工具目录与派发.

工具定义来自助理工具层 (见 ``agent/external.py``), 这里只做参数校验与错误映射:

- 工具自身的 ``{"error": ...}`` 保持 200, 它是工具结果而不是传输失败;
- 入参校验失败 422, 未知工具 404, 未预期异常 500.

外部通道没有审批, 因此每次派发留一条审计: 改了什么只能事后从日志回答.

目录分三级, 按「什么时候需要」拆开, 避免每次都付全量成本:

- ``GET /tools``: 索引, 每个工具一行签名, 用来选工具;
- ``GET /tools/{name}``: 单个工具的完整入参, 只在参数不确定时取;
- ``GET /db-schema``: 库结构, 只有写 SQL 时才需要. 不叫 ``/schema``: 那与 OpenAPI
  和各资源自己的表单 Schema 撞名.
"""

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from ...agent.external import ToolCallContext, catalog, dispatch
from ...agent.schema_docs import build_schema_docs
from ...agent.tools import AgentDeps
from ...version import get_version
from ..deps import AgentDep, ConfigDep
from ..models.tools import (
    RuntimePaths,
    ToolCallRequest,
    ToolCatalog,
    ToolDetail,
    ToolSummary,
)

logger = structlog.get_logger("amane.external")

router = APIRouter(tags=["tools"])


@router.get("/tools")
async def get_tool_catalog(agent: AgentDep, config: ConfigDep) -> ToolCatalog:
    """工具索引: 客户端据此选工具, 不必内置工具清单."""
    cold = config.cold
    return ToolCatalog(
        version=get_version(),
        paths=RuntimePaths(
            data_dir=str(cold.data_dir.resolve()),
            log_dir=str(cold.log_dir.resolve()),
            db_path=str(cold.db_path.resolve()),
            config_path=str(cold.config_path.resolve()),
            env=["AMANE_DATA_DIR", "AMANE_LOG_DIR"],
        ),
        tools=[
            ToolSummary(
                name=spec.name,
                group=spec.group,
                summary=spec.summary,
                signature=spec.signature,
            )
            for spec in catalog(agent.tool_registry)
        ],
    )


@router.get("/tools/{name}")
async def get_tool_detail(name: str, agent: AgentDep) -> ToolDetail:
    """单个工具的完整入参."""
    spec = agent.tool_registry.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"未知工具: {name}")
    return ToolDetail(
        name=spec.name,
        group=spec.group,
        description=spec.description,
        params=spec.params,
    )


@router.get("/db-schema")
async def get_db_schema() -> dict[str, str]:
    """库结构摘要, 供编写只读 SQL 时参考."""
    return {"schema_doc": build_schema_docs()}


@router.post("/tools/{name}")
async def call_tool(name: str, body: ToolCallRequest, agent: AgentDep) -> Any:
    """派发一次工具调用.

    上下文固定为「已批准」且无会话: 外部通道没有可等用户点击的界面, 审批只对内置助理生效;
    无会话使交付的查询预设直接保留并进入预设列表.
    """
    registry = agent.tool_registry
    if name not in registry:
        logger.info("external tool rejected", tool=name, outcome="unknown_tool")
        raise HTTPException(404, detail=f"工具 {name} 不存在")

    deps = AgentDeps(
        repo=agent.repo,
        executor=agent.executor,
        session_id=None,
        sql_timeout_ms=agent.config.sql_timeout_ms,
        bridge=agent.bridge,
    )
    try:
        result = await dispatch(registry, name, body.args, ToolCallContext(deps=deps, tool_call_approved=True))
    except ValidationError as exc:
        logger.info("external tool rejected", tool=name, outcome="invalid_args", fields=list(body.args))
        raise HTTPException(422, detail=_format_validation_error(exc)) from exc
    except Exception:
        # 工具抛出未被处理的异常时也要留痕: 否则这次调用只剩请求日志里的路径,
        # 事后无法回答「被拒的是哪个工具」.
        logger.info(
            "external tool called", tool=name, group=registry[name].group, outcome="exception", fields=sorted(body.args)
        )
        raise

    # 只记字段名不记取值: 入参可能含用户数据, 审计需要的是「动了哪个工具」而不是内容.
    logger.info(
        "external tool called",
        tool=name,
        group=registry[name].group,
        outcome="error" if isinstance(result, dict) and "error" in result else "ok",
        fields=sorted(body.args),
    )
    return result


def _format_validation_error(exc: ValidationError) -> str:
    """只回字段路径与原因, 不带 pydantic 的整段文档链接."""
    parts = [f"{'.'.join(str(p) for p in err['loc']) or '<args>'}: {err['msg']}" for err in exc.errors()]
    return "; ".join(parts[:5])
