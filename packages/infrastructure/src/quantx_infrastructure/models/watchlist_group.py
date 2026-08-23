"""Database model for account-scoped watchlist groups."""

from __future__ import annotations

from hashlib import md5
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, Index, Integer, String, func
from sqlalchemy.orm import relationship

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class WatchlistGroup(Base, TimestampMixin):
  """A named, account-scoped collection of watchlist items.

  The functional unique index deliberately normalizes only for uniqueness.  The
  original casing entered by the user remains the display name.
  """

  __tablename__ = "watchlist_groups"

  id = Column(String(32), primary_key=True, index=True, comment="主键")
  account_id = Column(String(50), nullable=False, comment="资金账号")
  name = Column(String(80), nullable=False, comment="分组名称")
  display_order = Column(Integer, nullable=False, default=0, comment="展示排序")
  __table_args__ = (
    Index(
      "uq_watchlist_group_account_name_ci",
      account_id,
      func.lower(name),
      unique=True,
    ),
    Index("ix_watchlist_group_account_order", account_id, display_order),
  )

  memberships = relationship(
    "WatchlistGroupMembership",
    back_populates="group",
    cascade="all, delete-orphan",
    passive_deletes=True,
    order_by="WatchlistGroupMembership.display_order",
    lazy="selectin",
  )
  items = relationship(
    "WatchlistItem",
    secondary="watchlist_group_memberships",
    back_populates="groups",
    viewonly=True,
    lazy="selectin",
  )

  @staticmethod
  def make_id(account_id: str, name: str) -> str:
    # IDs are generated once on create; this helper is deterministic for
    # migration/backfill and tests, while the service uses a random ID to keep
    # a rename from changing the group's identity.
    return md5(f"{account_id}:{name.strip().lower()}".encode("utf-8")).hexdigest()

  @classmethod
  def create(
    cls,
    *,
    account_id: str,
    name: str,
    display_order: int = 0,
    id: Optional[str] = None,
  ) -> "WatchlistGroup":
    normalized_name = name.strip()
    return cls(
      id=id or md5(f"{account_id}:{normalized_name}:{uuid4()}".encode("utf-8")).hexdigest(),
      account_id=account_id,
      name=normalized_name,
      display_order=max(0, int(display_order or 0)),
    )
