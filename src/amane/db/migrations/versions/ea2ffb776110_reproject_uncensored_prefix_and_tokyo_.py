"""reproject uncensored prefix and tokyo hot numbers

Revision ID: ea2ffb776110
Revises: 8dbd0fa0905e
Create Date: 2026-10-15 12:20:13.195977

SMBD / GACHIG 前缀与東京熱 k 系番号改判无码后, 按当前 parse_file_info 重算存量行的
类型与马赛克标记. 谓词只认这三类番号, 其它行保持原值.
"""

import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from amane.parsing import parse_file_info

# revision identifiers, used by Alembic.
revision: str = "ea2ffb776110"
down_revision: str | None = "8dbd0fa0905e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREFIXES = ("GACHIG-", "SMBD-")
_K_NUMBER = re.compile(r"k\d{4}", re.IGNORECASE)


def _moved_to_uncensored(number: str | None) -> bool:
    if not number:
        return False
    return number.upper().startswith(_PREFIXES) or _K_NUMBER.fullmatch(number) is not None


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
