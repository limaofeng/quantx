from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_api.gqlapi.schemas import realtime_schema
from quantx_api.gqlapi.schemas.realtime_schema import RealtimeSubscription
from quantx_api.gqlapi.types.trading_types import TradingEventType
from quantx_infrastructure.models.enums import (
  OrderPriceType,
  OrderStatus,
  OrderType,
)


def _order(**overrides):
  values = {
    "id": 123,
    "sysid": "SYS123",
    "stock_code": "600000.SH",
    "type": OrderType.BUY,
    "volume": 100,
    "price_type": OrderPriceType.LIMIT,
    "price": 10.5,
    "traded_volume": 100,
    "traded_price": 10.5,
    "status": OrderStatus.SUCCEEDED,
    "status_msg": "filled",
    "strategy_name": "grid",
    "time": datetime(2026, 7, 26, 10, 0),
    "updated_at": datetime(2026, 7, 26, 10, 1),
  }
  values.update(overrides)
  return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_trading_events_reload_order_from_database_after_redis_wakeup(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  async def wakeups():
    yield {
      "message_type": "execution_report",
      "broker_order_id": 123,
    }

  async def get_order(_self, order_id: int):
    assert order_id == 123
    return _order()

  monkeypatch.setattr(
    realtime_schema.runtime_subscription_bridge,
    "subscribe_trading_events",
    wakeups,
  )
  monkeypatch.setattr(realtime_schema.OrderService, "get_order_by_id", get_order)

  stream = RealtimeSubscription().trading_events(
    event_types=[TradingEventType.ORDER_FILLED],
    stock_codes=["600000.SH"],
    strategy_names=["grid"],
  )
  event = await stream.__anext__()
  await stream.aclose()

  assert event.event_type is TradingEventType.ORDER_FILLED
  assert event.order.id == "123"
  assert event.order.status is OrderStatus.SUCCEEDED


@pytest.mark.parametrize(
  ("status", "expected"),
  [
    (OrderStatus.WAIT_REPORTING, TradingEventType.ORDER_CREATED),
    (OrderStatus.SUCCEEDED, TradingEventType.ORDER_FILLED),
    (OrderStatus.CANCELED, TradingEventType.ORDER_CANCELLED),
    (OrderStatus.JUNK, TradingEventType.ORDER_REJECTED),
  ],
)
def test_order_status_maps_to_public_event_type(
  status: OrderStatus,
  expected: TradingEventType,
) -> None:
  assert (
    RealtimeSubscription._event_type_for_order(status, "order_report")
    is expected
  )
