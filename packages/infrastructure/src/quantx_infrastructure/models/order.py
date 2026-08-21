"""
数据库模型 - 订单相关数据模型
"""

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import Column, DateTime, Enum, Float, Index, Integer, String, and_
from sqlalchemy.orm import relationship

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_base import Base, TimestampMixin
from quantx_infrastructure.models.enums import (
  AccountType,
  OrderPriceType,
  OrderStatus,
  OrderType,
)


class Order(Base, TimestampMixin):
  """订单表"""

  __tablename__ = "orders"

  # id
  id = Column("order_id", Integer, primary_key=True, index=True, comment="主键")
  """ 委托编号 """

  account_id = Column(String(20), nullable=False, comment="资金账号")
  """ 资金账号 """

  account_type = Column(
    Enum(AccountType, name="account_type"), nullable=True, comment="账户类型"
  )
  """ 账户类型 """

  stock_code = Column(String(20), nullable=False, comment="证券代码, 例如'600000.SH'")
  """ 证券代码 """

  sysid = Column(
    "order_sysid", String(10), unique=True, nullable=False, comment="柜台编号"
  )
  """ 柜台编号 """

  time = Column("order_time", DateTime, nullable=False, comment="报单时间")
  """ 报单时间 """

  type = Column(
    "order_type",
    Enum(OrderType, name="order_type"),
    nullable=False,
    comment="委托类型, 23:买, 24:卖",
  )
  """ 委托类型 """

  volume = Column("order_volume", Integer, nullable=False, comment="委托数量")
  """ 委托数量 """

  price_type = Column(
    Enum(OrderPriceType, name="order_price_type"), nullable=False, comment="报价类型"
  )
  """ 报价类型 """

  price = Column(Float, nullable=False, comment="报价价格")
  """ 报价价格 """

  traded_volume = Column(Integer, nullable=False, comment="成交数量")
  """ 成交数量 """

  traded_price = Column(Float, nullable=False, comment="成交均价")
  """ 成交均价 """

  status = Column(
    "order_status",
    Enum(OrderStatus, name="order_status"),
    nullable=False,
    comment="委托状态",
  )
  """ 委托状态 """

  status_msg = Column(String(100), nullable=True, comment="委托状态描述")
  """ 委托状态描述 """

  strategy_name = Column(String(50), nullable=True, comment="策略名称")
  """ 策略名称 """

  remark = Column("order_remark", String(200), nullable=True, comment="委托备注")
  """ 委托备注 """

  direction = Column(Integer, nullable=True, comment="多空")
  """ 多空 """

  offset_flag = Column(Integer, nullable=True, comment="交易操作")
  """ 交易操作 """

  secu_account = Column(String(20), nullable=True, comment="股东代码")
  """ 股东代码 """

  instrument_name = Column(String(50), nullable=True, comment="证券名称")
  """ 证券名称 """

  trades = relationship("Trade", back_populates="order")

  def to_dict(self):
    """序列化为字典"""
    return {
      "id": self.id,
      "account_id": self.account_id,
      "stock_code": self.stock_code,
      "sysid": self.sysid,
      "time": self.time,
      "type": self.type,
      "volume": self.volume,
      "price_type": self.price_type,
      "price": self.price,
      "traded_volume": self.traded_volume,
      "traded_price": self.traded_price,
      "status": self.status,
      "status_msg": self.status_msg,
      "strategy_name": self.strategy_name,
      "remark": self.remark,
      "direction": self.direction,
      "offset_flag": self.offset_flag,
      "secu_account": self.secu_account,
      "instrument_name": self.instrument_name,
      "account_type": self.account_type,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }

  @staticmethod
  def from_dict(data: Dict[str, Any]) -> "Order":
    """从字典创建Order实例"""

    order_id = data.get("order_id")
    order_time = (
      time_utils.to_shanghai(
        datetime.fromtimestamp(data.get("order_time"), timezone.utc)
      )
      if isinstance(data.get("order_time"), (int, float))
      else data.get("order_time")
    )
    order_type = OrderType(data.get("order_type")) if data.get("order_type") else None
    order_status = (
      OrderStatus(data.get("order_status")) if data.get("order_status") else None
    )

    price_type = (
      OrderPriceType(data.get("price_type")) if data.get("price_type") else None
    )
    account_type = (
      AccountType.from_int(data.get("account_type"))
      if data.get("account_type")
      else None
    )

    return Order(
      id=order_id,
      account_id=data.get("account_id"),
      stock_code=data.get("stock_code"),
      sysid=data.get("order_sysid"),
      time=order_time,
      type=order_type,
      volume=data.get("order_volume"),
      price_type=price_type,
      price=data.get("price"),
      traded_volume=data.get("traded_volume"),
      traded_price=data.get("traded_price"),
      status=order_status,
      status_msg=data.get("status_msg"),
      strategy_name=data.get("strategy_name"),
      remark=data.get("order_remark"),
      direction=data.get("direction"),
      offset_flag=data.get("offset_flag"),
      secu_account=data.get("secu_account"),
      instrument_name=data.get("instrument_name"),
      account_type=account_type,
    )


Index(
  "ix_orders_exit_plan_cost_basis",
  Order.account_id,
  Order.stock_code,
  Order.type,
  Order.time.desc(),
  Order.id.desc(),
  postgresql_where=and_(Order.traded_volume > 0, Order.traded_price > 0),
)
