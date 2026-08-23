"""Resolvers for the account-scoped watchlist aggregate."""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from quantx_infrastructure.services.engine_command_service import (
  engine_command_service,
)
from quantx_infrastructure.services.watchlist_service import WatchlistService

from ..types.watchlist_types import (
  WatchlistGroup,
  WatchlistItem,
  WatchlistMutationResult,
)

logger = logging.getLogger(__name__)


class WatchlistResolver:
  @staticmethod
  def _unexpected_mutation_error(operation: str) -> WatchlistMutationResult:
    logger.exception("自选操作失败: operation=%s", operation)
    return WatchlistMutationResult(success=False, message="自选操作失败")

  @staticmethod
  async def _notify_engine(account_id: Optional[str]) -> None:
    """Durably wake the Engine after a main-watchlist change."""
    aggregate_id = str(account_id or "default")
    try:
      await engine_command_service.enqueue(
        "WARM_CACHE_REFRESH_SOURCES",
        {"account_id": account_id},
        aggregate_id=aggregate_id,
        idempotency_key=f"warm-cache-refresh:{aggregate_id}:{uuid.uuid4()}",
      )
    except Exception as exc:
      logger.warning(
        "自选股已持久化，但 Engine 热缓存唤醒失败: account=%s error=%s",
        aggregate_id,
        exc.__class__.__name__,
      )

  @staticmethod
  async def get_watchlist(account_id: Optional[str] = None) -> List[WatchlistItem]:
    items = await WatchlistService().get_watchlist(account_id)
    return [WatchlistItem.from_model(item) for item in items]

  @staticmethod
  async def get_watchlist_groups(
    account_id: Optional[str] = None,
  ) -> List[WatchlistGroup]:
    groups = await WatchlistService().get_groups(account_id)
    return [WatchlistGroup.from_model(group) for group in groups]

  @staticmethod
  async def save_watchlist_item(
    *,
    account_id: str,
    stock_code: str,
    group_ids: List[str],
    instrument_name: Optional[str] = None,
    note: Optional[str] = None,
  ) -> WatchlistMutationResult:
    try:
      item = await WatchlistService().save_item(
        account_id=account_id,
        stock_code=stock_code,
        group_ids=group_ids,
        instrument_name=instrument_name,
        note=note,
      )
      await WatchlistResolver._notify_engine(account_id)
      return WatchlistMutationResult(
        success=True,
        message="自选股已保存",
        item=WatchlistItem.from_model(item),
      )
    except ValueError as exc:
      return WatchlistMutationResult(success=False, message=str(exc))
    except Exception:
      return WatchlistResolver._unexpected_mutation_error("save_watchlist_item")

  @staticmethod
  async def remove_watchlist_item(
    stock_code: str, account_id: Optional[str] = None
  ) -> WatchlistMutationResult:
    try:
      removed = await WatchlistService().remove_item(stock_code, account_id)
      if removed:
        await WatchlistResolver._notify_engine(account_id)
      return WatchlistMutationResult(
        success=removed,
        message="自选股已删除" if removed else "自选股不存在",
      )
    except ValueError as exc:
      return WatchlistMutationResult(success=False, message=str(exc))
    except Exception:
      return WatchlistResolver._unexpected_mutation_error("remove_watchlist_item")

  @staticmethod
  async def create_watchlist_group(
    *,
    account_id: str,
    name: str,
    initial_stock_codes: List[str],
  ) -> WatchlistMutationResult:
    try:
      group = await WatchlistService().create_group(
        account_id=account_id,
        name=name,
        initial_stock_codes=initial_stock_codes,
      )
      if initial_stock_codes:
        await WatchlistResolver._notify_engine(account_id)
      return WatchlistMutationResult(
        success=True,
        message="自选股分组已创建",
        group=WatchlistGroup.from_model(group),
      )
    except ValueError as exc:
      return WatchlistMutationResult(success=False, message=str(exc))
    except Exception:
      return WatchlistResolver._unexpected_mutation_error("create_watchlist_group")

  @staticmethod
  async def rename_watchlist_group(
    *, account_id: str, group_id: str, name: str
  ) -> WatchlistMutationResult:
    try:
      group = await WatchlistService().rename_group(
        account_id=account_id, group_id=group_id, name=name
      )
      return WatchlistMutationResult(
        success=True,
        message="自选股分组已重命名",
        group=WatchlistGroup.from_model(group),
      )
    except ValueError as exc:
      return WatchlistMutationResult(success=False, message=str(exc))
    except Exception:
      return WatchlistResolver._unexpected_mutation_error("rename_watchlist_group")

  @staticmethod
  async def delete_watchlist_group(
    *, account_id: str, group_id: str
  ) -> WatchlistMutationResult:
    try:
      deleted = await WatchlistService().delete_group(
        account_id=account_id, group_id=group_id
      )
      return WatchlistMutationResult(
        success=deleted,
        message="自选股分组已删除" if deleted else "自选股分组不存在",
      )
    except ValueError as exc:
      return WatchlistMutationResult(success=False, message=str(exc))
    except Exception:
      return WatchlistResolver._unexpected_mutation_error("delete_watchlist_group")

  @staticmethod
  async def reorder_watchlist_items(
    *, account_id: str, item_ids: List[str]
  ) -> WatchlistMutationResult:
    try:
      items = await WatchlistService().reorder_items(
        account_id=account_id, item_ids=item_ids
      )
      return WatchlistMutationResult(
        success=True,
        message="自选股排序已更新",
        items=[WatchlistItem.from_model(item) for item in items],
      )
    except ValueError as exc:
      return WatchlistMutationResult(success=False, message=str(exc))
    except Exception:
      return WatchlistResolver._unexpected_mutation_error("reorder_watchlist_items")

  @staticmethod
  async def reorder_watchlist_groups(
    *, account_id: str, group_ids: List[str]
  ) -> WatchlistMutationResult:
    try:
      groups = await WatchlistService().reorder_groups(
        account_id=account_id, group_ids=group_ids
      )
      return WatchlistMutationResult(
        success=True,
        message="自选股分组排序已更新",
        groups=[WatchlistGroup.from_model(group) for group in groups],
      )
    except ValueError as exc:
      return WatchlistMutationResult(success=False, message=str(exc))
    except Exception:
      return WatchlistResolver._unexpected_mutation_error("reorder_watchlist_groups")

  @staticmethod
  async def reorder_watchlist_group_items(
    *, account_id: str, group_id: str, item_ids: List[str]
  ) -> WatchlistMutationResult:
    try:
      group = await WatchlistService().reorder_group_items(
        account_id=account_id, group_id=group_id, item_ids=item_ids
      )
      return WatchlistMutationResult(
        success=True,
        message="分组内自选股排序已更新",
        group=WatchlistGroup.from_model(group),
      )
    except ValueError as exc:
      return WatchlistMutationResult(success=False, message=str(exc))
    except Exception:
      return WatchlistResolver._unexpected_mutation_error(
        "reorder_watchlist_group_items"
      )
