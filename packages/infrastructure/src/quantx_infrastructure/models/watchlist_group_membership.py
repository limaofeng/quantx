"""Association model for watchlist groups and the main watchlist."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class WatchlistGroupMembership(Base, TimestampMixin):
  """An ordered membership of one watchlist item in one group."""

  __tablename__ = "watchlist_group_memberships"
  __table_args__ = (
    Index("ix_watchlist_group_membership_group_order", "group_id", "display_order"),
    Index("ix_watchlist_group_membership_item", "watchlist_item_id"),
  )

  group_id = Column(
    String(32),
    ForeignKey("watchlist_groups.id", ondelete="CASCADE"),
    primary_key=True,
    comment="分组ID",
  )
  watchlist_item_id = Column(
    String(32),
    ForeignKey("watchlist_items.id", ondelete="CASCADE"),
    primary_key=True,
    comment="自选记录ID",
  )
  display_order = Column(Integer, nullable=False, default=0, comment="组内展示排序")

  group = relationship("WatchlistGroup", back_populates="memberships")
  watchlist_item = relationship("WatchlistItem", back_populates="group_memberships")

  @classmethod
  def create(
    cls, *, group_id: str, watchlist_item_id: str, display_order: int = 0
  ) -> "WatchlistGroupMembership":
    return cls(
      group_id=group_id,
      watchlist_item_id=watchlist_item_id,
      display_order=max(0, int(display_order or 0)),
    )
