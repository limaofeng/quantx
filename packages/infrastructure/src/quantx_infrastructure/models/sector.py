from typing import List, Optional

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from quantx_infrastructure.database.relational_base import Base


class Sector(Base):
  __tablename__ = "sectors"

  id = Column(Integer, primary_key=True, autoincrement=True, comment="板块ID")
  name = Column(String(64), nullable=False, comment="板块名称")  # 板块名称
  code = Column(String(64), unique=True, nullable=False, comment="板块代码")  # 板块代码
  description = Column(String(256), nullable=True, comment="板块描述")  # 板块描述

  # 用于区分板块分类（例如：SW、TGN、DY 等）
  classification = Column(
    String(32), nullable=False, default="SW", comment="板块分类，如 SW、TGN、DY 等"
  )
  # 市场字段（例如 SSE、SZSE、HK）
  market = Column(String(32), nullable=True, comment="交易市场，例如 SSE、SZSE、HK")

  # 层级支持
  level = Column(Integer, nullable=False, default=1, comment="板块层级 (1, 2, ...)")
  parent_id = Column(
    Integer,
    ForeignKey("sectors.id", ondelete="SET NULL"),
    nullable=True,
    comment="父板块ID",
  )
  parent = relationship(
    "Sector", remote_side=[id], back_populates="children", uselist=False
  )
  children = relationship(
    "Sector",
    back_populates="parent",
    cascade="all, delete-orphan",
    passive_deletes=True,
  )

  # 关联到成分股（不依赖 Instrument 对象）
  sector_stocks = relationship(
    "SectorStock", back_populates="sector", cascade="all, delete-orphan"
  )

  @property
  def stock_codes(self) -> List[str]:
    """获取成分股代码列表"""
    return [ss.stock_code for ss in self.sector_stocks]

  def add_stock(self, stock_code: str):
    """添加成分股"""
    from quantx_infrastructure.models.sector_stock import SectorStock

    if stock_code not in self.stock_codes:
      sector_stock = SectorStock(stock_code=stock_code)
      self.sector_stocks.append(sector_stock)

  def remove_stock(self, stock_code: str):
    """移除成分股"""
    self.sector_stocks = [
      ss for ss in self.sector_stocks if ss.stock_code != stock_code
    ]

  def set_stocks(self, stock_codes: list):
    """设置成分股列表（替换所有现有成分股）"""
    from quantx_infrastructure.models.sector_stock import SectorStock

    # 清空现有成分股
    self.sector_stocks.clear()
    # 添加新的成分股
    for stock_code in stock_codes:
      sector_stock = SectorStock(stock_code=stock_code)
      self.sector_stocks.append(sector_stock)

  # 层级相关辅助方法
  def add_child(self, child: "Sector"):
    """把一个 Sector 实例作为子节点加入（并设置 parent）"""
    if child not in self.children:
      child.parent = self
      self.children.append(child)

  def remove_child(self, child: "Sector"):
    """移除指定子节点（通过对象）"""
    self.children = [c for c in self.children if c is not child]
    child.parent = None

  def remove_child_by_id(self, child_id: int):
    """通过 id 移除子节点"""
    self.children = [c for c in self.children if c.id != child_id]

  def is_root(self) -> bool:
    return self.parent_id is None

  def get_ancestors(self) -> List["Sector"]:
    """返回从父到根的祖先链（近 -> 远）"""
    ancestors: List[Sector] = []
    node = self.parent
    while node is not None:
      ancestors.append(node)
      node = node.parent
    return ancestors

  def get_descendants(self, _acc: Optional[List["Sector"]] = None) -> List["Sector"]:
    """递归返回所有后代节点（深度优先）"""
    if _acc is None:
      _acc = []
    for c in self.children:
      _acc.append(c)
      c.get_descendants(_acc)
    return _acc

  def __repr__(self):
    return (
      f"<Sector(id={self.id}, name='{self.name}', code='{self.code}', "
      f"classification='{self.classification}', market='{self.market}', parent_id={self.parent_id})>"
    )
