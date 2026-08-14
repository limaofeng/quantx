from datetime import timedelta
from types import SimpleNamespace

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.schemas import trading_schema
from quantx_api.gqlapi.types import CancelOrderInput
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.enums import OrderStatus
from quantx_infrastructure.services import order_service as order_service_module
from quantx_infrastructure.services.order_service import OrderService


def _principal() -> Principal:
  return Principal(
    user_id="user-1",
    username="operator",
    display_name="Operator",
    device_session_id="device-session-1",
    access_token_expires_at=time_utils.now() + timedelta(minutes=5),
    permissions=frozenset({"trade:manual"}),
    authorized_account_ids=("ACCOUNT-1",),
  )


@pytest.mark.asyncio
async def test_order_service_hides_order_owned_by_another_account(monkeypatch):
  async def sessions():
    yield object()

  class Repository:
    async def find_by_id(self, _order_id):
      return SimpleNamespace(account_id="ACCOUNT-2")

  monkeypatch.setattr(order_service_module, "get_async_db", sessions)
  monkeypatch.setattr(
    order_service_module,
    "OrderRepository",
    lambda _db: Repository(),
  )

  assert await OrderService("ACCOUNT-1").get_order_by_id(12345) is None


@pytest.mark.asyncio
async def test_cancel_rejects_terminal_order_before_command_queue(monkeypatch):
  class TerminalOrderService:
    def __init__(self, account_id):
      assert account_id == "ACCOUNT-1"

    async def get_order_by_id(self, order_id):
      assert order_id == 12345
      return SimpleNamespace(status=OrderStatus.SUCCEEDED)

  class QueueMustNotBeReached:
    def __init__(self, _db):
      raise AssertionError("terminal order must not reach the command queue")

  monkeypatch.setattr(trading_schema, "OrderService", TerminalOrderService)
  monkeypatch.setattr(
    trading_schema,
    "TradeCommandService",
    QueueMustNotBeReached,
  )

  result = await trading_schema.TradingMutation().cancel_order(
    SimpleNamespace(context={"principal": _principal()}),
    CancelOrderInput(
      account_id="ACCOUNT-1",
      order_id=12345,
      idempotency_key="cancel-12345",
    ),
  )

  assert result.success is False
  assert result.status == "REJECTED"
  assert "不允许撤单" in result.message
  assert "confirmation_token" not in CancelOrderInput.__annotations__
