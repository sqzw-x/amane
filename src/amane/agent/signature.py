"""把工具的参数 Schema 压成一行签名.

目录索引只暴露签名: 调用方需要知道「怎么调」, 不需要知道「Schema 长什么样」.
校验在服务端, 参数错了会回字段路径, 这个闭环本来就存在 —— 因此有损压缩是安全的.

压缩后单个工具约 20 tokens, 原始 JSON Schema 约 160.
"""

from __future__ import annotations

from typing import Any

_MAX_ENUM_INLINE = 8
"""枚举值多于此数时只给类型名, 免得一行签名被长枚举撑开."""

_MAX_DEPTH = 6
"""嵌套对象的展开深度上限; 再深就只给类型名."""


def summary(description: str) -> str:
    """描述的首行. 目录索引里每个工具只占一行."""
    for line in description.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def signature(params: dict[str, Any] | None) -> str:
    """渲染参数签名, 形如 ``name: str, ids?: [int], mode?: a|b``."""
    if not params:
        return ""
    defs = params.get("$defs") or {}
    properties = params.get("properties") or {}
    required = set(params.get("required") or [])

    parts: list[str] = []
    for name, schema in properties.items():
        optional = name not in required
        rendered = _render(schema, defs, 0)
        # 字段本身可选时, 类型末尾的 `?` (来自 anyOf 里的 null) 是重复信息.
        if optional and rendered.endswith("?"):
            rendered = rendered[:-1]
        parts.append(f"{name}{'?' if optional else ''}: {rendered}")
    return ", ".join(parts)


def _render(schema: Any, defs: dict[str, Any], depth: int) -> str:
    if not isinstance(schema, dict):
        return "any"

    if "$ref" in schema:
        return _render_ref(str(schema["$ref"]), defs, depth)

    for key in ("anyOf", "oneOf"):
        if key in schema:
            return _render_union(schema[key], defs, depth)

    if "const" in schema:
        return str(schema["const"])

    if "enum" in schema:
        return _render_enum(schema["enum"])

    kind = schema.get("type")
    if isinstance(kind, list):
        # 形如 ["string", "null"]: 取非 null 的那个.
        non_null = [k for k in kind if k != "null"]
        kind = non_null[0] if len(non_null) == 1 else "any"

    if kind == "array":
        return f"[{_render(schema.get('items', {}), defs, depth + 1)}]"
    if kind == "object":
        return _render_object(schema, defs, depth)
    if kind in ("integer", "number"):
        return _render_number(kind, schema)
    if kind is None:
        return "any"
    return str(kind)


def _render_ref(ref: str, defs: dict[str, Any], depth: int) -> str:
    name = ref.rsplit("/", 1)[-1]
    target = defs.get(name)
    if not isinstance(target, dict):
        return name
    if "enum" in target:
        return _render_enum(target["enum"])
    if "properties" in target:
        return _render_object(target, defs, depth)
    return name


def _render_union(options: Any, defs: dict[str, Any], depth: int) -> str:
    if not isinstance(options, list):
        return "any"
    rendered: list[str] = []
    nullable = False
    for option in options:
        if isinstance(option, dict) and option.get("type") == "null":
            nullable = True
            continue
        text = _render(option, defs, depth)
        if text not in rendered:
            rendered.append(text)
    if not rendered:
        return "null"
    joined = "|".join(rendered)
    return f"{joined}?" if nullable else joined


def _render_enum(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "any"
    if len(values) > _MAX_ENUM_INLINE:
        return "enum"
    return "|".join(str(value) for value in values)


def _render_object(schema: dict[str, Any], defs: dict[str, Any], depth: int) -> str:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return "object"
    if depth >= _MAX_DEPTH:
        return "object"
    required = set(schema.get("required") or [])
    inner = ", ".join(f"{name}{'' if name in required else '?'}" for name in properties)
    return "{" + inner + "}"


def _render_number(kind: str, schema: dict[str, Any]) -> str:
    low, high = schema.get("minimum"), schema.get("maximum")
    short = "int" if kind == "integer" else "num"
    if low is not None and high is not None:
        return f"{short}({low}~{high})"
    if low is not None:
        return f"{short}(>={low})"
    if high is not None:
        return f"{short}(<={high})"
    return short
