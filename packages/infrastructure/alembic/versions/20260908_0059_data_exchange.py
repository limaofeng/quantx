"""Durable, isolated development history exports."""

import sqlalchemy as sa
from alembic import op

revision = "20260908_0059"
down_revision = "20260908_0058"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column(
    "market_data_request",
    sa.Column(
      "development_only", sa.Boolean(), nullable=False, server_default=sa.false()
    ),
  )
  op.create_table(
    "development_data_export",
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("request", sa.JSON(), nullable=False),
    sa.Column("state", sa.String(32), nullable=False),
    sa.Column("source_request_id", sa.String(36)),
    sa.Column("manifest", sa.JSON()),
    sa.Column("error", sa.String(128)),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True)),
    comment="跨环境只读历史行情导出任务与开发导入凭证",
  )
  op.create_index(
    "ix_development_data_export_queue",
    "development_data_export",
    ["state", "updated_at"],
  )


def downgrade():
  op.drop_table("development_data_export")
  op.drop_column("market_data_request", "development_only")
