"""index resource file path

Revision ID: e377abaad72f
Revises: 6c9faa1bab67
Create Date: 2026-11-10 21:30:20.328967

资源按 url hash 定位: 两级散列路径是固定字面量前缀, 建索引后 GLOB 走范围查找而不是全表扫描.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e377abaad72f"
down_revision: str | None = "6c9faa1bab67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(op.f("ix_resources_file_path"), "resources", ["file_path"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_resources_file_path"), table_name="resources")
