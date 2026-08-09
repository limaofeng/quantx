"""Durable read projection for the account-level T-trade monitor."""

from sqlalchemy import JSON, BigInteger, Column, DateTime, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class TTradeGlobalMonitorProjection(Base, TimestampMixin):
  __tablename__ = "t_trade_global_monitor_projections"

  account_id = Column(String(50), primary_key=True)
  version = Column(BigInteger, nullable=False, default=0)
  payload = Column(JSON, nullable=False, default=dict)
  generated_at = Column(DateTime, nullable=False)
