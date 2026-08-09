"""
数据库模型 - 节假日信息表
"""

from sqlalchemy import Column, Date, Integer, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class Holiday(Base, TimestampMixin):
  """节假日信息表"""

  __tablename__ = "holidays"

  id = Column(Integer, primary_key=True, index=True, comment="主键")

  # 市场和地区信息
  market = Column(String(20), nullable=False, comment="市场代码，如 CN, US, HK")

  # 时间信息
  year = Column(Integer, nullable=False, comment="年度")
  date = Column(Date, nullable=False, comment="具体日期")

  # 节假日详情
  description = Column(String(200), comment="节假日说明")
