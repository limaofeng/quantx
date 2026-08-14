"""Add the account-level limit-up board assistant."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260813_0009"
down_revision = "20260813_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
  bind = op.get_bind()
  if bind.dialect.name == "postgresql":
    op.execute(
      "ALTER TYPE strategy_instrument_universe_mode "
      "ADD VALUE IF NOT EXISTS 'RADAR_CANDIDATES'"
    )

  tables = set(inspect(bind).get_table_names())
  if "limit_up_board_assistant_configs" not in tables:
    op.create_table(
      "limit_up_board_assistant_configs",
      sa.Column("id", sa.String(length=36), primary_key=True),
      sa.Column("account_id", sa.String(length=50), nullable=False),
      sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
      sa.Column("mode", sa.String(length=16), nullable=False, server_default="paper"),
      sa.Column(
        "auto_exit_acknowledged",
        sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
      ),
      sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
      sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
      sa.Column("strategy_run_id", sa.String(length=36), nullable=True),
      sa.Column("universe_revision", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("last_reconciled_at", sa.DateTime(), nullable=True),
      sa.Column("last_error", sa.Text(), nullable=True),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint("account_id", name="uq_limit_up_board_assistant_account"),
      comment="账户级打板助手配置",
    )
    op.create_index(
      "ix_limit_up_board_assistant_configs_account_id",
      "limit_up_board_assistant_configs",
      ["account_id"],
      unique=True,
    )
    op.create_index(
      "ix_limit_up_board_assistant_configs_strategy_run_id",
      "limit_up_board_assistant_configs",
      ["strategy_run_id"],
    )

  if "limit_up_board_candidate_arms" not in tables:
    op.create_table(
      "limit_up_board_candidate_arms",
      sa.Column("id", sa.String(length=36), primary_key=True),
      sa.Column("account_id", sa.String(length=50), nullable=False),
      sa.Column("trade_date", sa.Date(), nullable=False),
      sa.Column("instrument_code", sa.String(length=20), nullable=False),
      sa.Column("armed", sa.Boolean(), nullable=False, server_default=sa.true()),
      sa.Column("source", sa.String(length=16), nullable=False, server_default="MANUAL"),
      sa.Column("actor_id", sa.String(length=64), nullable=False, server_default=""),
      sa.Column("idempotency_key", sa.String(length=128), nullable=False, server_default=""),
      sa.Column("arm_version", sa.Integer(), nullable=False, server_default="1"),
      sa.Column("disarmed_at", sa.DateTime(), nullable=True),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint(
        "account_id",
        "trade_date",
        "instrument_code",
        name="uq_limit_up_board_candidate_arm",
      ),
      comment="账户当日人工布防的打板候选",
    )
    op.create_index(
      "ix_limit_up_board_candidate_arms_account_id",
      "limit_up_board_candidate_arms",
      ["account_id"],
    )
    op.create_index(
      "ix_limit_up_board_candidate_arms_trade_date",
      "limit_up_board_candidate_arms",
      ["trade_date"],
    )
    op.create_index(
      "ix_limit_up_board_candidate_arms_instrument_code",
      "limit_up_board_candidate_arms",
      ["instrument_code"],
    )

  if "limit_up_board_assistant_projections" not in tables:
    op.create_table(
      "limit_up_board_assistant_projections",
      sa.Column("account_id", sa.String(length=50), primary_key=True),
      sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
      sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
      sa.Column("generated_at", sa.DateTime(), nullable=False),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      comment="账户级打板助手读投影",
    )


def downgrade() -> None:
  op.drop_table("limit_up_board_assistant_projections")
  op.drop_table("limit_up_board_candidate_arms")
  op.drop_table("limit_up_board_assistant_configs")
  bind = op.get_bind()
  if bind.dialect.name != "postgresql":
    return

  # PostgreSQL cannot drop one enum label in place. Convert assistant templates
  # back to the pre-feature default, then rebuild the enum so the migration is
  # genuinely reversible instead of leaving RADAR_CANDIDATES behind.
  op.execute(
    "UPDATE strategies SET instrument_universe_mode = 'STATIC' "
    "WHERE instrument_universe_mode = 'RADAR_CANDIDATES'"
  )
  op.execute(
    "ALTER TYPE strategy_instrument_universe_mode "
    "RENAME TO strategy_instrument_universe_mode_with_radar"
  )
  op.execute(
    "CREATE TYPE strategy_instrument_universe_mode "
    "AS ENUM ('STATIC', 'ACCOUNT_HOLDINGS')"
  )
  op.execute(
    "ALTER TABLE strategies ALTER COLUMN instrument_universe_mode "
    "TYPE strategy_instrument_universe_mode "
    "USING instrument_universe_mode::text::strategy_instrument_universe_mode"
  )
  op.execute("DROP TYPE strategy_instrument_universe_mode_with_radar")
