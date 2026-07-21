from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database.relational_base import BulkSaveResult
from models.enums import AccountType, OrderPriceType, OrderStatus, OrderType
from services.order_service import OrderService


def make_xt_order(order_id: int = 123) -> SimpleNamespace:
  return SimpleNamespace(
    order_id=order_id,
    account_id="account-1",
    account_type=AccountType.STOCK.to_int(),
    stock_code="688552.SH",
    order_sysid=f"sys-{order_id}",
    order_time=1_752_634_800,
    order_type=int(OrderType.BUY),
    order_volume=400,
    price_type=int(OrderPriceType.LIMIT),
    price=24.78,
    traded_volume=400,
    traded_price=24.78,
    order_status=int(OrderStatus.SUCCEEDED),
    status_msg="",
    strategy_name="",
    order_remark="",
  )


@pytest.mark.asyncio
async def test_sync_today_orders_upserts_miniqmt_snapshot(monkeypatch):
  xt_order = make_xt_order()
  manager = SimpleNamespace(
    account_id="account-1",
    is_connected=True,
    get_orders=lambda cancelable_only=False: [xt_order],
  )
  service = object.__new__(OrderService)
  service.trading_manager = manager
  service.save_orders = AsyncMock(
    return_value=BulkSaveResult([], 1, 0, 1)
  )
  monkeypatch.setattr("services.order_service.get_stock_name", lambda _: "航天南湖")

  result = await service.sync_today_orders("account-1")

  assert result.saved_count == 1
  saved_order = service.save_orders.await_args.args[0][0]
  assert saved_order.id == 123
  assert saved_order.traded_volume == 400
  assert saved_order.traded_price == 24.78
  assert saved_order.status is OrderStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_sync_today_orders_rejects_disconnected_miniqmt():
  service = object.__new__(OrderService)
  service.trading_manager = SimpleNamespace(
    account_id="account-1", is_connected=False
  )

  with pytest.raises(ValueError, match="交易连接未建立"):
    await service.sync_today_orders("account-1")
