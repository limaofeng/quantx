"""Add strict ROE quality and per-code financial sync audits."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260812_0006"
down_revision = "20260810_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
  bind = op.get_bind()
  inspector = inspect(bind)
  table_names = set(inspector.get_table_names())

  if "financial_metric_roe_qualities" not in table_names:
    op.create_table(
      "financial_metric_roe_qualities",
      sa.Column("code", sa.String(length=20), nullable=False),
      sa.Column("as_of_date", sa.Date(), nullable=False),
      sa.Column("report_date", sa.Date(), nullable=False),
      sa.Column(
        "status",
        sa.String(length=20),
        nullable=False,
        server_default="UNVERIFIED",
      ),
      sa.Column(
        "flags",
        sa.ARRAY(sa.String()),
        nullable=False,
        server_default=sa.text("'{}'::varchar[]"),
      ),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.ForeignKeyConstraint(
        ["code", "as_of_date", "report_date"],
        [
          "financial_metric_snapshots.code",
          "financial_metric_snapshots.as_of_date",
          "financial_metric_snapshots.report_date",
        ],
        name="fk_financial_metric_roe_quality_snapshot",
        ondelete="CASCADE",
      ),
      sa.PrimaryKeyConstraint("code", "as_of_date", "report_date"),
      comment="上市公司ROE指标独立质量状态",
    )
    op.execute(
      """
      INSERT INTO financial_metric_roe_qualities (
        code, as_of_date, report_date, status, flags, created_at, updated_at
      )
      SELECT
        code, as_of_date, report_date, 'UNVERIFIED', '{}', NOW(), NOW()
      FROM financial_metric_snapshots
      ON CONFLICT (code, as_of_date, report_date) DO NOTHING
      """
    )

  if "financial_sync_code_audits" not in table_names:
    op.create_table(
      "financial_sync_code_audits",
      sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
      sa.Column(
        "run_id",
        sa.Integer(),
        sa.ForeignKey("financial_sync_runs.id", ondelete="CASCADE"),
        nullable=False,
      ),
      sa.Column("stock_code", sa.String(length=20), nullable=False),
      sa.Column("window_start", sa.Date(), nullable=False),
      sa.Column("window_end", sa.Date(), nullable=False),
      sa.Column("status", sa.String(length=20), nullable=False),
      sa.Column("statement_rows", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("metric_rows", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("verified_at", sa.DateTime(), nullable=True),
      sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint(
        "run_id",
        "stock_code",
        name="uq_financial_sync_code_audits_run_code",
      ),
      comment="上市公司逐标的财务同步验证记录",
    )
    op.create_index(
      "ix_financial_sync_code_audits_run_id",
      "financial_sync_code_audits",
      ["run_id"],
    )
    op.create_index(
      "ix_financial_sync_code_audits_code_run",
      "financial_sync_code_audits",
      ["stock_code", "run_id"],
    )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
