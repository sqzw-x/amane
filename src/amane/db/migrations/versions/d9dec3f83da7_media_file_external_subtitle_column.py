"""media file external subtitle column

Revision ID: d9dec3f83da7
Revises: 1f050b272f7d
Create Date: 2026-10-10 09:35:01.628482

拆开中字来源: 文件名标记改为 `has_subtitle_in_name`, 新增 `has_external_subtitle`.

存量行不回填: 判定需要逐目录读取磁盘, 媒体库位于网盘挂载点时单次目录列举可达数百毫秒,
整条迁移链会被阻塞数分钟以上, 而中止后重试又从零开始. 该列由库扫描与文件登记写入,
见 `src/amane/handlers/_common.py::refresh_external_subtitle`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9dec3f83da7"
down_revision: str | None = "1f050b272f7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 直连 ALTER: SQLite 的表重建无法重命名列, 而改名本身可以就地完成.
    op.alter_column("media_files", "has_subtitle", new_column_name="has_subtitle_in_name", existing_type=sa.Boolean())
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.add_column(sa.Column("has_external_subtitle", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.drop_index(op.f("ix_media_files_has_subtitle"))
        batch_op.create_index(op.f("ix_media_files_has_subtitle_in_name"), ["has_subtitle_in_name"], unique=False)
        batch_op.create_index(op.f("ix_media_files_has_external_subtitle"), ["has_external_subtitle"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.drop_index(op.f("ix_media_files_has_external_subtitle"))
        batch_op.drop_index(op.f("ix_media_files_has_subtitle_in_name"))
        batch_op.drop_column("has_external_subtitle")
    op.alter_column("media_files", "has_subtitle_in_name", new_column_name="has_subtitle", existing_type=sa.Boolean())
    op.create_index(op.f("ix_media_files_has_subtitle"), "media_files", ["has_subtitle"], unique=False)
