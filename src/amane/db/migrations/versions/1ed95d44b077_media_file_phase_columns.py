"""media file phase columns

Revision ID: 1ed95d44b077
Revises: c3d8f95d2198
Create Date: 2026-08-30 03:53:21.496238
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from amane.parsing import parse_file_info

# revision identifiers, used by Alembic.
revision: str = "1ed95d44b077"
down_revision: str | None = "c3d8f95d2198"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.add_column(sa.Column("content_type", sa.String(), nullable=False, server_default="western"))
        batch_op.add_column(sa.Column("mosaic", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("has_subtitle", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("definition", sa.String(), nullable=True))
        batch_op.create_index(op.f("ix_media_files_content_type"), ["content_type"], unique=False)
        batch_op.create_index(op.f("ix_media_files_mosaic"), ["mosaic"], unique=False)
        batch_op.create_index(op.f("ix_media_files_has_subtitle"), ["has_subtitle"], unique=False)
        batch_op.create_index(op.f("ix_media_files_definition"), ["definition"], unique=False)

    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, path FROM media_files")).all()
    for row_id, path in rows:
        info = parse_file_info(path)
        conn.execute(
            text(
                "UPDATE media_files SET content_type = :content_type, mosaic = :mosaic, "
                "has_subtitle = :has_subtitle, definition = :definition WHERE id = :id"
            ),
            {
                "content_type": info.content_type.value,
                "mosaic": info.mosaic.value if info.mosaic is not None else None,
                "has_subtitle": info.has_subtitle,
                "definition": info.definition,
                "id": row_id,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.drop_index(op.f("ix_media_files_definition"))
        batch_op.drop_index(op.f("ix_media_files_has_subtitle"))
        batch_op.drop_index(op.f("ix_media_files_mosaic"))
        batch_op.drop_index(op.f("ix_media_files_content_type"))
        batch_op.drop_column("definition")
        batch_op.drop_column("has_subtitle")
        batch_op.drop_column("mosaic")
        batch_op.drop_column("content_type")
