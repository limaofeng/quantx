from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from quantx_infrastructure.core.exit_plan_replay_metrics import (
  build_exit_plan_replay_metrics,
)


def _runtime(*, with_trade: bool = True):
  start = datetime(2026, 8, 3, 9, 30)
  raw_curve = []
  for index, price in enumerate((10.0, 10.5, 9.8, 9.5, 9.2, 9.0)):
    raw_curve.append(
      {
        "timestamp": start + timedelta(days=index),
        "equity": 1000.0 if index == 0 else 1050.0,
        "passive_equity": price * 100,
      }
    )
  trade = SimpleNamespace(
    commission=5.6,
    metadata={
      "exit_plan_id": "plan-1",
      "exit_rule_id": "rule-1",
      "exit_rule_type": "TRAILING_NET_PROFIT",
      "exit_reason": "trailing floor reached",
    },
    price=10.4,
    trade_time=start + timedelta(days=1),
    trade_type="SELL",
    volume=100,
  )
  plan = SimpleNamespace(remaining_volume=0, exit_avg_price=10.4)
  broker = SimpleNamespace(
    max_drawdown=0.02,
    replay_curve=raw_curve,
    trades=[trade] if with_trade else [],
  )
  return SimpleNamespace(
    broker=broker,
    context=SimpleNamespace(
      initial_capital=1000.0,
      parameters={
        "actual_sell_references": [],
        "commission_rate": 0.0003,
        "exit_plan_replay_origin": {"mode": "BUY_FILLS"},
        "exit_plan_replay_template": {"plan_id": "plan-1"},
        "initial_total_asset": 1000.0,
        "minimum_commission": 5.0,
        "replay_entry_volume": 100,
        "replay_tick_read_audit": {"issues": []},
        "slippage_rate": 0.0001,
        "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
      },
    ),
    exit_plan_book=SimpleNamespace(plans={"plan-1": plan}),
  )


def test_exit_plan_replay_metrics_compare_three_paths_without_forced_close() -> None:
  result = build_exit_plan_replay_metrics(_runtime())

  assert result["data_quality"] == "OK"
  assert result["summary"]["sold_volume"] == 100
  assert result["summary"]["remaining_volume"] == 0
  assert result["summary"]["plan_return_pct"] == pytest.approx(5.0)
  assert result["summary"]["hold_return_pct"] == pytest.approx(-10.0)
  assert result["summary"]["conclusion_code"] == "PLAN_OUTPERFORMED_HOLD"
  assert result["events"][0]["rule_type"] == "TRAILING_NET_PROFIT"
  assert result["post_exit_horizons"][0]["available"] is True
  assert result["post_exit_horizons"][-1]["available"] is False
  assert all(
    event["reason"] != "BACKTEST_END_FORCE_CLOSE" for event in result["events"]
  )


def test_exit_plan_replay_metrics_mark_remaining_position_to_market() -> None:
  runtime = _runtime(with_trade=False)
  runtime.exit_plan_book.plans["plan-1"].remaining_volume = 100
  runtime.exit_plan_book.plans["plan-1"].exit_avg_price = 0.0

  result = build_exit_plan_replay_metrics(runtime)

  assert result["summary"]["remaining_volume"] == 100
  assert result["summary"]["plan_final_value"] == 1050.0
  assert result["summary"]["conclusion_code"] == "PLAN_NOT_TRIGGERED"
  assert result["events"] == []
