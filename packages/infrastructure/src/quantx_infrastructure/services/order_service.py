"""Database-backed order queries and report persistence."""

from __future__ import annotations

from typing import Any, List, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.database.relational_base import BulkSaveResult
from quantx_infrastructure.models import Order
from quantx_infrastructure.repositories.order_repository import OrderRepository


class OrderService:
  """The server reads orders from its database; QMT access belongs to qmt-agent."""

  def __init__(self, account_id: Optional[str] = None):
    self.account_id = (account_id or "").strip()

  async def get_today_orders(self, account_id: str) -> List[Order]:
    today = time_utils.now().date()
    return await self.get_history_orders(
      account_id,
      today.isoformat(),
      today.isoformat(),
    )

  async def sync_today_orders(self, account_id: str) -> BulkSaveResult:
    """Compatibility method: reports are already persisted through the inbox."""
    orders = await self.get_today_orders(account_id)
    return BulkSaveResult(orders, len(orders), 0, 0)

  async def upsert_report(self, report: dict[str, Any]) -> Order:
    """Persist a normalized order report emitted by qmt-agent."""
    order = Order.from_dict(report)
    result = await self.save_orders([order])
    return result.saved_entities[0]

  async def get_history_orders(
    self,
    account_id: str,
    start_date: str,
    end_date: str,
  ) -> List[Order]:
    async for db in get_async_db():
      return await OrderRepository(db).find_all_by_date_range(
        account_id,
        start_date,
        end_date,
      )
    return []

  async def get_order_by_id(self, order_id: int) -> Optional[Order]:
    async for db in get_async_db():
      order = await OrderRepository(db).find_by_id(order_id)
      if order is not None and self.account_id:
        if order.account_id != self.account_id:
          return None
      return order
    return None

  async def get_orders(
    self,
    user_id: str = "default",
    limit: int = 100,
  ) -> List[Order]:
    del user_id
    async for db in get_async_db():
      return await OrderRepository(db).find_all(0, limit)
    return []

  async def save_orders(self, orders: List[Order]) -> BulkSaveResult:
    async for db in get_async_db():
      return await OrderRepository(db).bulk_save(orders)
    return BulkSaveResult([], 0, 0, len(orders))
