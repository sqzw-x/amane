"""reproject merci beaucoup uncensored prefixes

Revision ID: dc04a8ae624e
Revises: e06def65f72a
Create Date: 2026-11-20 08:12:50.993570

MXX / MCB / MCBD 前缀改判无码后, 按当前番号重算存量条目类型与文件马赛克标记.
MER 不收录: 该前缀同时是有码作品线, 数量更多.
条目直接按番号判定, 不取挂载文件的路径: 条目类型是番号的属性, 与文件怎么命名无关.
谓词只认这三个前缀, 其它行保持原值.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from amane.parsing import infer_content_type, parse_file_info

# revision identifiers, used by Alembic.
revision: str = "dc04a8ae624e"
down_revision: str | None = "e06def65f72a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREFIXES = ("MXX-", "MCB-", "MCBD-")


def _moved_to_uncensored(number: str | None) -> bool:
    return number is not None and number.upper().startswith(_PREFIXES)


def upgrade() -> None:
    conn = op.get_bind()
    for row_id, path in conn.execute(text("SELECT id, path FROM media_files")).all():
        info = parse_file_info(path)
        if not _moved_to_uncensored(info.number):
            continue
        conn.execute(
            text("UPDATE media_files SET mosaic = :mosaic WHERE id = :id"),
            {"mosaic": info.mosaic.name if info.mosaic is not None else None, "id": row_id},
        )
    for metadata_id, number in conn.execute(text("SELECT id, number FROM metadata")).all():
        if not _moved_to_uncensored(number):
            continue
        conn.execute(
            text("UPDATE metadata SET content_type = :content_type WHERE id = :id"),
            {"content_type": infer_content_type(number).name, "id": metadata_id},
        )


def downgrade() -> None:
    pass
