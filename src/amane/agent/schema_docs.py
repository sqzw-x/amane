"""从 SQLModel metadata 生成给 Agent 的只读 schema 说明."""

from __future__ import annotations

from sqlmodel import SQLModel


def build_schema_docs(*, include_tables: frozenset[str] | None = None) -> str:
    """生成表/列摘要文本, 注入 system prompt."""
    lines: list[str] = [
        "Read-only SQLite schema (do NOT write):",
        "Primary keys are always integer column `id` unless noted.",
        "",
    ]
    tables = sorted(SQLModel.metadata.tables.items(), key=lambda x: x[0])
    for name, table in tables:
        if include_tables is not None and name not in include_tables:
            continue
        cols = ", ".join(f"{c.name}:{c.type!s}" for c in table.columns)
        lines.append(f"- {name}({cols})")
    lines.append("")
    lines.append("Join keys (FK → PK):")
    lines.append("- media_files.metadata_id → metadata.id")
    lines.append("- metadata_actors.metadata_id → metadata.id ; .actor_id → actors.id")
    lines.append("- actor_aliases.actor_id → actors.id ; actor_aliases.name is NOT unique (shared aliases)")
    lines.append("- metadata_directors.metadata_id → metadata.id ; .director_id → directors.id")
    lines.append("- metadata_tags.metadata_id → metadata.id ; .tag_id → tags.id")
    lines.append("- metadata_user_tags.metadata_id → metadata.id ; .user_tag_id → user_tags.id")
    lines.append("- comments.metadata_id → metadata.id")
    lines.append("Note: `metadata` PK is `id` — there is NO `metadata.metadata_id` column.")
    lines.append("")
    lines.append("Browse deliver targets:")
    lines.append("- entity=metadata → table `metadata`, must SELECT column `id`")
    lines.append("- entity=actor → table `actors`, must SELECT column `id`")
    return "\n".join(lines)
