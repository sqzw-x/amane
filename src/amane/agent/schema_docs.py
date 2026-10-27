"""库结构摘要: 拼进智能体的 instructions.

表与列取自 ``SQLModel.metadata``; 不符合 ``<x>_id`` 惯例的外键与非常规主键同样从模型
派生 —— 手写清单会随 schema 漂移, 因此这里只写惯例本身和派生出的例外.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, Table
from sqlalchemy.types import TypeDecorator, TypeEngine
from sqlmodel import SQLModel

_TYPE_NAMES: tuple[tuple[type[TypeEngine[Any]], str], ...] = (
    (Boolean, "bool"),
    (Integer, "int"),
    (Float, "float"),
    (DateTime, "datetime"),
    (JSON, "json"),
)
"""伪类型: 只保留写查询需要的区分, 长度与 ``VARCHAR``/``TEXT`` 之别属存储细节."""


def _base_type(type_: TypeEngine[Any]) -> TypeEngine[Any]:
    """剥掉 SQLModel 的类型装饰器 (``AutoString`` / ``UTCDateTime``), 按内层类型分类."""
    while isinstance(type_, TypeDecorator):
        type_ = type_.impl_instance
    return type_


def _short_type(type_: TypeEngine[Any]) -> str:
    base = _base_type(type_)
    for python_type, name in _TYPE_NAMES:
        if isinstance(base, python_type):
            return name
    return "str"


def _column_text(column: Column[Any]) -> str:
    """``id``/``*_id`` 与文本列不标注, 由摘要开头的两条规则给出."""
    if column.name == "id" or column.name.endswith("_id"):
        return column.name
    short = _short_type(column.type)
    return column.name if short == "str" else f"{column.name}:{short}"


def _stems(table_name: str) -> frozenset[str]:
    """``<x>_id`` 惯例接受的表名写法: 原名、去 s、ies → y."""
    stems = {table_name}
    if table_name.endswith("s"):
        stems.add(table_name[:-1])
    if table_name.endswith("ies"):
        stems.add(f"{table_name[:-3]}y")
    return frozenset(stems)


def _pk_clause(tables: list[tuple[str, Table]]) -> str:
    """非 ``id`` 主键逐表列出; ``metadata`` 的 PK 点名, 免得模型写 ``metadata_id``."""
    odd = [
        f"{name}({', '.join(column.name for column in table.primary_key.columns)})"
        for name, table in tables
        if tuple(column.name for column in table.primary_key.columns) != ("id",)
    ]
    base = "PK: `id` in every table"
    if not odd:
        return f"{base}."
    return f"{base}, except {', '.join(odd)}. `metadata`'s PK is `id`, never `metadata_id`."


def _fk_exceptions(tables: list[tuple[str, Table]]) -> list[str]:
    """不符合 ``<x>_id`` 惯例的外键, 按 (来源表, 目标表) 归并成 ``表.列/列 → 目标.id``."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for name, table in tables:
        for fk in table.foreign_keys:
            target = fk.column.table.name
            if fk.parent.name not in {f"{stem}_id" for stem in _stems(target)}:
                grouped.setdefault((name, target), []).append(fk.parent.name)
    return [f"{name}.{'/'.join(sorted(columns))} → {target}.id" for (name, target), columns in sorted(grouped.items())]


def build_schema_docs() -> str:
    """表/列与连接规则; 例外部分由模型派生, 不会落后于真实 schema."""
    tables = sorted(SQLModel.metadata.tables.items(), key=lambda item: item[0])
    lines = [
        "Read-only SQLite schema (do NOT write):",
        "Types: `id` and `*_id` columns are int; every other untyped column is str.",
        _pk_clause(tables),
        "",
    ]
    for name, table in tables:
        columns = ", ".join(_column_text(column) for column in table.columns)
        lines.append(f"- {name}({columns})")
    lines += [
        "",
        "Joins:",
        "- `<x>_id` references the table of the same name (singular or plural), column `id`:"
        " `actor_id` → `actors.id`, `library_id` → `libraries.id`.",
    ]
    exceptions = _fk_exceptions(tables)
    if exceptions:
        lines.append(f"- Exceptions: {'; '.join(exceptions)}.")
    lines += [
        "- Names, not ids: `metadata.studio/publisher/series` = `studios/publishers/series.name`;"
        " `metadata.actors/tags/directors` are JSON arrays whose resolved projections are"
        " `metadata_actors/metadata_tags/metadata_directors` — join those"
        " (`actor_aliases.name` alone is not unique).",
        "- `metadata.number` is unique case-insensitively: compare with `COLLATE NOCASE`.",
    ]
    return "\n".join(lines)
