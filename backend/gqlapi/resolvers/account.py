import asyncio
from datetime import datetime
from typing import Optional

from miniqmt.manager_registry import XTTradingManagerRegistry
from database.connection import get_async_db
from repositories.account_repository import AccountRepository

from ..types import Account
from core.utils import time_utils

registry = XTTradingManagerRegistry()


class AccountResolver:
  ACCOUNT_QUERY_TIMEOUT_SECONDS = 5.0

  @staticmethod
  def _optional_rounded(value: object, digits: int = 2) -> Optional[float]:
    if value is None:
      return None
    try:
      return round(float(value), digits)
    except (TypeError, ValueError):
      return None

  @staticmethod
  def get_account(account_id: str) -> Optional[Account]:
    """获取账户信息"""
    try:
      # 使用 XTTradingManagerRegistry 获取或创建 trading manager
      trading_manager = registry.get_manager(account_id, reconnect=False)

      if trading_manager:
        account_info = trading_manager.get_account_info()

        if account_info:
          profit_loss_ratio = AccountResolver._optional_rounded(
            account_info.get("profit_loss_ratio"), 8
          )
          return Account(
            id=account_info.get("account_id", account_id),
            account_type="",
            account_name=f"账户{account_id}",
            total_asset=round(account_info.get("total_asset", 0), 2),
            cash=round(account_info.get("cash", 0), 2),
            frozen_cash=round(account_info.get("frozen_cash", 0), 2),
            market_value=round(account_info.get("market_value", 0), 2),
            total_profit_loss=AccountResolver._optional_rounded(
              account_info.get("profit_loss")
            ),
            profit_loss_percent=(
              round(profit_loss_ratio * 100, 2)
              if profit_loss_ratio is not None
              else None
            ),
            create_time=time_utils.now(),  # XTQuant 可能不提供创建时间
            update_time=time_utils.now(),
          )
      return None
    except Exception as e:
      print(f"XTQuant API error: {e}")
      return None

  @staticmethod
  def get_current_account() -> Optional[Account]:
    """Return an already registered account, if one exists."""
    account_ids = list(getattr(registry, "_managers", {}).keys())
    if not account_ids:
      return None
    return AccountResolver.get_account(account_ids[0])

  @staticmethod
  async def get_account_async(account_id: str) -> Optional[Account]:
    """在线程中获取账户信息，避免阻塞 GraphQL 事件循环。"""
    try:
      return await asyncio.wait_for(
        asyncio.to_thread(AccountResolver.get_account, account_id),
        timeout=AccountResolver.ACCOUNT_QUERY_TIMEOUT_SECONDS,
      )
    except asyncio.TimeoutError:
      print(f"XTQuant account query timeout: {account_id}")
      return None

  @staticmethod
  async def get_current_account_async() -> Optional[Account]:
    """Resolve the persisted default account, then refresh it from miniQMT."""
    account_model = None
    async for db in get_async_db():
      account_model = await AccountRepository(db).find_default()
      break
    if account_model is None:
      account_ids = list(getattr(registry, "_managers", {}).keys())
      if not account_ids:
        return None
      return await AccountResolver.get_account_async(account_ids[0])

    live_account = await AccountResolver.get_account_async(account_model.account_id)
    if live_account is not None:
      return live_account
    return Account(
      id=account_model.account_id,
      account_name=f"账户{account_model.account_id}",
      account_type=account_model.account_type.value,
      total_asset=float(account_model.total_asset or 0),
      cash=float(account_model.cash or 0),
      frozen_cash=float(account_model.frozen_cash or 0),
      market_value=float(account_model.market_value or 0),
      total_profit_loss=None,
      profit_loss_percent=None,
      create_time=account_model.created_at,
      update_time=account_model.updated_at,
    )
