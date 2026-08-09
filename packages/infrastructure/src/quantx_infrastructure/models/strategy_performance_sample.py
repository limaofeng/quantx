"""Strategy performance sampling model."""

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String

from quantx_infrastructure.database.relational_base import BaseModel, TimestampMixin


class StrategyPerformanceSample(BaseModel, TimestampMixin):
  """Event-level equity and execution sample for a strategy run."""

  __tablename__ = "strategy_performance_samples"
  __table_args__ = (
    Index("ix_strategy_perf_run_sequence", "run_id", "sequence"),
    Index("ix_strategy_perf_backtest_sequence", "backtest_id", "sequence"),
  )

  run_id = Column(
    String(36),
    ForeignKey("strategy_runs.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
    comment="Strategy run id",
  )
  backtest_id = Column(
    String(36),
    ForeignKey("strategy_backtests.id", ondelete="CASCADE"),
    nullable=True,
    index=True,
    comment="Backtest version id, when mode is BACKTEST",
  )
  mode = Column(String(20), nullable=False, index=True, comment="Run mode")
  timestamp = Column(DateTime, nullable=False, index=True, comment="Sample timestamp")
  sequence = Column(Integer, nullable=False, default=0, comment="Per-run sequence")
  event_type = Column(String(32), nullable=False, default="event", comment="Event type")

  equity = Column(Float, nullable=False, default=0.0, comment="Total equity")
  cash = Column(Float, nullable=False, default=0.0, comment="Cash")
  market_value = Column(Float, nullable=False, default=0.0, comment="Market value")
  realized_pnl = Column(Float, nullable=False, default=0.0, comment="Realized PnL")
  unrealized_pnl = Column(Float, nullable=False, default=0.0, comment="Unrealized PnL")
  return_pct = Column(Float, nullable=False, default=0.0, comment="Return percent")
  drawdown_pct = Column(Float, nullable=False, default=0.0, comment="Drawdown percent")
  benchmark_return_pct = Column(Float, nullable=True, comment="Benchmark return percent")

  intent_id = Column(String(64), nullable=True, index=True, comment="Trade intent id")
  order_id = Column(String(64), nullable=True, index=True, comment="Order id")
  trade_id = Column(String(64), nullable=True, index=True, comment="Trade id")
  sample_metadata = Column("metadata", JSON, nullable=False, default=dict)

  def to_dict(self):
    """Serialize sample for aggregation and JSON snapshots."""
    return {
      "id": self.id,
      "run_id": self.run_id,
      "backtest_id": self.backtest_id,
      "mode": self.mode,
      "timestamp": self.timestamp.isoformat() if self.timestamp else None,
      "sequence": self.sequence,
      "event_type": self.event_type,
      "equity": self.equity,
      "cash": self.cash,
      "market_value": self.market_value,
      "realized_pnl": self.realized_pnl,
      "unrealized_pnl": self.unrealized_pnl,
      "return_pct": self.return_pct,
      "drawdown_pct": self.drawdown_pct,
      "benchmark_return_pct": self.benchmark_return_pct,
      "intent_id": self.intent_id,
      "order_id": self.order_id,
      "trade_id": self.trade_id,
      "metadata": self.sample_metadata or {},
    }
