import logging
import uuid
from typing import List, Optional

from quantx_infrastructure.services.engine_command_service import (
  engine_command_service,
)
from quantx_infrastructure.services.watchlist_service import WatchlistService

from ..types.watchlist_types import WatchlistItem, WatchlistMutationResult

logger = logging.getLogger(__name__)


class WatchlistResolver:
  @staticmethod
  async def _notify_engine(account_id: Optional[str]) -> None:
    """Durably wake the Engine; its periodic DB scan remains the recovery path."""
    aggregate_id = str(account_id or "default")
    try:
      await engine_command_service.enqueue(
        "WARM_CACHE_REFRESH_SOURCES",
        {"account_id": account_id},
        aggregate_id=aggregate_id,
        idempotency_key=(
          f"warm-cache-refresh:{aggregate_id}:{uuid.uuid4()}"
        ),
      )
    except Exception as exc:
      logger.warning(
        "自选股已持久化，但 Engine 热缓存唤醒失败: account=%s error=%s",
        aggregate_id,
        exc.__class__.__name__,
      )

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
      await WatchlistResolver._notify_engine(account_id)
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
      await WatchlistResolver._notify_engine(account_id)
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
      await WatchlistResolver._notify_engine(account_id)
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
