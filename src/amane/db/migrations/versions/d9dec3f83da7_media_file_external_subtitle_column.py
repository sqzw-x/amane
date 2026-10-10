"""media file external subtitle column

Revision ID: d9dec3f83da7
Revises: 1f050b272f7d
Create Date: 2026-10-10 09:35:01.628482

拆开中字来源: 文件名标记改为 `has_subtitle_in_name`, 新增 `has_external_subtitle`. 同时按各库
`subtitle_extensions` 扫一遍视频同目录, 只回填命中的行; 未命中的行保持假值, 不必逐行写库.
"""

import os
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9dec3f83da7"
down_revision: str | None = "1f050b272f7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIBRARY_TABLE = sa.table(
    "libraries",
    sa.column("id", sa.Integer),
    sa.column("subtitle_extensions", sa.JSON()),
)
_MEDIA_FILE_TABLE = sa.table(
    "media_files",
    sa.column("id", sa.Integer),
    sa.column("path", sa.String),
    sa.column("library_id", sa.Integer),
    sa.column("has_external_subtitle", sa.Boolean),
)


def _has_companion_subtitle(video_path: Path, extensions: frozenset[str]) -> bool:
    """视频同目录 (不递归) 存在命中扩展名的文件.

    迁移内联实现: 迁移必须与当时的库代码解耦, 后续改动 `library/rules.py` 不得改变这次回填的口径.
    """
    try:
        with os.scandir(video_path.parent) as entries:
            for entry in entries:
                if entry.is_file() and Path(entry.name).suffix.lower() in extensions:
                    return True
    except OSError:
        return False
    return False


def _backfill_external_subtitles() -> None:
    """只回填命中的行: 未命中意味着同目录一次目录列举, 不需要写库."""
    conn = op.get_bind()
    library_rows = conn.execute(sa.select(_LIBRARY_TABLE.c.id, _LIBRARY_TABLE.c.subtitle_extensions)).all()
    library_extensions = {
        library_id: frozenset(ext.lower() for ext in (extensions or [])) for library_id, extensions in library_rows
    }
    if not any(library_extensions.values()):
        return
    rows = conn.execute(
        sa.select(_MEDIA_FILE_TABLE.c.id, _MEDIA_FILE_TABLE.c.path, _MEDIA_FILE_TABLE.c.library_id)
    ).all()
    scanned: dict[tuple[int, str], bool] = {}
    for row_id, path, library_id in rows:
        extensions = library_extensions.get(library_id, frozenset())
        if not extensions:
            continue
        key = (library_id, str(Path(path).parent))
        if key not in scanned:
            scanned[key] = _has_companion_subtitle(Path(path), extensions)
        if not scanned[key]:
            continue
        conn.execute(
            sa.update(_MEDIA_FILE_TABLE).where(_MEDIA_FILE_TABLE.c.id == row_id).values(has_external_subtitle=True)
        )


def upgrade() -> None:
    # 直连 ALTER: SQLite 的表重建无法重命名列, 而改名本身可以就地完成.
    op.alter_column("media_files", "has_subtitle", new_column_name="has_subtitle_in_name", existing_type=sa.Boolean())
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.add_column(sa.Column("has_external_subtitle", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.drop_index(op.f("ix_media_files_has_subtitle"))
        batch_op.create_index(op.f("ix_media_files_has_subtitle_in_name"), ["has_subtitle_in_name"], unique=False)
        batch_op.create_index(op.f("ix_media_files_has_external_subtitle"), ["has_external_subtitle"], unique=False)
    _backfill_external_subtitles()


def downgrade() -> None:
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.drop_index(op.f("ix_media_files_has_external_subtitle"))
        batch_op.drop_index(op.f("ix_media_files_has_subtitle_in_name"))
        batch_op.drop_column("has_external_subtitle")
    op.alter_column("media_files", "has_subtitle_in_name", new_column_name="has_subtitle", existing_type=sa.Boolean())
    op.create_index(op.f("ix_media_files_has_subtitle"), "media_files", ["has_subtitle"], unique=False)
