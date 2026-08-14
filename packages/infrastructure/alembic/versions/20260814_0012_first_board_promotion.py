"""Add first-board promotion V2 market facts and AI research artifacts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260814_0012"
down_revision = "20260814_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
  bind = op.get_bind()
  tables = set(inspect(bind).get_table_names())

  if "limit_up_chain_snapshots" not in tables:
    op.create_table(
      "limit_up_chain_snapshots",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column("trade_date", sa.Date(), nullable=False),
      sa.Column("as_of", sa.DateTime(), nullable=False),
      sa.Column("snapshot_version", sa.String(64), nullable=False),
      sa.Column("score_version", sa.String(64), nullable=False),
      sa.Column("max_board_count", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("first_board_count", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("sealed_count", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("broken_count", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("break_rate", sa.Float(), nullable=False, server_default="0"),
      sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint("trade_date", "snapshot_version", name="uq_limit_up_chain_version"),
      comment="涨停连板梯队不可变快照",
    )
    op.create_index("ix_limit_up_chain_snapshots_trade_date", "limit_up_chain_snapshots", ["trade_date"])
    op.create_index("ix_limit_up_chain_trade_asof", "limit_up_chain_snapshots", ["trade_date", "as_of"])

  if "limit_up_lifecycle_snapshots" not in tables:
    op.create_table(
      "limit_up_lifecycle_snapshots",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column("trade_date", sa.Date(), nullable=False),
      sa.Column("instrument_code", sa.String(20), nullable=False),
      sa.Column("stage", sa.String(32), nullable=False),
      sa.Column("as_of", sa.DateTime(), nullable=False),
      sa.Column("snapshot_version", sa.String(64), nullable=False),
      sa.Column("feature_version", sa.String(64), nullable=False),
      sa.Column("ever_touched_limit", sa.Boolean(), nullable=False, server_default=sa.false()),
      sa.Column("break_count", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint("trade_date", "instrument_code", "snapshot_version", name="uq_limit_up_lifecycle_version"),
      comment="涨停候选生命周期不可变快照",
    )
    op.create_index("ix_limit_up_lifecycle_snapshots_trade_date", "limit_up_lifecycle_snapshots", ["trade_date"])
    op.create_index("ix_limit_up_lifecycle_snapshots_instrument_code", "limit_up_lifecycle_snapshots", ["instrument_code"])
    op.create_index("ix_limit_up_lifecycle_date_code_asof", "limit_up_lifecycle_snapshots", ["trade_date", "instrument_code", "as_of"])

  if "first_board_promotion_assessments" not in tables:
    op.create_table(
      "first_board_promotion_assessments",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column("lifecycle_snapshot_id", sa.String(36), nullable=False),
      sa.Column("trade_date", sa.Date(), nullable=False),
      sa.Column("instrument_code", sa.String(20), nullable=False),
      sa.Column("as_of", sa.DateTime(), nullable=False),
      sa.Column("model_version", sa.String(64), nullable=False),
      sa.Column("exit_policy_version", sa.String(64), nullable=False),
      sa.Column("segment", sa.String(24), nullable=False),
      sa.Column("eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
      sa.Column("rank_score", sa.Float(), nullable=False, server_default="0"),
      sa.Column("first_board_close_probability", sa.Float(), nullable=False, server_default="0"),
      sa.Column("next_day_limit_touch_probability", sa.Float(), nullable=False, server_default="0"),
      sa.Column("next_day_limit_seal_probability", sa.Float(), nullable=False, server_default="0"),
      sa.Column("expected_net_return_pct", sa.Float(), nullable=False, server_default="0"),
      sa.Column("cvar95_loss_pct", sa.Float(), nullable=False, server_default="0"),
      sa.Column("high_position_type", sa.String(32), nullable=False),
      sa.Column("veto_reasons", sa.JSON(), nullable=False, server_default="[]"),
      sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint("lifecycle_snapshot_id", "model_version", name="uq_first_board_assessment_model"),
      comment="首板晋级确定性评估快照",
    )
    op.create_index("ix_first_board_promotion_assessments_lifecycle_snapshot_id", "first_board_promotion_assessments", ["lifecycle_snapshot_id"])
    op.create_index("ix_first_board_promotion_assessments_trade_date", "first_board_promotion_assessments", ["trade_date"])
    op.create_index("ix_first_board_promotion_assessments_instrument_code", "first_board_promotion_assessments", ["instrument_code"])
    op.create_index("ix_first_board_assessment_rank", "first_board_promotion_assessments", ["trade_date", "eligible", "rank_score"])

  if "first_board_candidate_preferences" not in tables:
    op.create_table(
      "first_board_candidate_preferences",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column("account_id", sa.String(50), nullable=False),
      sa.Column("trade_date", sa.Date(), nullable=False),
      sa.Column("instrument_code", sa.String(20), nullable=False),
      sa.Column("preference", sa.String(16), nullable=False, server_default="PREFER"),
      sa.Column("actor_id", sa.String(64), nullable=False, server_default=""),
      sa.Column("idempotency_key", sa.String(128), nullable=False, server_default=""),
      sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint("account_id", "trade_date", "instrument_code", name="uq_first_board_candidate_preference"),
      comment="账户首板候选偏好",
    )
    for column in ("account_id", "trade_date", "instrument_code"):
      op.create_index(f"ix_first_board_candidate_preferences_{column}", "first_board_candidate_preferences", [column])

  if "limit_up_research_jobs" not in tables:
    op.create_table(
      "limit_up_research_jobs",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column("assessment_id", sa.String(36), nullable=False),
      sa.Column("trade_date", sa.Date(), nullable=False),
      sa.Column("instrument_code", sa.String(20), nullable=False),
      sa.Column("input_snapshot_version", sa.String(64), nullable=False),
      sa.Column("agent_id", sa.String(64), nullable=False, server_default="limit_up_research_assistant"),
      sa.Column("status", sa.String(24), nullable=False, server_default="QUEUED"),
      sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("idempotency_key", sa.String(160), nullable=False),
      sa.Column("lease_owner", sa.String(96), nullable=True),
      sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
      sa.Column("started_at", sa.DateTime(), nullable=True),
      sa.Column("finished_at", sa.DateTime(), nullable=True),
      sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("error_code", sa.String(64), nullable=True),
      sa.Column("error_message", sa.String(512), nullable=True),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint("idempotency_key", name="uq_limit_up_research_job_idempotency"),
      comment="首板候选AI研究任务",
    )
    for column in ("assessment_id", "trade_date", "instrument_code"):
      op.create_index(f"ix_limit_up_research_jobs_{column}", "limit_up_research_jobs", [column])
    op.create_index("ix_limit_up_research_job_queue", "limit_up_research_jobs", ["status", "priority", "created_at"])
    op.create_index("ix_limit_up_research_job_daily_code", "limit_up_research_jobs", ["trade_date", "instrument_code"])

  if "limit_up_research_artifacts" not in tables:
    op.create_table(
      "limit_up_research_artifacts",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column("job_id", sa.String(36), nullable=False),
      sa.Column("assessment_id", sa.String(36), nullable=False),
      sa.Column("trade_date", sa.Date(), nullable=False),
      sa.Column("instrument_code", sa.String(20), nullable=False),
      sa.Column("input_snapshot_version", sa.String(64), nullable=False),
      sa.Column("agent_id", sa.String(64), nullable=False),
      sa.Column("model", sa.String(80), nullable=False),
      sa.Column("prompt_version", sa.String(32), nullable=False),
      sa.Column("status", sa.String(24), nullable=False, server_default="COMPLETED"),
      sa.Column("summary", sa.Text(), nullable=False, server_default=""),
      sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
      sa.Column("citations", sa.JSON(), nullable=False, server_default="[]"),
      sa.Column("generated_at", sa.DateTime(), nullable=False),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint("job_id", name="uq_limit_up_research_artifact_job"),
      comment="首板候选市场级共享AI研究产物",
    )
    for column in ("job_id", "assessment_id", "trade_date", "instrument_code"):
      op.create_index(f"ix_limit_up_research_artifacts_{column}", "limit_up_research_artifacts", [column])
    op.create_index("ix_limit_up_research_artifact_date_code", "limit_up_research_artifacts", ["trade_date", "instrument_code", "generated_at"])

  columns = {column["name"] for column in inspect(bind).get_columns("stock_announcements")}
  additions = (
    ("source_authority", sa.String(40)),
    ("content_text", sa.Text()),
    ("content_hash", sa.String(64)),
    ("content_fetched_at", sa.DateTime()),
  )
  for name, column_type in additions:
    if name not in columns:
      op.add_column("stock_announcements", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
