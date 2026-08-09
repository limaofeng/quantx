"""
数据库模型 - 成交相关数据模型
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_base import Base, TimestampMixin
from quantx_infrastructure.models.enums import AccountType


class Trade(Base, TimestampMixin):
  """成交表"""

  __tablename__ = "trades"

  id = Column("traded_id", String(20), primary_key=True, index=True, comment="成交编号")
  """ 成交编号 """

  time = Column("traded_time", DateTime, nullable=False, comment="成交时间")
  """ 成交时间 """

  price = Column("traded_price", Float, nullable=False, comment="成交价格")
  """ 成交价格 """

  volume = Column("traded_volume", Integer, nullable=False, comment="成交数量")
  """ 成交数量 """

  amount = Column("traded_amount", Float, nullable=False, comment="成交金额")
  """ 成交金额 """

  # 资金账号信息
  account_id = Column(String(20), nullable=False, comment="资金账号")
  """ 资金账号 """

  account_type = Column(
    Enum(AccountType, name="account_type"), nullable=True, comment="账户类型"
  )
  """ 账户类型 """

  # 证券信息
  stock_code = Column(String(20), nullable=False, comment="证券代码, 例如'600000.SH'")
  """ 证券代码 """

  # 关联订单信息
  order_id = Column(
    Integer, ForeignKey("orders.order_id"), nullable=False, comment="委托编号"
  )
  """ 委托编号 """

  order_sysid = Column(String(10), nullable=False, comment="柜台编号")
  """ 柜台编号 """

  order_type = Column(Integer, nullable=False, comment="委托类型")
  """ 委托类型 """

  # 其他信息
  strategy_name = Column(String(50), nullable=True, comment="策略名称")
  """ 策略名称 """

  order_remark = Column(String(200), nullable=True, comment="委托备注")
  """ 委托备注 """

  direction = Column(Integer, nullable=True, comment="多空")
  """ 多空 """

  offset_flag = Column(Integer, nullable=True, comment="交易操作")
  """ 交易操作 """

  # 关联关系
  order = relationship("Order", back_populates="trades")

  def to_dict(self):
    """序列化为字典"""
    return {
      "id": self.id,
      "account_id": self.account_id,
      "account_type": self.account_type,
      "stock_code": self.stock_code,
      "order_id": self.order_id,
      "order_sysid": self.order_sysid,
      "order_type": self.order_type,
      "time": self.time.isoformat() if self.time else None,
      "price": self.price,
      "volume": self.volume,
      "amount": self.amount,
      "strategy_name": self.strategy_name,
      "order_remark": self.order_remark,
      "direction": self.direction,
      "offset_flag": self.offset_flag,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }

  @staticmethod
  def from_dict(data: dict) -> "Trade":
    traded_time = (
      time_utils.to_shanghai(
        datetime.fromtimestamp(data.get("traded_time"), timezone.utc)
      )
      if isinstance(data.get("traded_time"), (int, float))
      else data.get("traded_time")
    )

    """从字典创建 Trade 实例"""
    return Trade(
      id=data.get("traded_id"),
      account_id=data.get("account_id"),
      account_type=AccountType.from_int(data.get("account_type"))
      if data.get("account_type")
      else None,
      stock_code=data.get("stock_code"),
      order_id=data.get("order_id"),
      order_sysid=data.get("order_sysid"),
      order_type=data.get("order_type"),
      time=traded_time,
      price=data.get("traded_price"),
      volume=data.get("traded_volume"),
      amount=data.get("traded_amount"),
      strategy_name=data.get("strategy_name"),
      order_remark=data.get("order_remark"),
      direction=data.get("direction"),
      offset_flag=data.get("offset_flag"),
    )
