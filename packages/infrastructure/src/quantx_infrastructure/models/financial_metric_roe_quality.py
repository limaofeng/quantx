"""Independent ROE quality state for one financial metric snapshot."""

from sqlalchemy import ARRAY, Column, Date, ForeignKeyConstraint, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class FinancialMetricRoeQuality(Base, TimestampMixin):
  """Validation state kept separate from other financial metrics."""

  __tablename__ = "financial_metric_roe_qualities"
  __table_args__ = (
    ForeignKeyConstraint(
      ["code", "as_of_date", "report_date"],
      [
        "financial_metric_snapshots.code",
        "financial_metric_snapshots.as_of_date",
        "financial_metric_snapshots.report_date",
      ],
      ondelete="CASCADE",
      name="fk_financial_metric_roe_quality_snapshot",
    ),
  )

  code = Column(String(20), primary_key=True, comment="标的代码")
  as_of_date = Column(Date, primary_key=True, comment="指标可见日期")
  report_date = Column(Date, primary_key=True, comment="报告截止日")
  status = Column(
    String(20),
    nullable=False,
    default="UNVERIFIED",
    comment="VALID/STALE/SUSPICIOUS/INVALID/UNVERIFIED",
  )
  flags = Column(ARRAY(String), nullable=False, default=list, comment="ROE质量标记")
