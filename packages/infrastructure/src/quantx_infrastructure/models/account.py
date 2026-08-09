"""
数据库模型 - 账户相关数据模型
"""

from sqlalchemy import Column, Enum, Numeric, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin
from quantx_infrastructure.models.enums import AccountType


class Account(Base, TimestampMixin):
  """账户表"""

  __tablename__ = "accounts"

  # 复合主键：md5(资金账号 + 账户类型)
  id = Column(String(32), primary_key=True, index=True, comment="主键")

  # 账户基本信息
  account_id = Column(String(50), nullable=False, comment="资金账号")
  account_type = Column(
    Enum(AccountType, name="account_type"), nullable=False, comment="账户类型"
  )

  # 资金信息
  total_asset = Column(Numeric(15, 2), default=0.00, comment="总资产")
  cash = Column(Numeric(15, 2), default=0.00, comment="可用资金")
  market_value = Column(Numeric(15, 2), default=0.00, comment="冻结资金")
  frozen_cash = Column(Numeric(15, 2), default=0.00, comment="冻结资金")

  def to_dict(self):
    """转换为字典格式"""
    return {
      "id": self.id,
      "account_id": self.account_id,
      "account_type": self.account_type,
      "total_asset": float(self.total_asset) if self.total_asset else 0.00,
      "cash": float(self.cash) if self.cash else 0.00,
      "market_value": float(self.market_value) if self.market_value else 0.00,
      "frozen_cash": float(self.frozen_cash) if self.frozen_cash else 0.00,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }

  def __repr__(self):
    return f"<Account(id='{self.id}', account_id='{self.account_id}')>"
