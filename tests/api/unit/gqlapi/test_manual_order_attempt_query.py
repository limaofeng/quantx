from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import utcnow
from quantx_api.gqlapi.schemas import trading_schema
from quantx_api.gqlapi.schemas.trading_schema import TradingQuery
from quantx_api.gqlapi.types.trading_types import (
  ManualOrderExecutionMode,
  ManualOrderSide,
)


def _info() -> SimpleNamespace:
  return SimpleNamespace(
    context={
      "principal": Principal(
        user_id="user-1",
        username="operator",
        display_name="Operator",
        device_session_id="session-1",
        access_token_expires_at=utcnow() + timedelta(minutes=5),
        permissions=frozenset({"orders:read"}),
        authorized_account_ids=("ACCOUNT-1",),
        is_native_session=True,
      )
    }
  )


class _Result:
  def __init__(self, row):
    self.row = row

  def one_or_none(self):
    return self.row


class _SessionContext:
  row = None

  async def __aenter__(self):
    return SimpleNamespace(execute=AsyncMock(return_value=_Result(self.row)))

  async def __aexit__(self, exc_type, exc, traceback):
    return False


@pytest.mark.asyncio
async def test_manual_order_attempt_exposes_agent_rejection_without_broker_order(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = datetime(2026, 9, 1, 11, 6, 43)
  _SessionContext.row = (
    SimpleNamespace(
      account_id="ACCOUNT-1",
      broker_order_id=None,
      bucket="manual",
      client_order_id="client-order-1",
      created_at=now,
      execution_mode="live",
      instrument_code="688577.SH",
      side="SELL",
      status="REJECTED",
      status_reason="stale live quote",
      updated_at=now,
      user_id="user-1",
      volume=400,
    ),
    SimpleNamespace(
      delivery_status="REJECTED",
      last_error="stale live quote",
    ),
  )
  monkeypatch.setattr(trading_schema, "AsyncSessionLocal", _SessionContext)

  result = await TradingQuery().manual_order_attempt(
    _info(),
    client_order_id="client-order-1",
    account_id="ACCOUNT-1",
  )

  assert result is not None
  assert result.broker_order_id is None
  assert result.status == "REJECTED"
  assert result.delivery_status == "REJECTED"
  assert result.side == ManualOrderSide.SELL
  assert result.execution_mode == ManualOrderExecutionMode.LIVE
  assert result.message == "QMT Agent 下单前行情已超过 30 秒，未向券商提交"


@pytest.mark.asyncio
async def test_manual_order_attempt_reports_broker_order_id_as_created(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = datetime(2026, 9, 1, 13, 5, 0)
  _SessionContext.row = (
    SimpleNamespace(
      broker_order_id="broker-123",
      client_order_id="client-order-2",
      created_at=now,
      execution_mode="live",
      instrument_code="605499.SH",
      side="SELL",
      status="SUBMITTED",
      status_reason=None,
      updated_at=now,
      volume=200,
    ),
    SimpleNamespace(delivery_status="ACKNOWLEDGED", last_error=None),
  )
  monkeypatch.setattr(trading_schema, "AsyncSessionLocal", _SessionContext)

  result = await TradingQuery().manual_order_attempt(
    _info(),
    client_order_id="client-order-2",
    account_id="ACCOUNT-1",
  )

  assert result is not None
  assert result.broker_order_id == "broker-123"
  assert result.message == "券商委托已生成：broker-123；最终状态以券商回报为准"
