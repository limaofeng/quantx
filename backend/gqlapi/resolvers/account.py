from datetime import datetime
from typing import Optional

from miniqmt.manager_registry import XTTradingManagerRegistry

from ..types import Account
from core.utils import time_utils

registry = XTTradingManagerRegistry()


class AccountResolver:
  @staticmethod
  def get_account(account_id: str) -> Optional[Account]:
    """获取账户信息"""
    try:
      # 使用 XTTradingManagerRegistry 获取或创建 trading manager
      trading_manager = registry.get_manager(account_id)

      if trading_manager:
        account_info = trading_manager.get_account_info()

        if account_info:
          return Account(
            id=account_info.get("account_id", account_id),
            account_type="",
            account_name=f"账户{account_id}",
            total_asset=round(account_info.get("total_asset", 0), 2),
            cash=round(account_info.get("cash", 0), 2),
            frozen_cash=round(account_info.get("frozen_cash", 0), 2),
            market_value=round(account_info.get("market_value", 0), 2),
            total_profit_loss=round(account_info.get("profit_loss", 0), 2),
            profit_loss_percent=round(
              account_info.get("profit_loss_ratio", 0) * 100, 2
            ),
            create_time=time_utils.now(),  # XTQuant 可能不提供创建时间
            update_time=time_utils.now(),
          )
      return None
    except Exception as e:
      print(f"XTQuant API error: {e}, falling back to mock data")

  @staticmethod
  def get_current_account() -> Account:
    """获取当前账户信息（默认账户）"""
    # 默认使用第一个账户
    default_account_id = "300000013250"
    account = AccountResolver.get_account(default_account_id)
    if account is None:
      raise Exception("无法获取默认账户信息")
    return account
