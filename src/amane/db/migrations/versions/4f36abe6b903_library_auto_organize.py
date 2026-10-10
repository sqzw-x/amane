"""library auto organize

Revision ID: 4f36abe6b903
Revises: d9dec3f83da7
Create Date: 2026-10-10 11:10:53.644283
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4f36abe6b903"
down_revision: str | None = "d9dec3f83da7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 存量库默认关闭: server_default 只服务回填 (SQLite 不支持事后 DROP DEFAULT).
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.add_column(sa.Column("auto_organize", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.drop_column("auto_organize")
