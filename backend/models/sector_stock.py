"""
板块成分股关联表
只存储板块ID和股票代码的简单关联，不依赖 Instrument 对象
"""

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from database.relational_base import Base


class SectorStock(Base):
  """板块成分股关联表"""

  __tablename__ = "sector_stocks"

  id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
  sector_id = Column(
    Integer, ForeignKey("sectors.id"), nullable=False, comment="板块ID"
  )
  stock_code = Column(String(32), nullable=False, comment="股票代码")

  # 确保同一板块内不会有重复的股票代码
  __table_args__ = (
    UniqueConstraint("sector_id", "stock_code", name="uix_sector_stock"),
  )

  # 关联到板块
  sector = relationship("Sector", back_populates="sector_stocks")

  def __repr__(self):
    return f"<SectorStock(sector_id={self.sector_id}, stock_code='{self.stock_code}')>"
