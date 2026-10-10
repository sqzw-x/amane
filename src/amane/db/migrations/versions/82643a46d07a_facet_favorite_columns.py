"""facet favorite columns

Revision ID: 82643a46d07a
Revises: d9dec3f83da7
Create Date: 2026-10-10 13:41:49.439765

分类收藏: 四张分类实体表各加一列 ``is_favorite``. 既有行必须带默认值 — SQLite 不允许给已有数据
的表加「NOT NULL 且无默认值」的列.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "82643a46d07a"
down_revision: str | None = "d9dec3f83da7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("tags", "studios", "publishers", "series")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("is_favorite")
