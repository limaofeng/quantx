"""Strategy GridBook snapshot model."""

from sqlalchemy import JSON, Column, ForeignKey, Index, Integer, String, Text

from quantx_infrastructure.database.relational_base import BaseModel, TimestampMixin


class StrategyGridBookSnapshot(BaseModel, TimestampMixin):
  """结构化存储 GridBook 查询快照。

  JSONL 仍保留完整审计流水；本表只承载 UI 高频查询所需的当前/最终快照。
  """

  __tablename__ = "strategy_grid_book_snapshots"

  id = Column(String(96), primary_key=True, comment="快照键")
  strategy_run_id = Column(
    String(36),
    ForeignKey("strategy_runs.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
    comment="策略运行实例 ID",
  )
  backtest_id = Column(
    String(36),
    ForeignKey("strategy_backtests.id", ondelete="CASCADE"),
    nullable=True,
    index=True,
    comment="回测版本 ID；当前态为空",
  )
  backtest_version = Column(Integer, nullable=True, index=True, comment="回测版本号")
  mode = Column(String(20), nullable=True, index=True, comment="运行模式")
  snapshot_type = Column(
    String(32),
    nullable=False,
    index=True,
    comment="快照类型：CURRENT / BACKTEST_FINAL",
  )
  instrument_code = Column(String(20), nullable=True, index=True, comment="标的代码")
  grid_book_version = Column(Integer, nullable=False, default=1, comment="GridBook 内部版本")
  parameter_version = Column(String(50), nullable=True, comment="参数版本")
  snapshot = Column(JSON, nullable=False, default=dict, comment="GridBook 快照 JSON")
  snapshot_count = Column(Integer, nullable=False, default=0, comment="落盘快照数")
  observed_count = Column(Integer, nullable=False, default=0, comment="观测到的快照数")
  source_path = Column(String(255), nullable=True, comment="冷审计文件路径")
  note = Column(Text, nullable=True, comment="备注")

  __table_args__ = (
    Index("idx_grid_book_run_type", "strategy_run_id", "snapshot_type"),
    Index("idx_grid_book_backtest", "backtest_id", "snapshot_type"),
    Index("idx_grid_book_run_version", "strategy_run_id", "backtest_version"),
  )

  def to_dict(self):
    return {
      "id": self.id,
      "strategy_run_id": self.strategy_run_id,
      "backtest_id": self.backtest_id,
      "backtest_version": self.backtest_version,
      "mode": self.mode,
      "snapshot_type": self.snapshot_type,
      "instrument_code": self.instrument_code,
      "grid_book_version": self.grid_book_version,
      "parameter_version": self.parameter_version,
      "snapshot": self.snapshot or {},
      "snapshot_count": self.snapshot_count,
      "observed_count": self.observed_count,
      "source_path": self.source_path,
      "note": self.note,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }
