"""Durable lifecycle projection for one T-trade historical replay."""

from sqlalchemy import JSON, BigInteger, Column, DateTime, Float, ForeignKey, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class TTradeReplayProjection(Base, TimestampMixin):
  __tablename__ = "t_trade_replay_projections"

  run_id = Column(
    String(36),
    ForeignKey("strategy_runs.id", ondelete="CASCADE"),
    primary_key=True,
  )
  account_id = Column(String(50), nullable=False, index=True)
  status = Column(String(20), nullable=False, default="PENDING")
  progress_pct = Column(Float, nullable=False, default=0.0)
  phase = Column(String(32), nullable=False, default="VALIDATING_PORTFOLIO")
  phase_progress_pct = Column(Float, nullable=False, default=0.0)
  phase_message = Column(String(500), nullable=False, default="")
  data_preparation = Column(JSON, nullable=False, default=dict)
  processed_until = Column(DateTime, nullable=True)
  revision = Column(BigInteger, nullable=False, default=1)
