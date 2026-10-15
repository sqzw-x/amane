"""reproject drgbd and smdv uncensored prefixes

Revision ID: 136fd7914700
Revises: ea2ffb776110
Create Date: 2026-10-15 17:54:01.859466

DRGBD 与 SMDV 前缀改判无码后, 按当前 parse_file_info 重算存量行的类型与马赛克标记.
谓词只认这两类前缀的番号, 其它行保持原值.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from amane.parsing import parse_file_info

# revision identifiers, used by Alembic.
revision: str = "136fd7914700"
down_revision: str | None = "ea2ffb776110"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREFIXES = ("DRGBD-", "SMDV-")


def _moved_to_uncensored(number: str | None) -> bool:
    return number is not None and number.upper().startswith(_PREFIXES)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, path FROM media_files")).all()
    for row_id, path in rows:
        info = parse_file_info(path)
        if not _moved_to_uncensored(info.number):
            continue
        conn.execute(
            text("UPDATE media_files SET content_type = :content_type, mosaic = :mosaic WHERE id = :id"),
            {
                "content_type": info.content_type.name,
                "mosaic": info.mosaic.name if info.mosaic is not None else None,
                "id": row_id,
            },
        )


def downgrade() -> None:
    pass
