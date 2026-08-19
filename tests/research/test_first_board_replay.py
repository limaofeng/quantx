from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from quantx_research.runner import run_study, validate_study
from quantx_research.studies.first_board_promotion import DEFAULT_FEATURE_COLUMNS
from quantx_research.studies.first_board_replay import FirstBoardPolicyReplay


def _snapshots() -> pd.DataFrame:
  signal_at = datetime(2026, 8, 18, 14, 0)
  row: dict[str, object] = {
    "instrument_code": "600000.SH",
    "signal_at": signal_at,
    "feature_as_of": signal_at - timedelta(seconds=1),
    "stage": "NEAR_LIMIT",
    "change_pct": 9.9,
    "limit_up_price": 10.0,
    "current_price": 9.99,
    "price_tick": 0.01,
    "open": 9.2,
    "high": 9.99,
    "low": 9.1,
    "amount": 100_000_000,
    "bid1_volume": 10_000,
    "history_trading_days": 500,
    "previous_limit_up_streak": 0,
    "recent_limit_up_count_10d": 0,
    "price_position_252": 0.5,
    "prior_20d_return_pct": 5.0,
    "ma20_deviation_pct": 5.0,
    "realized_volatility_20_pct": 10.0,
    "amount_pace_ratio": 2.0,
    "volume_pace_ratio": 2.0,
    "last_5m_volume_ratio": 2.0,
    "price_change_5m_pct": 1.0,
    "depth_imbalance_5": 0.5,
    "industry_candidate_count": 3,
    "sector_promotion_rate": 0.3,
    "turnover_rate_pct": 8.0,
  }
  defaults = {
    "price_position_252d": 0.5,
    "return_5d_pct": 2.0,
    "return_20d_pct": 5.0,
    "ma20_deviation_pct": 5.0,
    "volatility_20d_pct": 10.0,
    "recent_limit_up_count": 0.0,
    "free_float_log": 20.0,
    "volume_acceleration": 2.0,
    "turnover_rate_pct": 8.0,
    "order_book_strength": 0.5,
    "sector_promotion_rate": 0.3,
    "market_break_rate": 0.1,
  }
  for column in DEFAULT_FEATURE_COLUMNS:
    row[column] = defaults[column]
  return pd.DataFrame([row])


def _ticks(*, with_depth: bool = True) -> pd.DataFrame:
  rows = [
    {
      "instrument_code": "600000.SH",
      "timestamp": datetime(2026, 8, 18, 14, 0, 1),
      "last_price": 9.99,
      "limit_up_price": 10.0,
      "limit_down_price": 8.0,
      "price_tick": 0.01,
      "bid1_price": 9.99,
      "bid1_volume": 10_000 if with_depth else 0,
      "ask1_price": 10.0,
      "ask1_volume": 10_000 if with_depth else 0,
      "bid_prices": [9.99, 9.98, 9.97, 9.96, 9.95],
      "bid_volumes": [10_000] * 5,
      "ask_prices": [10.0, 10.01, 10.02, 10.03, 10.04],
      "ask_volumes": [10_000] * 5,
      "volume": 1_000_000,
      "amount": 100_000_000,
    },
    {
      "instrument_code": "600000.SH",
      "timestamp": datetime(2026, 8, 19, 9, 31),
      "last_price": 10.5,
      "limit_up_price": 11.0,
      "limit_down_price": 9.0,
      "price_tick": 0.01,
      "bid1_price": 10.49,
      "bid1_volume": 10_000 if with_depth else 0,
      "ask1_price": 10.5,
      "ask1_volume": 10_000 if with_depth else 0,
      "bid_prices": [10.49, 10.48, 10.47, 10.46, 10.45],
      "bid_volumes": [10_000] * 5,
      "ask_prices": [10.5, 10.51, 10.52, 10.53, 10.54],
      "ask_volumes": [10_000] * 5,
      "volume": 100_000,
      "amount": 10_000_000,
    },
    {
      "instrument_code": "600000.SH",
      "timestamp": datetime(2026, 8, 19, 9, 32),
      "last_price": 10.1,
      "limit_up_price": 11.0,
      "limit_down_price": 9.0,
      "price_tick": 0.01,
      "bid1_price": 10.09,
      "bid1_volume": 10_000 if with_depth else 0,
      "ask1_price": 10.1,
      "ask1_volume": 10_000 if with_depth else 0,
      "bid_prices": [10.09, 10.08, 10.07, 10.06, 10.05],
      "bid_volumes": [10_000] * 5,
      "ask_prices": [10.1, 10.11, 10.12, 10.13, 10.14],
      "ask_volumes": [10_000] * 5,
      "volume": 200_000,
      "amount": 20_000_000,
    },
  ]
  return pd.DataFrame(rows)


def test_replay_uses_shared_signal_and_exit_plan() -> None:
  result = FirstBoardPolicyReplay().run(_snapshots(), _ticks())

  assert result.quality.market_signal_count == 1
  assert result.quality.completed_trade_count == 1
  assert result.quality.coverage_ratio == 1.0
  trade = result.trades.iloc[0]
  assert trade["entry_price"] == 10.0
  assert trade["exit_rule"] == "TRAILING_PRICE_DRAWDOWN"
  assert trade["exit_reason"] == "FIRST_BOARD_T1_WEAKNESS_EXIT"
  assert trade["holding_trading_days"] == 2


def test_replay_excludes_missing_executable_depth() -> None:
  result = FirstBoardPolicyReplay().run(_snapshots(), _ticks(with_depth=False))

  assert result.trades.empty
  assert result.quality.exclusion_reasons["ENTRY_NOT_FILLED"] == 1
  assert result.quality.coverage_ratio == 0.0


def test_replay_rejects_future_features() -> None:
  snapshots = _snapshots()
  snapshots.loc[0, "feature_as_of"] = snapshots.loc[0, "signal_at"] + timedelta(
    seconds=1
  )

  with pytest.raises(ValueError, match="future data detected"):
    FirstBoardPolicyReplay().run(snapshots, _ticks())


@pytest.mark.asyncio
async def test_first_board_cli_runner_validates_and_writes_artifacts(
  tmp_path: Path,
) -> None:
  snapshots_path = tmp_path / "features.parquet"
  ticks_path = tmp_path / "ticks.parquet"
  config_path = tmp_path / "first-board.yaml"
  _snapshots().to_parquet(snapshots_path, index=False)
  _ticks().to_parquet(ticks_path, index=False)
  config_path.write_text(
    "\n".join(
      [
        "study: first-board-promotion",
        "version: test",
        "feature_snapshots: features.parquet",
        "tick_archive: ticks.parquet",
        "output_root: output",
        "minimum_replay_coverage_ratio: 1.0",
        "model:",
        "  min_train_trading_days: 1",
        "  bootstrap_samples: 100",
      ]
    ),
    encoding="utf-8",
  )

  validation = await validate_study(config_path)
  run_dir = await run_study(config_path)

  assert validation["valid"] is True
  assert (run_dir / "manifest.json").is_file()
  assert (run_dir / "data-quality.json").is_file()
  assert (run_dir / "trade-details.parquet").is_file()
  assert (run_dir / "signal-decisions.parquet").is_file()
  assert (run_dir / "model-predictions.parquet").is_file()
  assert (run_dir / "report.html").is_file()
