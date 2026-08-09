"""
数据库模型 - 持仓相关数据模型
"""

import math
from hashlib import md5
from typing import Any, Dict, Optional

from sqlalchemy import Column, Enum, Integer, Numeric, String, UniqueConstraint

from quantx_infrastructure.database.relational_base import Base, TimestampMixin
from quantx_infrastructure.models.enums import AccountType


class Position(Base, TimestampMixin):
  """持仓表"""

  __tablename__ = "positions"
  __allow_unmapped__ = True
  __table_args__ = (
    UniqueConstraint("account_id", "stock_code", name="uq_account_stock"),
  )

  # account_id + stock_code 唯一（一个账户对同一股票只能有一条持仓记录）
  id = Column(String(32), primary_key=True, index=True, comment="主键")
  """ 主键ID """

  account_id = Column(String(50), nullable=False, comment="资金账号")
  """ 资金账号 """

  account_type = Column(
    Enum(AccountType, name="account_type"), nullable=True, comment="账户类型"
  )
  """ 账户类型 """

  # 证券信息
  stock_code = Column(String(20), nullable=False, comment="证券代码, 例如'600000.SH'")
  """ 证券代码 """

  instrument_name = Column(String(50), nullable=True, comment="证券名称")
  """ 证券名称 """

  # 持仓数量相关
  volume = Column(
    Integer, default=0, comment="持仓数量,股票以'股'为单位, 债券以'张'为单位"
  )
  """ 持仓数量 """

  can_use_volume = Column(
    Integer, default=0, comment="可用数量, 股票以'股'为单位, 债券以'张'为单位"
  )
  """ 可用数量 """

  frozen_volume = Column(Integer, default=0, comment="冻结数量")
  """ 冻结数量 """

  on_road_volume = Column(Integer, default=0, comment="在途股份")
  """ 在途股份 """

  yesterday_volume = Column(Integer, default=0, comment="昨夜拥股")
  """ 昨夜拥股 """

  # 价格相关
  open_price = Column(Numeric(10, 4), comment="开仓价")
  """ 开仓价格 """

  avg_price = Column(Numeric(10, 4), comment="成本价")
  """ 平均成本价 """

  # 市值和盈亏
  market_value = Column(Numeric(15, 2), comment="市值")
  """ 当前市值 """

  # 方向
  direction = Column(Integer, comment="多空, 股票不需要")
  """ 持仓方向 """

  last_price = Column(Numeric(10, 4), nullable=True, comment="券商持仓快照最新价")
  """ 券商持仓快照携带的最新价格 """

  def to_dict(self):
    """转换为字典格式"""
    return {
      "id": self.id,
      "account_id": self.account_id,
      "instrument_name": self.instrument_name,
      "volume": self.volume,
      "can_use_volume": self.can_use_volume,
      "frozen_volume": self.frozen_volume,
      "on_road_volume": self.on_road_volume,
      "yesterday_volume": self.yesterday_volume,
      "open_price": float(self.open_price) if self.open_price else None,
      "avg_price": float(self.avg_price) if self.avg_price else None,
      "market_value": float(self.market_value) if self.market_value else None,
      "last_price": float(self.last_price) if self.last_price else None,
      "direction": self.direction,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }

  def __repr__(self):
    return f"<Position(account_id='{self.account_id}', stock_code='{self.stock_code}', volume={self.volume})>"

  @staticmethod
  def _sanitize_price_value(value) -> Optional[float]:
    """清理价格字段，过滤无限值和NaN

    Args:
        value: 待验证的价格值

    Returns:
        有效的浮点数值或None
    """
    if value is None:
      return None
    try:
      float_value = float(value)
      # 检查是否为有限数值（过滤 inf, -inf, nan）
      if not math.isfinite(float_value):
        return None
      return float_value
    except (TypeError, ValueError):
      return None

  @staticmethod
  def from_xtquant(position: Any, instrument_name: str = None) -> "Position":
    return Position.from_dict(
      {
        "account_id": position.account_id,
        "account_type": position.account_type,
        "stock_code": position.stock_code,
        "instrument_name": instrument_name
        if instrument_name
        else position.instrument_name,
        "volume": position.volume,
        "can_use_volume": position.can_use_volume,
        "open_price": position.open_price,
        "market_value": position.market_value,
        "frozen_volume": position.frozen_volume,
        "on_road_volume": position.on_road_volume,
        "yesterday_volume": position.yesterday_volume,
        "avg_price": position.avg_price,
        "direction": position.direction,
        "last_price": position.last_price,
        "profit_rate": position.profit_rate,
        "secu_account": position.secu_account,
      }
    )

  @staticmethod
  def from_dict(data: Dict[str, Any]) -> "Position":
    """从字典创建Position实例"""

    # 先转换 account_type 为枚举，确保一致性
    account_type = (
      AccountType.from_int(data.get("account_type"))
      if data.get("account_type")
      else None
    )

    # 使用 account_id + stock_code 生成唯一 id
    # 注意：不包含 account_type，因为一个账户对同一股票只能有一条持仓记录
    id = md5(
      f"{data['account_id']}:{data['stock_code']}".encode("utf-8")
    ).hexdigest()

    return Position(
      id=id,
      account_id=data.get("account_id"),
      account_type=account_type,
      stock_code=data.get("stock_code"),
      instrument_name=data.get("instrument_name"),
      volume=data.get("volume"),
      can_use_volume=data.get("can_use_volume"),
      open_price=Position._sanitize_price_value(data.get("open_price")),
      market_value=Position._sanitize_price_value(data.get("market_value")),
      last_price=Position._sanitize_price_value(data.get("last_price")),
      frozen_volume=data.get("frozen_volume"),
      on_road_volume=data.get("on_road_volume"),
      yesterday_volume=data.get("yesterday_volume"),
      avg_price=Position._sanitize_price_value(data.get("avg_price")),
      direction=data.get("direction"),
    )
