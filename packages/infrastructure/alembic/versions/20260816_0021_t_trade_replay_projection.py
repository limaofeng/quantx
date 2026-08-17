"""Add durable lifecycle projections for T-trade historical replays."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260816_0021"
down_revision = "20260816_0020"
branch_labels = None
depends_on = None


def _mapping(value: object) -> dict:
  return dict(value) if isinstance(value, dict) else {}


def _parse_datetime(value: object) -> datetime | None:
  if not isinstance(value, str) or not value:
    return None
  try:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
  except ValueError:
    return None


def upgrade() -> None:
  bind = op.get_bind()
  tables = set(inspect(bind).get_table_names())
  if "t_trade_replay_projections" not in tables:
    op.create_table(
      "t_trade_replay_projections",
      sa.Column(
        "run_id",
        sa.String(36),
        sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        primary_key=True,
      ),
      sa.Column("account_id", sa.String(50), nullable=False),
      sa.Column("status", sa.String(20), nullable=False),
      sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0"),
      sa.Column("processed_until", sa.DateTime(), nullable=True),
      sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      comment="做 T 历史回放生命周期投影",
    )
    op.create_index(
      "ix_t_trade_replay_projections_account_id",
      "t_trade_replay_projections",
      ["account_id"],
    )
    op.create_index(
      "ix_t_trade_replay_projections_account_status",
      "t_trade_replay_projections",
      ["account_id", "status"],
    )

  strategy_runs = sa.table(
    "strategy_runs",
    sa.column("id", sa.String(36)),
    sa.column("parameters", sa.JSON()),
    sa.column("status", sa.String()),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
  )
  strategy_backtests = sa.table(
    "strategy_backtests",
    sa.column("strategy_run_id", sa.String(36)),
    sa.column("version", sa.Integer()),
    sa.column("status", sa.String(20)),
  )
  projections = sa.table(
    "t_trade_replay_projections",
    sa.column("run_id", sa.String(36)),
    sa.column("account_id", sa.String(50)),
    sa.column("status", sa.String(20)),
    sa.column("progress_pct", sa.Float()),
    sa.column("processed_until", sa.DateTime()),
    sa.column("revision", sa.BigInteger()),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
  )
  existing = {
    row[0] for row in bind.execute(sa.select(projections.c.run_id)).fetchall()
  }
  now = datetime.now()
  for row in bind.execute(sa.select(strategy_runs)).mappings():
    params = _mapping(row["parameters"])
    if not params.get("t_trade_replay") or row["id"] in existing:
      continue
    account_id = str(params.get("account_id") or "").strip()
    if not account_id:
      continue
    run_status = str(row["status"] or "PENDING").upper()
    latest_backtest = bind.execute(
      sa.select(strategy_backtests.c.status)
      .where(strategy_backtests.c.strategy_run_id == row["id"])
      .order_by(strategy_backtests.c.version.desc())
      .limit(1)
    ).scalar_one_or_none()
    backtest_status = str(latest_backtest or "").upper()
    status = (
      backtest_status
      if backtest_status in {"CANCELLED", "COMPLETED", "ERROR"}
      else run_status
    )
    created_at = row["created_at"] or now
    updated_at = row["updated_at"] or created_at
    bind.execute(
      projections.insert().values(
        run_id=row["id"],
        account_id=account_id,
        status=status,
        progress_pct=100.0 if status == "COMPLETED" else 0.0,
        processed_until=(
          _parse_datetime(params.get("replay_end_time"))
          if status == "COMPLETED"
          else None
        ),
        revision=1,
        created_at=created_at,
        updated_at=updated_at,
      )
    )


def downgrade() -> None:
  op.drop_index(
    "ix_t_trade_replay_projections_account_status",
    table_name="t_trade_replay_projections",
  )
  op.drop_index(
    "ix_t_trade_replay_projections_account_id",
    table_name="t_trade_replay_projections",
  )
  op.drop_table("t_trade_replay_projections")
