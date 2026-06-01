"""Daily close asset snapshots for account and strategy P&L curves."""

from datetime import date, datetime
from decimal import Decimal
from hashlib import md5
from typing import Any, Dict, Optional

from sqlalchemy import (
  Column,
  Date,
  DateTime,
  Enum,
  ForeignKey,
  Index,
  Integer,
  JSON,
  Numeric,
  String,
  UniqueConstraint,
)

from database.relational_base import Base, TimestampMixin
from models.enums import AccountType


class DailyAssetSnapshot(Base, TimestampMixin):
  """Daily close total-equity snapshot.

  The main P&L source is total assets/equity, not position market value.
  Position-level rows are explanatory only.
  """

  __tablename__ = "daily_asset_snapshots"
  __table_args__ = (
    UniqueConstraint("scope_key", "trade_date", name="uq_daily_asset_scope_date"),
    Index("ix_daily_asset_account_date", "account_id", "trade_date"),
    Index("ix_daily_asset_strategy_date", "strategy_run_id", "trade_date"),
  )

  id = Column(String(64), primary_key=True, index=True, comment="Snapshot id")
  scope_type = Column(String(20), nullable=False, index=True, comment="ACCOUNT/STRATEGY")
  scope_key = Column(String(96), nullable=False, index=True, comment="Stable scope key")
  account_id = Column(String(50), nullable=True, index=True, comment="资金账号")
  account_type = Column(
    Enum(AccountType, name="account_type"), nullable=True, comment="账户类型"
  )
  strategy_run_id = Column(
    String(36),
    ForeignKey("strategy_runs.id", ondelete="SET NULL"),
    nullable=True,
    index=True,
    comment="策略运行实例ID",
  )

  trade_date = Column(Date, nullable=False, index=True, comment="交易日")
  snapshot_at = Column(DateTime, nullable=False, index=True, comment="快照时间")
  source = Column(String(40), nullable=False, default="MINIQMT", comment="数据来源")

  total_asset_cny = Column(Numeric(18, 4), nullable=False, comment="总资产")
  cash_available_cny = Column(Numeric(18, 4), nullable=False, default=0, comment="可用资金")
  cash_frozen_cny = Column(Numeric(18, 4), nullable=False, default=0, comment="冻结资金")
  market_value_cny = Column(Numeric(18, 4), nullable=False, default=0, comment="持仓市值")

  gross_asset_delta_cny = Column(Numeric(18, 4), nullable=True, comment="总资产变动")
  net_capital_flow_cny = Column(
    Numeric(18, 4),
    nullable=False,
    default=0,
    comment="出入金等资本流净额",
  )
  daily_pnl_cny = Column(Numeric(18, 4), nullable=True, comment="当日盈亏")
  daily_return_pct = Column(Numeric(12, 6), nullable=True, comment="当日收益率百分比")

  previous_snapshot_id = Column(
    String(64),
    ForeignKey("daily_asset_snapshots.id", ondelete="SET NULL"),
    nullable=True,
    comment="上一日快照ID",
  )
  data_quality = Column(String(40), nullable=False, default="OK", comment="数据质量")
  snapshot_metadata = Column("metadata", JSON, nullable=False, default=dict)

  @staticmethod
  def make_id(scope_key: str, trade_date: date) -> str:
    raw = f"{scope_key}:{trade_date.isoformat()}"
    return md5(raw.encode("utf-8")).hexdigest()

  def to_dict(self) -> Dict[str, Any]:
    return {
      "id": self.id,
      "scope_type": self.scope_type,
      "scope_key": self.scope_key,
      "account_id": self.account_id,
      "account_type": self.account_type.value if self.account_type else None,
      "strategy_run_id": self.strategy_run_id,
      "trade_date": self.trade_date.isoformat() if self.trade_date else None,
      "snapshot_at": self.snapshot_at.isoformat() if self.snapshot_at else None,
      "source": self.source,
      "total_asset_cny": _decimal_to_float(self.total_asset_cny),
      "cash_available_cny": _decimal_to_float(self.cash_available_cny),
      "cash_frozen_cny": _decimal_to_float(self.cash_frozen_cny),
      "market_value_cny": _decimal_to_float(self.market_value_cny),
      "gross_asset_delta_cny": _decimal_to_float(self.gross_asset_delta_cny),
      "net_capital_flow_cny": _decimal_to_float(self.net_capital_flow_cny),
      "daily_pnl_cny": _decimal_to_float(self.daily_pnl_cny),
      "daily_return_pct": _decimal_to_float(self.daily_return_pct),
      "previous_snapshot_id": self.previous_snapshot_id,
      "data_quality": self.data_quality,
      "metadata": self.snapshot_metadata or {},
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }


class DailyAssetPositionSnapshot(Base, TimestampMixin):
  """Position detail rows attached to a daily asset snapshot."""

  __tablename__ = "daily_asset_position_snapshots"
  __table_args__ = (
    UniqueConstraint(
      "snapshot_id",
      "instrument_code",
      "bucket",
      name="uq_daily_asset_position_snapshot",
    ),
    Index("ix_daily_asset_position_snapshot", "snapshot_id"),
  )

  id = Column(String(96), primary_key=True, index=True, comment="Position snapshot id")
  snapshot_id = Column(
    String(64),
    ForeignKey("daily_asset_snapshots.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
    comment="DailyAssetSnapshot id",
  )
  instrument_code = Column(String(20), nullable=False, index=True, comment="证券代码")
  instrument_name = Column(String(50), nullable=True, comment="证券名称")
  bucket = Column(String(40), nullable=False, default="", comment="策略 bucket")

  volume = Column(Integer, nullable=False, default=0, comment="持仓数量")
  available_volume = Column(Integer, nullable=False, default=0, comment="可用数量")
  frozen_volume = Column(Integer, nullable=False, default=0, comment="冻结数量")
  avg_price = Column(Numeric(18, 6), nullable=True, comment="成本价")
  last_price = Column(Numeric(18, 6), nullable=True, comment="收盘/最新价")
  market_value_cny = Column(Numeric(18, 4), nullable=False, default=0, comment="市值")
  cost_basis_cny = Column(Numeric(18, 4), nullable=True, comment="成本金额")
  unrealized_pnl_cny = Column(Numeric(18, 4), nullable=True, comment="浮动盈亏")
  snapshot_metadata = Column("metadata", JSON, nullable=False, default=dict)

  @staticmethod
  def make_id(snapshot_id: str, instrument_code: str, bucket: str = "") -> str:
    raw = f"{snapshot_id}:{instrument_code}:{bucket or ''}"
    return md5(raw.encode("utf-8")).hexdigest()

  def to_dict(self) -> Dict[str, Any]:
    return {
      "id": self.id,
      "snapshot_id": self.snapshot_id,
      "instrument_code": self.instrument_code,
      "instrument_name": self.instrument_name,
      "bucket": self.bucket,
      "volume": self.volume,
      "available_volume": self.available_volume,
      "frozen_volume": self.frozen_volume,
      "avg_price": _decimal_to_float(self.avg_price),
      "last_price": _decimal_to_float(self.last_price),
      "market_value_cny": _decimal_to_float(self.market_value_cny),
      "cost_basis_cny": _decimal_to_float(self.cost_basis_cny),
      "unrealized_pnl_cny": _decimal_to_float(self.unrealized_pnl_cny),
      "metadata": self.snapshot_metadata or {},
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }


def _decimal_to_float(value: Optional[Decimal]) -> Optional[float]:
  if value is None:
    return None
  return float(value)
