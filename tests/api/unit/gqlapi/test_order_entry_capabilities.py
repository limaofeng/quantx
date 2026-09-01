from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_api import manual_order_runtime
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
      get=AsyncMock(return_value=SimpleNamespace(is_trading=False))
    )

  async def __aexit__(self, exc_type, exc, traceback):
    return False


def _ready():
  return {
    "can_increase_risk": True,
    "can_reduce_risk": True,
    "blocked_reasons": [],
  }


def _configure_live(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(manual_order_runtime.settings, "runtime_profile", "full")
  monkeypatch.setattr(manual_order_runtime.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    manual_order_runtime.settings,
    "real_trading_account_allowlist",
    ["ACCOUNT-1"],
  )


def _configure_paper(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(manual_order_runtime.settings, "runtime_profile", "web")
  monkeypatch.setattr(manual_order_runtime.settings, "enable_real_trading", False)
  monkeypatch.setattr(
    manual_order_runtime.settings,
    "real_trading_account_allowlist",
    [],
  )


@pytest.mark.asyncio
async def test_order_entry_capabilities_follow_live_trading_and_ignore_stale_flag(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _configure_live(monkeypatch)
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
  assert result.default_execution_mode == ManualOrderExecutionMode.LIVE
  assert result.execution_modes == [ManualOrderExecutionMode.LIVE]
  assert result.supported_price_types == [
    ManualOrderPriceType.LIMIT,
    ManualOrderPriceType.BEST,
  ]


@pytest.mark.asyncio
async def test_order_entry_capabilities_use_paper_only_when_live_trading_is_disabled(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _configure_paper(monkeypatch)
  monkeypatch.setattr(trading_schema, "AsyncSessionLocal", _SessionContext)
  safety_status = AsyncMock()
  with patch.object(
    trading_schema.AccountExecutionSafetyService,
    "status",
    new=safety_status,
  ):
    result = await TradingQuery().order_entry_capabilities(
      _info(), "600000.SH"
    )

  assert result.can_manual_trade is True
  assert result.default_execution_mode == ManualOrderExecutionMode.PAPER
  assert result.execution_modes == [ManualOrderExecutionMode.PAPER]
  assert result.live_ready is False
  assert result.can_live_buy is False
  assert result.can_live_sell is False
  assert result.live_blocked_reasons == []
  safety_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("can_increase_risk", "can_reduce_risk"),
  [(False, True), (False, False)],
)
async def test_order_entry_capabilities_never_downgrade_configured_live_to_paper(
  monkeypatch: pytest.MonkeyPatch,
  can_increase_risk: bool,
  can_reduce_risk: bool,
) -> None:
  _configure_live(monkeypatch)
  monkeypatch.setattr(trading_schema, "AsyncSessionLocal", _SessionContext)
  with patch.object(
    trading_schema.AccountExecutionSafetyService,
    "status",
    new=AsyncMock(
      return_value={
        "can_increase_risk": can_increase_risk,
        "can_reduce_risk": can_reduce_risk,
        "blocked_reasons": ["实盘安全门禁暂未就绪"],
      }
    ),
  ):
    result = await TradingQuery().order_entry_capabilities(
      _info(), "600000.SH"
    )

  assert result.default_execution_mode == ManualOrderExecutionMode.LIVE
  assert result.execution_modes == [ManualOrderExecutionMode.LIVE]
  assert ManualOrderExecutionMode.PAPER not in result.execution_modes
  assert result.can_live_buy is can_increase_risk
  assert result.can_live_sell is can_reduce_risk
  assert result.live_blocked_reasons == ["实盘安全门禁暂未就绪"]


@pytest.mark.asyncio
async def test_beijing_market_never_advertises_best_quote(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _configure_live(monkeypatch)
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
  _configure_live(monkeypatch)
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
