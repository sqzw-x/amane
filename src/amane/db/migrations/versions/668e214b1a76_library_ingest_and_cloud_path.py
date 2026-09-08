"""library ingest and cloud path

Revision ID: 668e214b1a76
Revises: 4e95bf93b0c6
Create Date: 2026-09-09 01:19:01.279717
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "668e214b1a76"
down_revision: str | None = "4e95bf93b0c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.add_column(sa.Column("ingest", sa.String(), nullable=False, server_default="NATIVE"))
        batch_op.add_column(sa.Column("cloud_path", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.drop_column("cloud_path")
        batch_op.drop_column("ingest")
