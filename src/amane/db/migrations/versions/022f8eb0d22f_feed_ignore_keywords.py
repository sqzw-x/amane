"""feed ignore keywords

Revision ID: 022f8eb0d22f
Revises: ce561bbac32c
Create Date: 2026-09-12 02:28:42.090047
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "022f8eb0d22f"
down_revision: str | None = "ce561bbac32c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feeds",
        sa.Column("ignore_keywords", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("feeds", "ignore_keywords")
