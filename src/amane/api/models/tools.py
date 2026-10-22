from typing import Any

from pydantic import BaseModel


class ToolSummary(BaseModel):
    """目录索引里的一条: 够选工具, 不够直接构造复杂入参."""

    name: str
    group: str
    summary: str
    signature: str


class ToolDetail(BaseModel):
    """单个工具的完整入参. 返回结构不对外: 调用方从响应体直接看."""

    name: str
    group: str
    description: str
    params: dict[str, Any]


class RuntimePaths(BaseModel):
    """智能体可直接读取的位置; 供它自行查日志 / 配置 / 数据库, 不为这些能力做接口."""

    data_dir: str
    log_dir: str
    db_path: str
    config_path: str
    env: list[str]


class ToolCatalog(BaseModel):
    version: str
    paths: RuntimePaths
    tools: list[ToolSummary]


class ToolCallRequest(BaseModel):
    args: dict[str, Any] = {}
