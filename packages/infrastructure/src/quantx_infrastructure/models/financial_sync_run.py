"""Durable audit record for each full financial synchronization run."""

from sqlalchemy import JSON, Column, Date, DateTime, Integer, String, Text

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class FinancialSyncRun(Base, TimestampMixin):
  """Aggregate download, transfer, persistence, and metric-rebuild outcome."""

  __tablename__ = "financial_sync_runs"

  id = Column(Integer, primary_key=True, autoincrement=True, comment="运行ID")
  status = Column(String(32), nullable=False, default="running", index=True)
  started_at = Column(DateTime, nullable=False, comment="开始时间")
  completed_at = Column(DateTime, nullable=True, comment="完成时间")
  window_start = Column(Date, nullable=False, comment="公告窗口开始日期")
  window_end = Column(Date, nullable=False, comment="公告窗口结束日期")
  batch_count = Column(Integer, nullable=False, default=0, comment="批次数")
  failed_batches = Column(Integer, nullable=False, default=0, comment="失败批次数")
  requested_codes = Column(Integer, nullable=False, default=0, comment="请求标的数")
  synced_codes = Column(Integer, nullable=False, default=0, comment="有财务数据标的数")
  empty_codes = Column(Integer, nullable=False, default=0, comment="空数据标的数")
  statement_rows = Column(Integer, nullable=False, default=0, comment="四表写入行数")
  metric_rows = Column(Integer, nullable=False, default=0, comment="指标快照行数")
  warnings = Column(Text, nullable=True, comment="运行警告")
  details = Column(JSON, nullable=False, default=dict, comment="批次与异常明细")
