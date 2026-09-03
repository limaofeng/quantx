from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_infrastructure.services.t_trade_batch_metrics import (
  METRIC_BASIS_ESTIMATED,
  METRIC_ORIGIN_LEGACY_BACKFILL,
  METRIC_ORIGIN_RULE_ESTIMATE,
  METRIC_QUALITY_COMPLETE,
  METRIC_QUALITY_INCOMPLETE,
  calculate_t_trade_batch_metrics,
  extract_t_trade_cost_snapshot,
)


def _batch(**overrides):
  values = {
    "environment": "LIVE",
    "metrics_origin": METRIC_ORIGIN_RULE_ESTIMATE,
    "entry_filled_volume": 100,
    "entry_avg_price": 10.0,
    "exit_filled_volume": 100,
    "exit_avg_price": 11.0,
    "entry_filled_at": datetime(2026, 8, 29, 1, 0),
    "closed_at": datetime(2026, 8, 29, 3, 0),
    "commission_rate": 0.0003,
    "minimum_commission": 5.0,
    "stamp_tax_rate": 0.0005,
    "transfer_fee_rate": 0.00001,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def test_closed_batch_applies_minimum_commission_once_per_side() -> None:
  metrics = calculate_t_trade_batch_metrics(_batch())

  assert metrics["quality"] == METRIC_QUALITY_COMPLETE
  assert metrics["entry_capital_cny"] == pytest.approx(1005.01)
  assert metrics["total_fees_cny"] == pytest.approx(10.571)
  assert metrics["realized_net_profit_cny"] == pytest.approx(89.429)
  assert metrics["mark_to_market_net_profit_cny"] == pytest.approx(89.429)
  assert metrics["holding_hours"] == pytest.approx(2.0)
  assert metrics["capital_utilization_pct"] == pytest.approx(100.0)


def test_partial_exit_allocates_entry_cost_but_requires_fresh_quote_for_mark() -> None:
  metrics = calculate_t_trade_batch_metrics(
    _batch(
      exit_filled_volume=50,
      closed_at=None,
    ),
    as_of=datetime(2026, 8, 29, 5, 0),
  )

  assert metrics["quality"] == METRIC_QUALITY_INCOMPLETE
  assert metrics["realized_net_profit_cny"] == pytest.approx(42.2145)
  assert metrics["mark_to_market_net_profit_cny"] is None
  assert metrics["net_return_pct"] is None
  assert metrics["holding_hours"] == pytest.approx(4.0)

  fresh = calculate_t_trade_batch_metrics(
    _batch(exit_filled_volume=50, closed_at=None),
    as_of=datetime(2026, 8, 29, 5, 0),
    market_price=12.0,
  )
  assert fresh["quality"] == METRIC_QUALITY_COMPLETE
  assert fresh["mark_to_market_net_profit_cny"] == pytest.approx(139.4035)


def test_over_exit_and_zero_live_commission_are_incomplete() -> None:
  assert (
    calculate_t_trade_batch_metrics(_batch(exit_filled_volume=101))["quality"]
    == METRIC_QUALITY_INCOMPLETE
  )
  assert (
    calculate_t_trade_batch_metrics(_batch(commission_rate=0.0))["quality"]
    == METRIC_QUALITY_INCOMPLETE
  )


def test_cost_snapshot_uses_nested_frozen_policy_without_defaults() -> None:
  snapshot = extract_t_trade_cost_snapshot(
    {
      "exit_plan_template": {
        "costs": {
          "commission_rate": 0.0003,
          "minimum_commission": 5.0,
          "stamp_tax_rate": 0.0005,
          "transfer_fee_rate": 0.00001,
        }
      }
    }
  )

  assert snapshot is not None
  assert snapshot.minimum_commission == 5.0
  assert extract_t_trade_cost_snapshot({"commission_rate": 0.0003}) is None


def test_capital_utilization_is_four_hours_over_holding_time() -> None:
  metrics = calculate_t_trade_batch_metrics(
    _batch(closed_at=datetime(2026, 8, 29, 9, 0))
  )

  assert metrics["holding_hours"] == pytest.approx(8.0)
  assert metrics["capital_utilization_pct"] == pytest.approx(50.0)


def test_legacy_backfill_origin_is_preserved_separately_from_basis() -> None:
  metrics = calculate_t_trade_batch_metrics(
    _batch(metrics_origin=METRIC_ORIGIN_LEGACY_BACKFILL)
  )

  assert metrics["basis"] == METRIC_BASIS_ESTIMATED
  assert metrics["origin"] == METRIC_ORIGIN_LEGACY_BACKFILL
  assert metrics["quality"] == METRIC_QUALITY_COMPLETE
