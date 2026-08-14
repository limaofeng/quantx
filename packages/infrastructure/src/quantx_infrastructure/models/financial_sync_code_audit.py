"""Per-instrument financial synchronization verification records."""

from sqlalchemy import (
  JSON,
  Column,
  Date,
  DateTime,
  ForeignKey,
  Index,
  Integer,
  String,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class FinancialSyncCodeAudit(Base, TimestampMixin):
  """Verification outcome for one instrument in one full sync run."""

  __tablename__ = "financial_sync_code_audits"
  __table_args__ = (
    UniqueConstraint(
      "run_id",
      "stock_code",
      name="uq_financial_sync_code_audits_run_code",
    ),
    Index(
      "ix_financial_sync_code_audits_code_run",
      "stock_code",
      "run_id",
    ),
  )

  id = Column(Integer, primary_key=True, autoincrement=True)
  run_id = Column(
    Integer,
    ForeignKey("financial_sync_runs.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
    comment="财务同步运行ID",
  )
  stock_code = Column(String(20), nullable=False, comment="标的代码")
  window_start = Column(Date, nullable=False, comment="同步公告窗口开始日期")
  window_end = Column(Date, nullable=False, comment="同步公告窗口结束日期")
  status = Column(
    String(20),
    nullable=False,
    comment="SUCCESS/EMPTY/FAILED",
  )
  statement_rows = Column(Integer, nullable=False, default=0, comment="四表行数")
  metric_rows = Column(Integer, nullable=False, default=0, comment="指标快照行数")
  verified_at = Column(DateTime, nullable=True, comment="成功验证时间")
  details = Column(JSON, nullable=False, default=dict, comment="验证与异常明细")
