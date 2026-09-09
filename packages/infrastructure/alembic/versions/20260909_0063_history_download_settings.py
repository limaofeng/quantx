"""Persist configurable history download windows."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_0063"
down_revision = "20260909_0062"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "history_download_settings",
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("policy", sa.JSON(), nullable=False),
    sa.Column("updated_by_user_id", sa.String(36), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    comment="全局历史补采允许时段配置",
  )


def downgrade():
  op.drop_table("history_download_settings")
