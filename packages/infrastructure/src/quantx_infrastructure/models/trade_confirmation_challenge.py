"""Durable, device-bound confirmation challenges for sensitive trade actions."""

from sqlalchemy import (
  JSON,
  CheckConstraint,
  Column,
  DateTime,
  ForeignKey,
  Index,
  String,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin
from quantx_infrastructure.models.execution_owner import register_identity_immutability


class TradeConfirmationChallenge(Base, TimestampMixin):
  """Generic one-time challenge; raw confirmation tokens are never stored."""

  __tablename__ = "trade_confirmation_challenges"
  __table_args__ = (
    CheckConstraint(
      "(owner_type IS NULL AND owner_id IS NULL AND environment IS NULL) OR "
      "(owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND') AND "
      "length(owner_id) > 0 AND owner_id = trim(owner_id) AND "
      "environment IN ('PAPER','LIVE','BACKTEST'))",
      name="ck_trade_confirmation_owner_shape",
    ),
    CheckConstraint(
      "(action IN ('MANUAL_ORDER','STRATEGY_TRADE_INTENT_APPROVAL',"
      "'T_TRADE_ENTRY_APPROVAL','EXIT_PLAN_SELL_APPROVAL',"
      "'ENTRY_PLAN_AUTHORIZATION','EXIT_PLAN_AUTHORIZATION','LIQUIDATION_GROUP') "
      "AND owner_type IS NOT NULL AND owner_id IS NOT NULL AND "
      "environment IS NOT NULL) OR (action NOT IN "
      "('MANUAL_ORDER','STRATEGY_TRADE_INTENT_APPROVAL',"
      "'T_TRADE_ENTRY_APPROVAL','EXIT_PLAN_SELL_APPROVAL',"
      "'ENTRY_PLAN_AUTHORIZATION','EXIT_PLAN_AUTHORIZATION','LIQUIDATION_GROUP') "
      "AND owner_type IS NULL AND owner_id IS NULL AND environment IS NULL)",
      name="ck_trade_confirmation_execution_owner_required",
    ),
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
  # Control-plane challenges intentionally leave these three fields NULL;
  # execution approvals must carry an explicit owner/environment projection.
  owner_type = Column(String(32), nullable=True)
  owner_id = Column(String(128), nullable=True)
  environment = Column(String(16), nullable=True)
  idempotency_key = Column(String(128), nullable=False)
  payload = Column(JSON, nullable=False)
  payload_fingerprint = Column(String(64), nullable=False)
  token_digest = Column(String(64), nullable=False)
  expires_at = Column(DateTime, nullable=False, index=True)
  consumed_at = Column(DateTime, nullable=True)
  result_reference = Column(JSON, nullable=True)


register_identity_immutability(TradeConfirmationChallenge)
