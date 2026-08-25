from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine.strategy_manager import StrategyManager


@pytest.mark.asyncio
async def test_manual_portfolio_downloads_d1_for_exact_configured_stocks(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  StrategyManager._instance = None
  manager = StrategyManager()
  runtime = SimpleNamespace(
    run_id="replay-manual-1",
    context=SimpleNamespace(
      backtest_id="backtest-manual-1",
      parameters={
        "t_trade_replay": True,
        "account_id": "account-1",
        "initial_portfolio": {
          "source": "MANUAL",
          "as_of": "2026-07-31T15:00:00",
          "positions": [
            {"stock_code": "600887.SH", "volume": 400},
            {"stock_code": "000001.SZ", "volume": 50},
          ],
        },
      },
    ),
  )
  missing = {
    "000001.SZ": {
      "dates": {date(2026, 7, 31)},
      "klines": {"1d"},
      "tick": False,
    }
  }
  find_missing = AsyncMock(side_effect=[missing, {}])
  sync_missing = AsyncMock()
  set_phase = AsyncMock()
  monkeypatch.setattr(manager, "_find_missing_backtest_data", find_missing)
  monkeypatch.setattr(manager, "_sync_missing_backtest_data", sync_missing)
  monkeypatch.setattr(manager, "_set_t_trade_replay_phase", set_phase)

  await manager._ensure_t_trade_portfolio_reference_data(
    runtime,
    SimpleNamespace(),
  )

  assert find_missing.await_count == 2
  first_check = find_missing.await_args_list[0].kwargs
  assert first_check["instruments"] == ["000001.SZ", "600887.SH"]
  assert first_check["start_time"] == datetime(2026, 7, 31)
  assert first_check["end_time"].date() == date(2026, 7, 31)
  assert first_check["required_kline_periods"] == {"1d"}
  assert first_check["require_tick"] is False
  sync_missing.assert_awaited_once_with(
    runtime=runtime,
    missing=missing,
    sync_periods={"1d"},
  )
  StrategyManager._instance = None


@pytest.mark.asyncio
async def test_manual_portfolio_is_marked_to_d1_close_before_broker_start(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  StrategyManager._instance = None
  manager = StrategyManager()
  parameters = {
    "t_trade_replay": True,
    "initial_portfolio": {
      "source": "MANUAL",
      "as_of": "2026-07-31T15:00:00",
      "cash_available": 20_000.0,
      "positions": [
        {"stock_code": "600887.SH", "volume": 400, "avg_price": 24.0},
        {"stock_code": "000001.SZ", "volume": 100, "avg_price": 10.0},
      ],
    },
    "initial_portfolio_metadata": {
      "600887.SH": {},
      "000001.SZ": {},
    },
  }
  runtime = SimpleNamespace(
    run_id="replay-manual-valuation",
    context=SimpleNamespace(
      parameters=parameters,
      instruments=["000001.SZ", "600887.SH"],
      backtest_id="backtest-manual-valuation",
      initial_capital=0.0,
    ),
    metrics=SimpleNamespace(initial_capital=0.0, current_capital=0.0),
  )
  closes = {"600887.SH": 25.0, "000001.SZ": 11.0}

  class MarketDataService:
    async def get_kline_data(self, stock_code, **_kwargs):
      return [SimpleNamespace(close=closes[stock_code])]

  run_repo = SimpleNamespace(update_run=AsyncMock())
  backtest = SimpleNamespace(parameters={})
  backtest_repo = SimpleNamespace(get_backtest=AsyncMock(return_value=backtest))
  db = SimpleNamespace(commit=AsyncMock())

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(
    "quantx_engine.strategy_manager.HistoricalMarketDataService",
    MarketDataService,
  )
  monkeypatch.setattr(
    "quantx_engine.strategy_manager.get_async_db",
    fake_get_async_db,
  )
  monkeypatch.setattr(
    "quantx_engine.strategy_manager.StrategyRunRepository",
    lambda _db: run_repo,
  )
  monkeypatch.setattr(
    "quantx_engine.strategy_manager.BacktestRepository",
    lambda _db: backtest_repo,
  )

  await manager._finalize_t_trade_replay_initial_portfolio(runtime)

  expected_total = 20_000.0 + 400 * 25.0 + 100 * 11.0
  assert runtime.context.initial_capital == expected_total
  assert runtime.metrics.initial_capital == expected_total
  assert parameters["initial_total_asset"] == expected_total
  assert parameters["initial_positions"][0]["available_volume"] == 400
  assert parameters["initial_positions"][0]["last_price"] == 25.0
  assert parameters["initial_asset_reconciliation"]["policy"] == (
    "MANUAL_D1_MARK_TO_MARKET"
  )
  run_repo.update_run.assert_awaited_once_with(
    runtime.run_id,
    {"parameters": parameters, "initial_capital": expected_total},
  )
  assert backtest.parameters is parameters
  db.commit.assert_awaited_once()
  StrategyManager._instance = None
