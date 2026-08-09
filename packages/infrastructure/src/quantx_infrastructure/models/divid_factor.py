"""
复权因子数据模型（PostgreSQL）
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, BigInteger, Column, DateTime, Index, String
from sqlalchemy.sql import func

from quantx_infrastructure.database.relational import Base


@dataclass
class DividFactor:
    """复权因子数据模型"""

    id: int = None
    stock_code: str = ""
    time: datetime = None
    ex_date: str = ""
    interest: Decimal = Decimal("0")
    stock_bonus: Decimal = Decimal("0")
    stock_gift: Decimal = Decimal("0")
    allot_num: Decimal = Decimal("0")
    allot_price: Decimal = Decimal("0")
    gugai: Decimal = Decimal("0")
    dr: Decimal = Decimal("1.0")
    created_at: datetime = None
    updated_at: datetime = None

    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'stock_code': self.stock_code,
            'time': self.time,
            'ex_date': self.ex_date,
            'interest': float(self.interest) if self.interest else 0,
            'stock_bonus': float(self.stock_bonus) if self.stock_bonus else 0,
            'stock_gift': float(self.stock_gift) if self.stock_gift else 0,
            'allot_num': float(self.allot_num) if self.allot_num else 0,
            'allot_price': float(self.allot_price) if self.allot_price else 0,
            'gugai': float(self.gugai) if self.gugai else 0,
            'dr': float(self.dr) if self.dr else 1.0,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


# SQLAlchemy ORM 表定义
class DividFactorTable(Base):
    """复权因子表"""

    __tablename__ = 'divid_factors'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, index=True)
    time = Column(DateTime, nullable=False, index=True)
    ex_date = Column(String(20), nullable=False)

    # 除权除息信息
    interest = Column(DECIMAL(12, 4), default=0)        # 现金分红
    stock_bonus = Column(DECIMAL(12, 4), default=0)     # 送股
    stock_gift = Column(DECIMAL(12, 4), default=0)      # 转增
    allot_num = Column(DECIMAL(12, 4), default=0)       # 配股数量
    allot_price = Column(DECIMAL(12, 4), default=0)     # 配股价格
    gugai = Column(DECIMAL(12, 4), default=0)          # 股改对价
    dr = Column(DECIMAL(12, 6), nullable=False)        # 复权因子

    # 时间戳
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # 索引
    __table_args__ = (
        Index('idx_divid_factors_stock_time', 'stock_code', 'time'),
        {"comment": "证券除权除息与复权因子"},
    )
