from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_infrastructure.core.t_trade_replay_metrics import (
  build_t_trade_replay_metrics,
)


def _runtime(*, reconciliation: dict, initial_equity: float):
  timestamp = datetime(2024, 1, 2, 15, 0)
  broker = SimpleNamespace(
    initial_capital=initial_equity,
    initial_asset_reconciliation=reconciliation,
    current_time=timestamp,
    trades=[],
    replay_curve=[
      {
        "timestamp": timestamp,
        "equity": initial_equity,
        "passive_equity": initial_equity,
      }
    ],
    get_performance_metrics=lambda: {"max_drawdown_pct": 0.0},
  )
  return SimpleNamespace(
    broker=broker,
    context=SimpleNamespace(
      backtest_start_time=datetime(2024, 1, 2, 9, 30),
      backtest_end_time=timestamp,
      parameters={
        "initial_total_asset": 100_000.0,
        "initial_cash": 80_000.0,
        "initial_asset_reconciliation": reconciliation,
        "max_total_t_exposure_pct": 0.1,
      },
    ),
  )


def test_metrics_disclose_constant_non_trading_asset_and_snapshot_quality() -> None:
  reconciliation = {
    "reported_total_asset": 100_000.0,
    "available_cash": 80_000.0,
    "position_market_value": 10_000.0,
    "raw_residual": 10_000.0,
    "non_trading_asset": 10_000.0,
    "effective_initial_equity": 100_000.0,
    "negative_residual_clamped": False,
    "quality_flags": [
      "ASSET_COMPONENT_MISMATCH",
      "NON_TRADING_ASSET_RESIDUAL_PRESERVED",
    ],
  }

  metrics = build_t_trade_replay_metrics(
    _runtime(reconciliation=reconciliation, initial_equity=100_000.0)
  )

  assert metrics["data_quality"] == "PARTIAL"
  assert "10000.00 元" in metrics["data_quality_message"]
  assert "ASSET_COMPONENT_MISMATCH" in metrics["data_quality_message"]
  assert metrics["summary"]["initial_equity"] == 100_000.0
  assert metrics["summary"]["non_trading_asset"] == 10_000.0
  assert metrics["summary"]["total_return_pct"] == 0.0
  assert metrics["methodology"]["initial_asset_reconciliation"] == reconciliation


def test_metrics_use_effective_equity_after_negative_residual_is_clamped() -> None:
  reconciliation = {
    "reported_total_asset": 100_000.0,
    "available_cash": 95_000.0,
    "position_market_value": 10_000.0,
    "raw_residual": -5_000.0,
    "non_trading_asset": 0.0,
    "effective_initial_equity": 105_000.0,
    "negative_residual_clamped": True,
    "quality_flags": ["INITIAL_COMPONENTS_EXCEED_REPORTED_TOTAL"],
  }

  metrics = build_t_trade_replay_metrics(
    _runtime(reconciliation=reconciliation, initial_equity=105_000.0)
  )

  assert metrics["data_quality"] == "PARTIAL"
  assert "高 5000.00 元" in metrics["data_quality_message"]
  assert metrics["summary"]["reported_initial_equity"] == 100_000.0
  assert metrics["summary"]["initial_equity"] == 105_000.0
  assert metrics["summary"]["final_equity"] == 105_000.0
  assert metrics["summary"]["total_return_pct"] == pytest.approx(0.0)
  assert metrics["summary"]["max_drawdown_pct"] == pytest.approx(0.0)
