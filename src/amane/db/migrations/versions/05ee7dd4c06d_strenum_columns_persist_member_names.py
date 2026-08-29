"""strenum columns persist member names

Revision ID: 05ee7dd4c06d
Revises: 1ed95d44b077
Create Date: 2026-08-30 05:19:03.142977
"""

from collections.abc import Sequence
from enum import StrEnum

from alembic import op
from sqlalchemy import text

from amane.db.models import AgentSessionStatus, FacetKind, FacetRuleAction, SavedQueryEntity
from amane.enums import ActorGender, LibraryAutomation, LinkMode, MoveMode
from amane.parsing import ContentType

# revision identifiers, used by Alembic.
revision: str = "05ee7dd4c06d"
down_revision: str | None = "1ed95d44b077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, enum, nullable) — SQLite 仍是 VARCHAR, 只把存量 value 改成成员名.
_ENUM_COLUMNS: tuple[tuple[str, str, type[StrEnum], bool], ...] = (
    ("libraries", "automation", LibraryAutomation, False),
    ("libraries", "move_mode", MoveMode, False),
    ("libraries", "link_mode", LinkMode, False),
    ("actors", "gender", ActorGender, False),
    ("facet_rules", "kind", FacetKind, False),
    ("facet_rules", "action", FacetRuleAction, False),
    ("feeds", "content_type", ContentType, True),
    ("agent_sessions", "status", AgentSessionStatus, False),
    ("saved_queries", "entity", SavedQueryEntity, False),
)


def _rewrite(enum_cls: type[StrEnum], table: str, column: str, *, to_names: bool, nullable: bool) -> None:
    conn = op.get_bind()
    for member in enum_cls:
        if member.name == member.value:
            continue
        src, dst = (member.value, member.name) if to_names else (member.name, member.value)
        conn.execute(
            text(f"UPDATE {table} SET {column} = :dst WHERE {column} = :src"),
            {"dst": dst, "src": src},
        )
    if to_names and nullable:
        names = [member.name for member in enum_cls]
        placeholders = ", ".join(f":n{i}" for i in range(len(names)))
        params = {f"n{i}": name for i, name in enumerate(names)}
        conn.execute(
            text(f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL AND {column} NOT IN ({placeholders})"),
            params,
        )


def upgrade() -> None:
    for table, column, enum_cls, nullable in _ENUM_COLUMNS:
        _rewrite(enum_cls, table, column, to_names=True, nullable=nullable)

    with op.batch_alter_table("libraries") as batch_op:
        batch_op.alter_column("automation", server_default="SCRAPE")
        batch_op.alter_column("move_mode", server_default="MOVE")
        batch_op.alter_column("link_mode", server_default="STRM")
    with op.batch_alter_table("actors") as batch_op:
        batch_op.alter_column("gender", server_default="UNKNOWN")


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.alter_column("automation", server_default="scrape")
        batch_op.alter_column("move_mode", server_default="move")
        batch_op.alter_column("link_mode", server_default="strm")
    with op.batch_alter_table("actors") as batch_op:
        batch_op.alter_column("gender", server_default="unknown")

    for table, column, enum_cls, nullable in _ENUM_COLUMNS:
        _rewrite(enum_cls, table, column, to_names=False, nullable=nullable)
