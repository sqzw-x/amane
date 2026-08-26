"""actor aliases as rows

Revision ID: fd00ba796b91
Revises: 5abbb79b1ae6
Create Date: 2026-08-26 19:39:50.772914
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fd00ba796b91"
down_revision: str | None = "5abbb79b1ae6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def upgrade() -> None:
    op.create_table(
        "actor_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "name", name="uq_actor_aliases_actor_name"),
    )
    op.create_index(op.f("ix_actor_aliases_actor_id"), "actor_aliases", ["actor_id"], unique=False)
    op.create_index(op.f("ix_actor_aliases_name"), "actor_aliases", ["name"], unique=False)

    bind = op.get_bind()
    now = _now()

    # 1. 现有别名行来源: 先 actor 别名规则入边 (目标实体缺失时按目标名新建), 再 JSON 袋.
    per_actor: dict[int, list[str]] = defaultdict(list)
    for source, target in bind.execute(
        sa.text("select source_name, target_name from facet_rules where kind = 'actor' and action = 'alias'")
    ).fetchall():
        target_id = bind.execute(sa.text("select id from actors where name = :n"), {"n": target}).scalar()
        if target_id is None:
            result = bind.execute(
                sa.text(
                    "insert into actors (name, gender, image_urls, provider_ids, source_urls, field_sources, raw,"
                    " created_at, updated_at) values (:n, 'unknown', '[]', '{}', '{}', '{}', '{}', :now, :now)"
                ),
                {"n": target, "now": now},
            )
            target_id = result.lastrowid
        if source and source != target and source not in per_actor[int(target_id)]:
            per_actor[int(target_id)].append(source)

    for actor_id, name, aliases in bind.execute(sa.text("select id, name, aliases from actors")).fetchall():
        if not aliases:
            continue
        try:
            bag = json.loads(aliases)
        except TypeError, ValueError:
            bag = []
        for alias in bag:
            if isinstance(alias, str) and alias and alias != name and alias not in per_actor[int(actor_id)]:
                per_actor[int(actor_id)].append(alias)

    # 2. 行化写入 (保序 position); 空/与展示名相同项已在上层排除.
    for actor_id, names in per_actor.items():
        for position, name in enumerate(names):
            bind.execute(
                sa.text(
                    "insert into actor_aliases (actor_id, name, position, created_at, updated_at)"
                    " values (:a, :n, :p, :now, :now)"
                ),
                {"a": actor_id, "n": name, "p": position, "now": now},
            )

    # 3. actor 别名规则退役 (block 规则保留).
    bind.execute(sa.text("delete from facet_rules where kind = 'actor' and action = 'alias'"))

    # 4. 删 JSON 袋列 (SQLite 只能 batch 重建; 索引由 batch 反射保留).
    with op.batch_alter_table("actors") as batch:
        batch.drop_column("aliases")


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("actors") as batch:
        batch.add_column(sa.Column("aliases", sa.JSON(), nullable=True))

    rows = bind.execute(sa.text("select actor_id, name from actor_aliases order by actor_id, position")).fetchall()
    per_actor: dict[int, list[str]] = defaultdict(list)
    for actor_id, name in rows:
        per_actor[int(actor_id)].append(name)
    for actor_id, names in per_actor.items():
        bind.execute(
            sa.text("update actors set aliases = :j where id = :a"),
            {"j": json.dumps(names, ensure_ascii=False), "a": actor_id},
        )

    op.drop_index(op.f("ix_actor_aliases_name"), table_name="actor_aliases")
    op.drop_index(op.f("ix_actor_aliases_actor_id"), table_name="actor_aliases")
    op.drop_table("actor_aliases")
