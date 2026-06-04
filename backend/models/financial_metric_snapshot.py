"""财务指标派生快照模型。"""

from sqlalchemy import ARRAY, Column, Date, DateTime, Float, String

from database.relational_base import Base, TimestampMixin


class FinancialMetricSnapshot(Base, TimestampMixin):
  """由财务四表计算出的选股指标快照。"""

  __tablename__ = "financial_metric_snapshots"

  code = Column(String(20), primary_key=True, comment="标的代码")
  as_of_date = Column(Date, primary_key=True, comment="指标可见日期")
  report_date = Column(Date, primary_key=True, comment="报告截止日")
  announce_date = Column(Date, comment="当前报告公告日")

  roe_ttm = Column(Float, comment="TTM归母ROE(%)")
  net_profit_ttm = Column(Float, comment="TTM归母净利润")
  net_profit_growth_pct = Column(Float, comment="归母净利润累计同比增速(%)")
  revenue_growth_pct = Column(Float, comment="营业收入累计同比增速(%)")
  net_profit_quarter_growth_pct = Column(Float, comment="归母净利润单季同比增速(%)")
  revenue_quarter_growth_pct = Column(Float, comment="营业收入单季同比增速(%)")

  quality_status = Column(String(20), nullable=False, default="invalid", comment="valid/partial/invalid")
  quality_flags = Column(ARRAY(String), nullable=False, default=list, comment="数据质量标记")
  calculated_at = Column(DateTime, nullable=False, comment="指标计算时间")
