"""Durable, device-bound confirmation challenges for sensitive trade actions."""

from sqlalchemy import (
  JSON,
  Column,
  DateTime,
  ForeignKey,
  Index,
  String,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class TradeConfirmationChallenge(Base, TimestampMixin):
  """Generic one-time challenge; raw confirmation tokens are never stored."""

  __tablename__ = "trade_confirmation_challenges"
  __table_args__ = (
    UniqueConstraint(
      "user_id",
      "account_id",
      "action",
      "idempotency_key",
      name="uq_trade_confirmation_challenge_idempotency",
    ),
    Index(
      "ix_trade_confirmation_challenge_session_expiry",
      "device_session_id",
      "expires_at",
    ),
  )

  id = Column(String(36), primary_key=True)
  action = Column(String(48), nullable=False)
  user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  device_session_id = Column(
    String(36),
    ForeignKey("auth_device_sessions.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  account_id = Column(String(50), nullable=False, index=True)
  idempotency_key = Column(String(128), nullable=False)
  payload = Column(JSON, nullable=False)
  payload_fingerprint = Column(String(64), nullable=False)
  token_digest = Column(String(64), nullable=False)
  expires_at = Column(DateTime, nullable=False, index=True)
  consumed_at = Column(DateTime, nullable=True)
  result_reference = Column(JSON, nullable=True)
