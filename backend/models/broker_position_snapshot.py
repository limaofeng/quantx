"""Metadata for the latest successful full broker position snapshot."""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text

from database.relational_base import Base, TimestampMixin


class BrokerPositionSnapshot(Base, TimestampMixin):
  __tablename__ = "broker_position_snapshots"

  account_id = Column(String(50), primary_key=True)
  sequence = Column(BigInteger, nullable=False, default=0)
  source = Column(String(32), nullable=False, default="MINIQMT")
  reported_at = Column(DateTime, nullable=True)
  received_at = Column(DateTime, nullable=True)
  position_count = Column(Integer, nullable=False, default=0)
  is_complete = Column(Boolean, nullable=False, default=False)
  last_error = Column(Text, nullable=True)

  def to_dict(self):
    return {
      "account_id": self.account_id,
      "sequence": int(self.sequence or 0),
      "source": self.source,
      "reported_at": self.reported_at,
      "received_at": self.received_at,
      "position_count": int(self.position_count or 0),
      "is_complete": bool(self.is_complete),
      "last_error": self.last_error,
    }
