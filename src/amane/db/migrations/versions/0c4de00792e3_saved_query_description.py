"""saved query description

Revision ID: 0c4de00792e3
Revises: f08a70df4a51
Create Date: 2026-09-30 10:36:19.244572
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0c4de00792e3"
down_revision: str | None = "f08a70df4a51"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default 填充存量行且 SQLite 不支持后置 DROP DEFAULT, 保留不删.
    with op.batch_alter_table("saved_queries") as batch_op:
        batch_op.add_column(
            sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("saved_queries") as batch_op:
        batch_op.drop_column("description")
