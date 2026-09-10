"""reproject mosaic from uncensored content type

Revision ID: 0980003004e2
Revises: 668e214b1a76
Create Date: 2026-09-11 02:30:09.990031

按当前 parse_file_info 回填 MediaFile 相位列. 无标记时有码类型 mosaic=censored, 无码类型 mosaic=uncensored.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from amane.parsing import parse_file_info

# revision identifiers, used by Alembic.
revision: str = "0980003004e2"
down_revision: str | None = "668e214b1a76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
                "content_type": info.content_type.name,
                "mosaic": info.mosaic.name if info.mosaic is not None else None,
                "has_subtitle": info.has_subtitle,
                "definition": info.definition,
                "id": row_id,
            },
        )


def downgrade() -> None:
    pass
