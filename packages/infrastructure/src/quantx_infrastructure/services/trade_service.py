"""Database-backed execution queries and report persistence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.database.relational_base import BulkSaveResult
from quantx_infrastructure.models import Trade
from quantx_infrastructure.models.enums import OrderType
from quantx_infrastructure.repositories.trade_repository import TradeRepository


class TradeService:
  def __init__(self, account_id: Optional[str] = None):
    self.account_id = (account_id or "").strip()

  async def get_today_trades(self, account_id: str) -> List[Trade]:
    return await self.get_trades_by_date(
      time_utils.now().date().isoformat(),
      account_id,
    )

  async def upsert_report(self, report: dict[str, Any]) -> Trade:
    trade = Trade.from_dict(report)
    async for db in get_async_db():
      return await TradeRepository(db).save(trade)
    raise RuntimeError("成交数据库不可用")

  async def get_history_trades(
    self,
    account_id: str,
    start_date: str,
    end_date: str,
  ) -> List[Trade]:
    async for db in get_async_db():
      return await TradeRepository(db).find_all_by_date_range(
        start_date,
        end_date,
        account_id,
      )
    return []

  async def get_trades_by_order_id(self, order_id: int) -> List[Trade]:
    async for db in get_async_db():
      return await TradeRepository(db).find_all_by_order_id(order_id)
    return []

  async def get_trade_by_id(self, trade_id: str) -> Optional[Trade]:
    async for db in get_async_db():
      trade = await TradeRepository(db).find_by_trade_id(trade_id)
      if trade is not None and self.account_id:
        if trade.account_id != self.account_id:
          return None
      return trade
    return None

  async def save_trades(self, trades: List[Trade]) -> BulkSaveResult:
    async for db in get_async_db():
      return await TradeRepository(db).bulk_save(trades)
    return BulkSaveResult(
      saved_entities=[],
      saved_count=0,
      inserted_count=0,
      updated_count=0,
      deleted_count=0,
    )

  async def get_trades_by_date(
    self,
    trade_date: str,
    account_id: Optional[str] = None,
  ) -> List[Trade]:
    async for db in get_async_db():
      return await TradeRepository(db).find_all_by_date(trade_date, account_id)
    return []

  async def get_trades_by_account(
    self,
    account_id: str,
    limit: int = 100,
  ) -> List[Trade]:
    async for db in get_async_db():
      return await TradeRepository(db).find_all_by_account(
        account_id,
        limit=limit,
      )
    return []

  async def get_trade_summary(
    self,
    account_id: str,
    trade_date: Optional[str] = None,
  ) -> Dict[str, Any]:
    date_value = trade_date or time_utils.now().date().isoformat()
    trades = await self.get_trades_by_date(date_value, account_id)
    buy_amount = sum(
      float(trade.amount)
      for trade in trades
      if int(trade.order_type) == int(OrderType.BUY)
    )
    sell_amount = sum(
      float(trade.amount)
      for trade in trades
      if int(trade.order_type) == int(OrderType.SELL)
    )
    return {
      "trade_date": date_value,
      "account_id": account_id,
      "total_trades": len(trades),
      "buy_count": sum(
        int(int(trade.order_type) == int(OrderType.BUY)) for trade in trades
      ),
      "sell_count": sum(
        int(int(trade.order_type) == int(OrderType.SELL)) for trade in trades
      ),
      "buy_amount": buy_amount,
      "sell_amount": sell_amount,
      "net_amount": buy_amount - sell_amount,
      "trades": [trade.to_dict() for trade in trades],
    }
