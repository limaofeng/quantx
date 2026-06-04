from typing import List, Optional

from core.data.intraday_warm_cache import intraday_warm_cache
from services.watchlist_service import WatchlistService

from ..types.watchlist_types import WatchlistItem, WatchlistMutationResult


class WatchlistResolver:
  @staticmethod
  async def get_watchlist(account_id: Optional[str] = None) -> List[WatchlistItem]:
    service = WatchlistService()
    items = await service.get_watchlist(account_id)
    return [WatchlistItem.from_model(item) for item in items]

  @staticmethod
  async def add_watchlist_item(
    *,
    stock_code: str,
    account_id: Optional[str] = None,
    instrument_name: Optional[str] = None,
    display_order: Optional[int] = None,
    group_name: Optional[str] = None,
    note: Optional[str] = None,
  ) -> WatchlistMutationResult:
    try:
      service = WatchlistService()
      item = await service.add_item(
        account_id=account_id,
        stock_code=stock_code,
        instrument_name=instrument_name,
        display_order=display_order,
        group_name=group_name,
        note=note,
      )
      await intraday_warm_cache.ensure_symbol(item.stock_code, source="watchlist")
      return WatchlistMutationResult(
        success=True,
        message="自选股已保存",
        item=WatchlistItem.from_model(item),
      )
    except Exception as exc:
      return WatchlistMutationResult(success=False, message=str(exc))

  @staticmethod
  async def remove_watchlist_item(
    stock_code: str, account_id: Optional[str] = None
  ) -> WatchlistMutationResult:
    service = WatchlistService()
    removed = await service.remove_item(stock_code, account_id)
    if removed:
      await intraday_warm_cache.refresh_source_symbols()
    return WatchlistMutationResult(
      success=removed,
      message="自选股已删除" if removed else "自选股不存在",
    )

  @staticmethod
  async def replace_watchlist(
    symbols: List[str], account_id: Optional[str] = None
  ) -> WatchlistMutationResult:
    try:
      service = WatchlistService()
      items = await service.replace_watchlist(symbols, account_id)
      await intraday_warm_cache.replace_source_symbols(
        "watchlist",
        [item.stock_code for item in items],
      )
      return WatchlistMutationResult(
        success=True,
        message="自选股列表已替换",
        items=[WatchlistItem.from_model(item) for item in items],
      )
    except Exception as exc:
      return WatchlistMutationResult(success=False, message=str(exc))

  @staticmethod
  async def reorder_watchlist(
    symbols: List[str], account_id: Optional[str] = None
  ) -> WatchlistMutationResult:
    try:
      service = WatchlistService()
      items = await service.reorder_watchlist(symbols, account_id)
      return WatchlistMutationResult(
        success=True,
        message="自选股排序已更新",
        items=[WatchlistItem.from_model(item) for item in items],
      )
    except Exception as exc:
      return WatchlistMutationResult(success=False, message=str(exc))
