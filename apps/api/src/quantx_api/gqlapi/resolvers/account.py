"""Account resolver backed by server-side reconciled snapshots."""

from typing import Optional

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.repositories.account_repository import AccountRepository

from ..types import Account


class AccountResolver:
  @staticmethod
  def _optional_rounded(value: object, digits: int = 2) -> Optional[float]:
    if value is None:
      return None
    try:
      return round(float(value), digits)
    except (TypeError, ValueError):
      return None

  @staticmethod
  def _to_graphql(account_model) -> Account:
    total_asset = float(account_model.total_asset or 0)
    total_profit_loss = AccountResolver._optional_rounded(
      getattr(account_model, "total_profit_loss", None)
    )
    profit_loss_percent = (
      round(total_profit_loss / total_asset * 100, 2)
      if total_profit_loss is not None and total_asset
      else None
    )
    return Account(
      id=account_model.account_id,
      account_name=f"账户{account_model.account_id}",
      account_type=account_model.account_type.value,
      total_asset=total_asset,
      cash=float(account_model.cash or 0),
      frozen_cash=float(account_model.frozen_cash or 0),
      market_value=float(account_model.market_value or 0),
      total_profit_loss=total_profit_loss,
      profit_loss_percent=profit_loss_percent,
      create_time=account_model.created_at,
      update_time=account_model.updated_at,
    )

  @staticmethod
  def get_account(account_id: str) -> Optional[Account]:
    """Synchronous live QMT reads were removed; use get_account_async."""
    del account_id
    return None

  @staticmethod
  def get_current_account() -> Optional[Account]:
    """Synchronous live QMT reads were removed; use get_current_account_async."""
    return None

  @staticmethod
  async def get_account_async(account_id: str) -> Optional[Account]:
    async for db in get_async_db():
      account_model = await AccountRepository(db).find_by_account_id(account_id)
      return (
        AccountResolver._to_graphql(account_model)
        if account_model is not None
        else None
      )
    return None

  @staticmethod
  async def get_current_account_async() -> Optional[Account]:
    async for db in get_async_db():
      account_model = await AccountRepository(db).find_default()
      return (
        AccountResolver._to_graphql(account_model)
        if account_model is not None
        else None
      )
    return None
