"""perf indexes for list order and reverse relation

Revision ID: 6c9faa1bab67
Revises: fb4fd486708c
Create Date: 2026-11-10 21:29:49.528803

- 关联表补反向索引: 主键 (metadata_id, X) 只能从条目侧定位, 按 X 筛选与删 X 的外键检查要全表扫描.
- metadata 列表默认排序 (updated_at DESC, id ASC) 走复合索引后不再建临时 B-tree.
- feed_items 默认视图带 ignored_at IS NULL, 过滤列进索引前缀才能同时满足筛选与排序.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6c9faa1bab67"
down_revision: str | None = "fb4fd486708c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_actor_user_tags_user_tag_id", "actor_user_tags", ["user_tag_id", "actor_id"], unique=False)
    op.create_index("ix_metadata_actors_actor_id", "metadata_actors", ["actor_id", "metadata_id"], unique=False)
    op.create_index(
        "ix_metadata_directors_director_id", "metadata_directors", ["director_id", "metadata_id"], unique=False
    )
    op.create_index("ix_metadata_tags_tag_id", "metadata_tags", ["tag_id", "metadata_id"], unique=False)
    op.create_index(
        "ix_metadata_user_tags_user_tag_id", "metadata_user_tags", ["user_tag_id", "metadata_id"], unique=False
    )
    # 表达式 / 带方向的索引: autogenerate 对 SQLite 无法反射, 须手补.
    op.execute(sa.text("CREATE INDEX ix_metadata_updated_at_id ON metadata (updated_at DESC, id)"))
    op.execute(
        sa.text(
            "CREATE INDEX ix_feed_items_state_list_order ON feed_items "
            "(ignored_at, coalesce(published_at, created_at), id)"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_feed_items_state_list_order", table_name="feed_items")
    op.drop_index("ix_metadata_updated_at_id", table_name="metadata")
    op.drop_index("ix_metadata_user_tags_user_tag_id", table_name="metadata_user_tags")
    op.drop_index("ix_metadata_tags_tag_id", table_name="metadata_tags")
    op.drop_index("ix_metadata_directors_director_id", table_name="metadata_directors")
    op.drop_index("ix_metadata_actors_actor_id", table_name="metadata_actors")
    op.drop_index("ix_actor_user_tags_user_tag_id", table_name="actor_user_tags")
