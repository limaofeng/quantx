"""
Database model for account watchlist items.
"""

from hashlib import md5
from typing import Optional

from sqlalchemy import Column, Index, Integer, String, UniqueConstraint

from database.relational_base import Base, TimestampMixin


class WatchlistItem(Base, TimestampMixin):
  """Account-scoped watchlist entry."""

  __tablename__ = "watchlist_items"
  __table_args__ = (
    UniqueConstraint("account_id", "stock_code", name="uq_watchlist_account_stock"),
    Index("ix_watchlist_account_order", "account_id", "display_order"),
  )

  id = Column(String(32), primary_key=True, index=True, comment="主键")
  account_id = Column(String(50), nullable=False, comment="资金账号")
  stock_code = Column(String(20), nullable=False, comment="证券代码")
  instrument_name = Column(String(80), nullable=True, comment="证券名称")
  display_order = Column(Integer, nullable=False, default=0, comment="展示排序")
  group_name = Column(String(80), nullable=True, comment="分组")
  note = Column(String(300), nullable=True, comment="备注")

  @staticmethod
  def make_id(account_id: str, stock_code: str) -> str:
    return md5(f"{account_id}:{stock_code.upper()}".encode("utf-8")).hexdigest()

  @classmethod
  def create(
    cls,
    *,
    account_id: str,
    stock_code: str,
    instrument_name: Optional[str] = None,
    display_order: int = 0,
    group_name: Optional[str] = None,
    note: Optional[str] = None,
  ) -> "WatchlistItem":
    normalized_code = stock_code.strip().upper()
    return cls(
      id=cls.make_id(account_id, normalized_code),
      account_id=account_id,
      stock_code=normalized_code,
      instrument_name=instrument_name,
      display_order=max(0, int(display_order or 0)),
      group_name=group_name,
      note=note,
    )
