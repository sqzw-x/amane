"""move content type onto metadata

Revision ID: 9054ebacea29
Revises: 1fdf4d41aac1
Create Date: 2026-10-16 18:48:04.747579

类型是番号的属性, 从 media_files 移到 metadata; 文件只留相位. 存量行按当前 ``parse_file_info`` 回填: 条目类型取
首个挂载文件的路径, 没有文件时取番号; 文件马赛克标记只认词表命中的来路标记, 原先按类型回填的有码 / 无码清掉.
downgrade 不还原马赛克值: 回填来源已不可区分.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

from amane.parsing import ContentType, file_phase_from_path, infer_content_type, parse_file_info

# revision identifiers, used by Alembic.
revision: str = "9054ebacea29"
down_revision: str | None = "1fdf4d41aac1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUM_VALUES = tuple(ContentType.__members__)
_CONTENT_TYPE_COLUMN = sa.Enum(*_ENUM_VALUES, name="contenttype")


def _backfill_metadata_types(conn: Connection) -> None:
    first_paths: dict[int, str] = {}
    for metadata_id, path in conn.execute(
        text("SELECT metadata_id, path FROM media_files WHERE metadata_id IS NOT NULL ORDER BY id")
    ).all():
        first_paths.setdefault(metadata_id, path)
    for metadata_id, number in conn.execute(text("SELECT id, number FROM metadata")).all():
        path = first_paths.get(metadata_id)
        content_type = parse_file_info(path).content_type if path is not None else infer_content_type(number)
        conn.execute(
            text("UPDATE metadata SET content_type = :content_type WHERE id = :id"),
            {"content_type": content_type.name, "id": metadata_id},
        )


def _reproject_file_mosaic(conn: Connection) -> None:
    for media_id, path in conn.execute(text("SELECT id, path FROM media_files")).all():
        mosaic = file_phase_from_path(path)["mosaic"]
        conn.execute(
            text("UPDATE media_files SET mosaic = :mosaic WHERE id = :id"),
            {"mosaic": mosaic.name if mosaic is not None else None, "id": media_id},
        )


def upgrade() -> None:
    op.add_column(
        "metadata",
        sa.Column("content_type", _CONTENT_TYPE_COLUMN, nullable=False, server_default=sa.text("'WESTERN'")),
    )
    op.create_index(op.f("ix_metadata_content_type"), "metadata", ["content_type"], unique=False)
    conn = op.get_bind()
    _backfill_metadata_types(conn)
    _reproject_file_mosaic(conn)
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.drop_index(op.f("ix_media_files_content_type"))
        batch_op.drop_column("content_type")


def downgrade() -> None:
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.add_column(
            sa.Column("content_type", _CONTENT_TYPE_COLUMN, nullable=False, server_default=sa.text("'WESTERN'"))
        )
        batch_op.create_index(op.f("ix_media_files_content_type"), ["content_type"], unique=False)
    op.drop_index(op.f("ix_metadata_content_type"), table_name="metadata")
    with op.batch_alter_table("metadata") as batch_op:
        batch_op.drop_column("content_type")
