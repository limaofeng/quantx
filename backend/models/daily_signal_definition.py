"""
日级信号定义模型。

第一阶段复用 indicator_snapshots 作为日级信号快照表，本表用于记录可查询的
信号目录与版本信息。
"""

from sqlalchemy import Boolean, Column, Integer, String, Text

from database.relational_base import Base, TimestampMixin


class DailySignalDefinition(Base, TimestampMixin):
  """日级信号定义表"""

  __tablename__ = "daily_signal_definitions"

  signal_code = Column(String(64), primary_key=True, comment="信号码")
  display_name = Column(String(100), nullable=False, comment="展示名称")
  category = Column(String(32), nullable=False, default="technical", comment="信号分类")
  description = Column(Text, nullable=True, comment="信号说明")
  expression = Column(Text, nullable=True, comment="白名单表达式或计算说明")
  max_window = Column(Integer, nullable=False, default=252, comment="最大依赖窗口")
  version = Column(String(32), nullable=False, default="daily-signal-v2", comment="定义版本")
  enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")
