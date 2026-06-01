from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gqlapi.resolvers.portfolio_summary import PortfolioSummaryResolver


def _manager():
  manager = MagicMock()
  manager.get_account_info.return_value = {
    "total_asset": 101_000,
    "cash": 101_000,
    "market_value": 0,
    "profit_loss": 0,
    "profit_loss_ratio": 0,
  }
  manager.get_positions.return_value = []
  return manager


@pytest.mark.asyncio
async def test_portfolio_summary_uses_latest_daily_asset_snapshot():
  snapshot = SimpleNamespace(
    daily_pnl_cny=Decimal("1000.00"),
    daily_return_pct=Decimal("1.000000"),
  )
  service = MagicMock()
  service.get_latest_account_snapshot = AsyncMock(return_value=snapshot)

  with patch(
    "gqlapi.resolvers.portfolio_summary.registry.get_manager",
    return_value=_manager(),
  ), patch(
    "gqlapi.resolvers.portfolio_summary.DailyAssetSnapshotService",
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
    "gqlapi.resolvers.portfolio_summary.registry.get_manager",
    return_value=_manager(),
  ), patch(
    "gqlapi.resolvers.portfolio_summary.DailyAssetSnapshotService",
    return_value=service,
  ):
    summary = await PortfolioSummaryResolver.get_portfolio_summary("test")

  assert summary.today_profit_loss is None
  assert summary.today_profit_loss_percent is None
