"""Durable intraday lifecycle events emitted by the limit-up radar."""

from sqlalchemy import JSON, Column, Date, DateTime, Float, Index, String

from quantx_infrastructure.database.relational_base import Base


class LimitUpRadarEvent(Base):
  __tablename__ = "limit_up_radar_events"
  __table_args__ = (
    Index(
      "ix_limit_up_radar_event_date_code_time",
      "trade_date",
      "instrument_code",
      "occurred_at",
    ),
    {"comment": "全市场打板雷达阶段事件"},
  )

  event_id = Column(String(36), primary_key=True)
  trade_date = Column(Date, nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  stage = Column(String(24), nullable=False)
  occurred_at = Column(DateTime, nullable=False)
  score = Column(Float, nullable=False, default=0.0)
  score_version = Column(String(40), nullable=False, default="limit-up-radar-v1")
  snapshot = Column(JSON, nullable=False, default=dict)
