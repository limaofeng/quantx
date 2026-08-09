"""Persistent account-level configuration for the global T-trade monitor."""

import uuid

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class TTradeGlobalConfig(Base, TimestampMixin):
  """One global T-trade monitor configuration per broker account."""

  __tablename__ = "t_trade_global_configs"

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  account_id = Column(String(50), nullable=False, unique=True, index=True)
  enabled = Column(Boolean, nullable=False, default=False)
  mode = Column(String(16), nullable=False, default="paper")
  auto_exit_acknowledged = Column(Boolean, nullable=False, default=False)
  ignored_stock_codes = Column(JSON, nullable=False, default=list)
  settings = Column(JSON, nullable=False, default=dict)
  config_version = Column(Integer, nullable=False, default=1)
  strategy_run_id = Column(String(36), nullable=True, index=True)
  universe_revision = Column(Integer, nullable=False, default=0)
  last_reconciled_at = Column(DateTime, nullable=True)
  last_error = Column(Text, nullable=True)

  def to_dict(self):
    return {
      "id": self.id,
      "account_id": self.account_id,
      "enabled": bool(self.enabled),
      "mode": self.mode,
      "auto_exit_acknowledged": bool(self.auto_exit_acknowledged),
      "ignored_stock_codes": list(self.ignored_stock_codes or []),
      "settings": dict(self.settings or {}),
      "config_version": int(self.config_version or 1),
      "strategy_run_id": self.strategy_run_id,
      "universe_revision": int(self.universe_revision or 0),
      "last_reconciled_at": self.last_reconciled_at,
      "last_error": self.last_error,
      "created_at": self.created_at,
      "updated_at": self.updated_at,
    }
