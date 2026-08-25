from decimal import Decimal
from types import SimpleNamespace

import pytest

from quantx_infrastructure.core.brokers.live import LiveBroker


class _TradingService:
  def __init__(self) -> None:
    self.position_service = SimpleNamespace(get_positions=self._get_positions)

  async def get_account_info(self, realtime: bool = False):
    assert realtime is True
    return SimpleNamespace(
      account_id="account-1",
      total_asset=Decimal("123456.78"),
      cash=Decimal("23456.78"),
      frozen_cash=Decimal("100.25"),
      market_value=Decimal("100000.00"),
    )

  async def _get_positions(self, *, account_id: str):
    assert account_id == "account-1"
    return []


@pytest.mark.asyncio
async def test_get_account_normalizes_database_decimals_to_domain_floats() -> None:
  broker = LiveBroker(account_id="account-1", initial_capital=Decimal("100000.00"))
  broker.trading_service = _TradingService()
  broker.is_connected = True

  account = await broker.get_account()

  assert broker.initial_capital == 100000.0
  assert account.total_asset == pytest.approx(123456.78)
  assert account.cash == pytest.approx(23456.78)
  assert account.frozen_cash == pytest.approx(100.25)
  assert account.market_value == pytest.approx(100000.0)
  assert account.total_pnl == pytest.approx(23456.78)
  assert all(
    isinstance(value, float)
    for value in (
      account.total_asset,
      account.cash,
      account.frozen_cash,
      account.market_value,
      account.total_pnl,
    )
  )
