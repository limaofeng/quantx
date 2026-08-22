from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import utcnow
from quantx_api.gqlapi.schemas import trading_schema
from quantx_api.gqlapi.schemas.trading_schema import TradingQuery
from quantx_api.gqlapi.types.trading_types import (
  ManualOrderExecutionMode,
  ManualOrderPriceType,
)


def _info(*, manual: bool = True) -> SimpleNamespace:
  permissions = {"market:read"}
  if manual:
    permissions.add("trade:manual")
  return SimpleNamespace(
    context={
      "principal": Principal(
        user_id="user-1",
        username="operator",
        display_name="Operator",
        device_session_id="session-1",
        access_token_expires_at=utcnow() + timedelta(minutes=5),
        permissions=frozenset(permissions),
        authorized_account_ids=("ACCOUNT-1",),
        is_native_session=True,
      )
    }
  )


class _SessionContext:
  async def __aenter__(self):
    return SimpleNamespace(
      get=AsyncMock(return_value=SimpleNamespace(is_trading=True))
    )

  async def __aexit__(self, exc_type, exc, traceback):
    return False


def _ready():
  return {
    "can_increase_risk": True,
    "blocked_reasons": [],
  }


@pytest.mark.asyncio
async def test_order_entry_capabilities_are_server_driven_and_paper_first(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(trading_schema, "AsyncSessionLocal", _SessionContext)
  with patch.object(
    trading_schema.AccountExecutionSafetyService,
    "status",
    new=AsyncMock(return_value=_ready()),
  ):
    result = await TradingQuery().order_entry_capabilities(
      _info(), "600000.SH"
    )

  assert result.can_manual_trade is True
  assert result.default_execution_mode == ManualOrderExecutionMode.PAPER
  assert result.execution_modes == [
    ManualOrderExecutionMode.PAPER,
    ManualOrderExecutionMode.LIVE,
  ]
  assert result.supported_price_types == [
    ManualOrderPriceType.LIMIT,
    ManualOrderPriceType.BEST,
  ]


@pytest.mark.asyncio
async def test_beijing_market_never_advertises_best_quote(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(trading_schema, "AsyncSessionLocal", _SessionContext)
  with patch.object(
    trading_schema.AccountExecutionSafetyService,
    "status",
    new=AsyncMock(return_value=_ready()),
  ):
    result = await TradingQuery().order_entry_capabilities(
      _info(), "430047.BJ"
    )

  assert result.supported_price_types == [ManualOrderPriceType.LIMIT]


@pytest.mark.asyncio
async def test_missing_manual_scope_keeps_market_query_read_only(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(trading_schema, "AsyncSessionLocal", _SessionContext)
  safety_status = AsyncMock()
  with patch.object(
    trading_schema.AccountExecutionSafetyService,
    "status",
    new=safety_status,
  ):
    result = await TradingQuery().order_entry_capabilities(
      _info(manual=False), "600000.SH"
    )

  assert result.can_manual_trade is False
  assert result.execution_modes == []
  assert result.live_ready is False
  assert "trade:manual" in result.live_blocked_reasons[0]
  safety_status.assert_not_awaited()
