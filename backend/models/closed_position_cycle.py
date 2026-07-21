"""Persisted lifecycle records for positions that have returned to zero."""

from sqlalchemy import Column, DateTime, Integer, JSON, Numeric, String

from database.relational_base import Base, TimestampMixin


class ClosedPositionCycle(Base, TimestampMixin):
  """One account/security holding cycle ending when the broker position reaches zero."""

  __tablename__ = "closed_position_cycles"

  id = Column(String(64), primary_key=True, index=True)
  account_id = Column(String(50), nullable=False, index=True)
  account_type = Column(String(30), nullable=True)
  stock_code = Column(String(20), nullable=False, index=True)
  instrument_name = Column(String(50), nullable=True)
  opened_at = Column(DateTime, nullable=True)
  closed_at = Column(DateTime, nullable=False, index=True)
  buy_volume = Column(Integer, nullable=False, default=0)
  sell_volume = Column(Integer, nullable=False, default=0)
  average_buy_price = Column(Numeric(15, 4), nullable=True)
  average_sell_price = Column(Numeric(15, 4), nullable=True)
  gross_buy_amount = Column(Numeric(18, 2), nullable=False, default=0)
  gross_sell_amount = Column(Numeric(18, 2), nullable=False, default=0)
  gross_realized_pnl = Column(Numeric(18, 2), nullable=True)
  gross_realized_pnl_percent = Column(Numeric(12, 4), nullable=True)
  related_trade_ids = Column(JSON, nullable=False, default=list)
  source = Column(String(30), nullable=False, default="POSITION_CALLBACK")
  pnl_quality = Column(String(30), nullable=False, default="INCOMPLETE_HISTORY")
  quality_flags = Column(JSON, nullable=False, default=list)
