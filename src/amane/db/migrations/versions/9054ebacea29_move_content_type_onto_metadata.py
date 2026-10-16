"""move content type onto metadata

Revision ID: 9054ebacea29
Revises: 1fdf4d41aac1
Create Date: 2026-10-16 18:48:04.747579

把类型列从 media_files 移到 metadata; 文件只留相位. 存量行按当前 ``parse_file_info`` 回填: 条目类型取最早登记
的那一份挂载文件的路径 (``id`` 最小; ``updated_at`` 会被每次重扫刷新, 不能用来定格), 没有文件时取番号; 文件马赛克
标记只认词表命中的马赛克标记, 原先按类型回填的有码 / 无码清掉.
用户模板不受影响: ``{mosaic?}`` 渲染的是马赛克性质, ``censored`` 仍是合法映射键 (a2e19df6190f 注入过的照旧生效).
引用过 ``media_files.content_type`` 的已存查询预设会报列不存在, 需要用户自行改写.
downgrade 尽力回填: 绑定文件取所属条目的类型, 未绑定文件按路径重解析; 马赛克值不还原 (回填来源已不可区分).
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


def _restore_file_types(conn: Connection) -> None:
    """旧版本按文件列选路由, 回落 server_default 会让所有条目走欧美."""
    conn.execute(
        text(
            "UPDATE media_files SET content_type = COALESCE("
            "(SELECT content_type FROM metadata WHERE metadata.id = media_files.metadata_id), 'WESTERN')"
        )
    )
    unbound = conn.execute(text("SELECT id, path FROM media_files WHERE metadata_id IS NULL")).all()
    for media_id, path in unbound:
        conn.execute(
            text("UPDATE media_files SET content_type = :content_type WHERE id = :id"),
            {"content_type": parse_file_info(path).content_type.name, "id": media_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.add_column(
            sa.Column("content_type", _CONTENT_TYPE_COLUMN, nullable=False, server_default=sa.text("'WESTERN'"))
        )
        batch_op.create_index(op.f("ix_media_files_content_type"), ["content_type"], unique=False)
    _restore_file_types(op.get_bind())
    op.drop_index(op.f("ix_metadata_content_type"), table_name="metadata")
    with op.batch_alter_table("metadata") as batch_op:
        batch_op.drop_column("content_type")
