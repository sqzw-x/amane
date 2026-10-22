from typing import Any

from pydantic import BaseModel


class ToolInfo(BaseModel):
    name: str
    group: str
    description: str
    params: dict[str, Any]
    returns: dict[str, Any]


class RuntimePaths(BaseModel):
    """智能体可直接读取的位置; 供它自行查日志 / 配置 / 数据库, 不为这些能力做接口."""

    data_dir: str
    log_dir: str
    db_path: str
    config_path: str
    env: list[str]


class ToolCatalog(BaseModel):
    version: str
    schema_doc: str
    paths: RuntimePaths
    tools: list[ToolInfo]


class ToolCallRequest(BaseModel):
    args: dict[str, Any] = {}
