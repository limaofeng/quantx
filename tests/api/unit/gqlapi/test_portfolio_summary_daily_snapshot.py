from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quantx_api.gqlapi.resolvers.portfolio_summary import PortfolioSummaryResolver
from quantx_api.gqlapi.types.portfolio_types import Account


def _account():
  return Account(
    id="test",
    account_name="账户test",
    account_type="STOCK",
    total_asset=101_000,
    cash=101_000,
    frozen_cash=0,
    market_value=0,
    total_profit_loss=0,
    profit_loss_percent=0,
    create_time=datetime(2026, 1, 1),
    update_time=datetime(2026, 1, 1),
  )


@pytest.mark.asyncio
async def test_portfolio_summary_uses_latest_daily_asset_snapshot():
  snapshot = SimpleNamespace(
    daily_pnl_cny=Decimal("1000.00"),
    daily_return_pct=Decimal("1.000000"),
  )
  service = MagicMock()
  service.get_latest_account_snapshot = AsyncMock(return_value=snapshot)

  with patch(
    "quantx_api.gqlapi.resolvers.portfolio_summary.AccountResolver.get_account_async",
    new=AsyncMock(return_value=_account()),
  ), patch(
    "quantx_api.gqlapi.resolvers.portfolio_summary.PositionResolver.get_positions",
    new=AsyncMock(return_value=[]),
  ), patch(
    "quantx_api.gqlapi.resolvers.portfolio_summary.DailyAssetSnapshotService",
    return_value=service,
  ):
    summary = await PortfolioSummaryResolver.get_portfolio_summary("test")

  assert summary.today_profit_loss == 1000.0
  assert summary.today_profit_loss_percent == 1.0


@pytest.mark.asyncio
async def test_portfolio_summary_keeps_today_pnl_null_without_snapshot():
  service = MagicMock()
  service.get_latest_account_snapshot = AsyncMock(return_value=None)

  with patch(
    "quantx_api.gqlapi.resolvers.portfolio_summary.AccountResolver.get_account_async",
    new=AsyncMock(return_value=_account()),
  ), patch(
    "quantx_api.gqlapi.resolvers.portfolio_summary.PositionResolver.get_positions",
    new=AsyncMock(return_value=[]),
  ), patch(
    "quantx_api.gqlapi.resolvers.portfolio_summary.DailyAssetSnapshotService",
    return_value=service,
  ):
    summary = await PortfolioSummaryResolver.get_portfolio_summary("test")

  assert summary.today_profit_loss is None
  assert summary.today_profit_loss_percent is None
