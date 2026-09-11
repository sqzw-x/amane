"""feed item read_at

Revision ID: ce561bbac32c
Revises: a2e19df6190f
Create Date: 2026-09-12 01:39:48.402596
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ce561bbac32c"
down_revision: str | None = "a2e19df6190f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feed_items", sa.Column("read_at", sa.DateTime(), nullable=True))
    op.create_index(op.f("ix_feed_items_read_at"), "feed_items", ["read_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_feed_items_read_at"), table_name="feed_items")
    op.drop_column("feed_items", "read_at")
