"""Durable source-trade ledger for externally imported T batches."""

import uuid

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint

from database.relational_base import Base, TimestampMixin


class TTradeImportedEntry(Base, TimestampMixin):
  __tablename__ = "t_trade_imported_entries"
  __table_args__ = (
    UniqueConstraint("account_id", "source_trade_id", name="uq_t_trade_source_trade"),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  account_id = Column(String(50), nullable=False, index=True)
  source_trade_id = Column(String(100), nullable=False)
  source_order_id = Column(String(100), nullable=True)
  source_trade_time = Column(DateTime, nullable=True)
  stock_code = Column(String(32), nullable=False)
  volume = Column(Integer, nullable=False)
  price = Column(Float, nullable=False)
  strategy_run_id = Column(String(36), nullable=False, index=True)
  batch_id = Column(String(36), nullable=False)
  status = Column(String(20), nullable=False, default="IMPORTED")
