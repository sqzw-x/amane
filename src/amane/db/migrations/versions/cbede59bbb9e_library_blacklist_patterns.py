"""library blacklist patterns

Revision ID: cbede59bbb9e
Revises: 5abbb79b1ae6
Create Date: 2026-08-26 19:18:39.419735
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cbede59bbb9e"
down_revision: str | None = "fd00ba796b91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.add_column(sa.Column("blacklist_patterns", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.drop_column("blacklist_patterns")
