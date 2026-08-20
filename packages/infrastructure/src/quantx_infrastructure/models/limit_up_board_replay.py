"""Durable point-in-time inputs and lifecycle for board-assistant replays."""

import uuid

from sqlalchemy import (
  JSON,
  BigInteger,
  CheckConstraint,
  Column,
  Date,
  DateTime,
  Float,
  ForeignKey,
  Index,
  Integer,
  String,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class LimitUpBoardUniverseSnapshot(Base, TimestampMixin):
  """Immutable, account-independent radar universe observed at one market time."""

  __tablename__ = "limit_up_board_universe_snapshots"
  __table_args__ = (
    UniqueConstraint("snapshot_key", name="uq_limit_up_board_universe_snapshot_key"),
    Index(
      "ix_limit_up_board_universe_date_asof",
      "trade_date",
      "observed_at",
    ),
    CheckConstraint("candidate_count >= 0", name="ck_board_universe_candidate_count"),
    CheckConstraint("eligible_count >= 0", name="ck_board_universe_eligible_count"),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  snapshot_key = Column(String(96), nullable=False)
  trade_date = Column(Date, nullable=False, index=True)
  observed_at = Column(DateTime, nullable=False)
  source_max_at = Column(DateTime, nullable=True)
  schema_version = Column(Integer, nullable=False, default=1)
  snapshot_version = Column(String(64), nullable=False)
  score_version = Column(String(64), nullable=False)
  feature_version = Column(String(64), nullable=False)
  model_version = Column(String(64), nullable=False)
  exit_policy_version = Column(String(64), nullable=False)
  candidate_count = Column(Integer, nullable=False, default=0)
  eligible_count = Column(Integer, nullable=False, default=0)
  payload = Column(JSON, nullable=False, default=dict)


class LimitUpBoardReplayJob(Base, TimestampMixin):
  """Account-level replay job grouping a fixed set of execution scenarios."""

  __tablename__ = "limit_up_board_replay_jobs"
  __table_args__ = (
    Index(
      "ix_limit_up_board_replay_account_status",
      "account_id",
      "status",
    ),
    CheckConstraint(
      "status IN ('PENDING','STARTING','RUNNING','COMPLETED','CANCELLED','ERROR')",
      name="ck_limit_up_board_replay_job_status",
    ),
    CheckConstraint(
      "progress_pct >= 0 AND progress_pct <= 100",
      name="ck_limit_up_board_replay_job_progress",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  account_id = Column(String(50), nullable=False, index=True)
  status = Column(String(20), nullable=False, default="PENDING")
  progress_pct = Column(Float, nullable=False, default=0.0)
  processed_until = Column(DateTime, nullable=True)
  revision = Column(BigInteger, nullable=False, default=1)
  scenario_profile = Column(String(32), nullable=False, default="STANDARD_V1")
  request = Column(JSON, nullable=False, default=dict)
  dataset_fingerprint = Column(String(64), nullable=False)
  config_fingerprint = Column(String(64), nullable=False)
  input_manifest = Column(JSON, nullable=False, default=dict)
  data_quality = Column(JSON, nullable=False, default=dict)
  error_message = Column(String(512), nullable=True)
  started_at = Column(DateTime, nullable=True)
  completed_at = Column(DateTime, nullable=True)


class LimitUpBoardReplayScenario(Base, TimestampMixin):
  """One immutable execution assumption linked to its authoritative backtest."""

  __tablename__ = "limit_up_board_replay_scenarios"
  __table_args__ = (
    UniqueConstraint(
      "job_id",
      "scenario_id",
      name="uq_limit_up_board_replay_job_scenario",
    ),
    CheckConstraint(
      "confirmation_delay_ms >= 0",
      name="ck_limit_up_board_replay_delay",
    ),
    CheckConstraint(
      "participation_cap_pct > 0 AND participation_cap_pct <= 1",
      name="ck_limit_up_board_replay_participation",
    ),
    CheckConstraint(
      "book_depth_participation_pct > 0 AND book_depth_participation_pct <= 1",
      name="ck_limit_up_board_replay_depth_participation",
    ),
    CheckConstraint(
      "status IN ('PENDING','STARTING','RUNNING','COMPLETED','CANCELLED','ERROR')",
      name="ck_limit_up_board_replay_scenario_status",
    ),
    CheckConstraint(
      "progress_pct >= 0 AND progress_pct <= 100",
      name="ck_limit_up_board_replay_scenario_progress",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  job_id = Column(
    String(36),
    ForeignKey("limit_up_board_replay_jobs.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  scenario_id = Column(String(32), nullable=False)
  backtest_id = Column(
    String(36),
    ForeignKey("strategy_backtests.id", ondelete="RESTRICT"),
    nullable=False,
    unique=True,
  )
  status = Column(String(20), nullable=False, default="PENDING")
  progress_pct = Column(Float, nullable=False, default=0.0)
  processed_until = Column(DateTime, nullable=True)
  revision = Column(BigInteger, nullable=False, default=1)
  error_message = Column(String(512), nullable=True)
  confirmation_delay_ms = Column(Integer, nullable=False)
  participation_cap_pct = Column(Float, nullable=False)
  book_depth_participation_pct = Column(Float, nullable=False)
