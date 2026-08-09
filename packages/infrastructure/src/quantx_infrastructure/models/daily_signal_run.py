"""
日级信号运行元信息模型。

查询层通过本表判断某个交易日快照是否已完成，以及对应版本和完成时间。
"""

from sqlalchemy import Column, Date, DateTime, Float, Integer, String, Text

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class DailySignalRun(Base, TimestampMixin):
  """日级信号批量计算运行日志"""

  __tablename__ = "daily_signal_runs"

  id = Column(Integer, primary_key=True, autoincrement=True, comment="运行ID")
  snapshot_date = Column(Date, nullable=False, index=True, comment="信号交易日")
  signal_version = Column(String(64), nullable=False, index=True, comment="信号版本")
  score_version = Column(String(64), nullable=False, default="score-v1", comment="评分版本")
  status = Column(String(32), nullable=False, default="running", index=True, comment="运行状态")
  started_at = Column(DateTime, nullable=False, comment="开始时间")
  completed_at = Column(DateTime, nullable=True, comment="完成时间")
  total_codes = Column(Integer, nullable=False, default=0, comment="标的总数")
  saved = Column(Integer, nullable=False, default=0, comment="写入数量")
  skipped = Column(Integer, nullable=False, default=0, comment="跳过数量")
  failed = Column(Integer, nullable=False, default=0, comment="失败数量")
  elapsed_seconds = Column(Float, nullable=True, comment="耗时秒数")
  warnings = Column(Text, nullable=True, comment="运行警告")
