"""drop resources last_accessed_at

Revision ID: 54138fa1c160
Revises: 05ee7dd4c06d
Create Date: 2026-09-03 01:52:50.141302
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "54138fa1c160"
down_revision: str | None = "05ee7dd4c06d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("resources") as batch_op:
        batch_op.drop_column("last_accessed_at")


def downgrade() -> None:
    with op.batch_alter_table("resources") as batch_op:
        batch_op.add_column(sa.Column("last_accessed_at", sa.DateTime(), nullable=True))
