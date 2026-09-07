"""Persist research preparation settings and jobs.

Revision ID: 20260907_0057
Revises: 20260907_0056
"""

import sqlalchemy as sa
from alembic import op

revision = "20260907_0057"
down_revision = "20260907_0056"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "research_preparation_settings",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("config", sa.JSON(), nullable=False),
    sa.CheckConstraint("id = 1", name="ck_research_preparation_singleton"),
    comment="研究训练数据准备配置",
  )
  op.create_table(
    "research_preparation_jobs",
    sa.Column("job_id", sa.String(36), primary_key=True),
    sa.Column("request_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("request", sa.JSON(), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("phase", sa.String(64), nullable=False),
    sa.Column("flow_run_id", sa.String(64)),
    sa.Column("result", sa.JSON(), nullable=False),
    sa.Column("error", sa.String(512)),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.CheckConstraint(
      "kind IN ('COVERAGE','DOWNLOAD','CERTIFY','GPU')",
      name="ck_research_preparation_kind",
    ),
    sa.CheckConstraint(
      "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
      name="ck_research_preparation_status",
    ),
    comment="研究数据覆盖、下载、认证与 GPU 资格任务",
  )


def downgrade():
  op.drop_table("research_preparation_jobs")
  op.drop_table("research_preparation_settings")
