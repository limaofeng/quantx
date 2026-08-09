from datetime import date, datetime
from types import SimpleNamespace

import pytest
from quantx_infrastructure.services.daily_asset_snapshot_service import (
  DailyAssetSnapshotService,
)


def _values(total_asset, previous_asset=100_000, net_flow=0, **overrides):
  previous = (
    SimpleNamespace(id="prev-snapshot", total_asset_cny=previous_asset)
    if previous_asset is not None
    else None
  )
  params = {
    "scope_type": "ACCOUNT",
    "scope_key": "account:test",
    "account_id": "test",
    "account_type": None,
    "strategy_run_id": None,
    "trade_date": date(2026, 5, 29),
    "snapshot_at": datetime(2026, 5, 29, 15, 5),
    "source": "MINIQMT",
    "total_asset_cny": total_asset,
    "cash_available_cny": total_asset,
    "cash_frozen_cny": 0,
    "market_value_cny": 0,
    "net_capital_flow_cny": net_flow,
    "previous_snapshot": previous,
    "metadata": {},
  }
  params.update(overrides)
  return DailyAssetSnapshotService.build_snapshot_values(**params)


def test_daily_pnl_uses_total_asset_delta():
  values = _values(102_000)

  assert values["gross_asset_delta_cny"] == pytest.approx(2_000)
  assert values["daily_pnl_cny"] == pytest.approx(2_000)
  assert values["daily_return_pct"] == pytest.approx(2.0)


def test_daily_pnl_excludes_capital_flow():
  values = _values(111_000, net_flow=10_000)

  assert values["gross_asset_delta_cny"] == pytest.approx(11_000)
  assert values["daily_pnl_cny"] == pytest.approx(1_000)
  assert values["daily_return_pct"] == pytest.approx(0.909091)


def test_first_snapshot_has_no_daily_pnl():
  values = _values(100_000, previous_asset=None)

  assert values["gross_asset_delta_cny"] is None
  assert values["daily_pnl_cny"] is None
  assert values["daily_return_pct"] is None
  assert values["previous_snapshot_id"] is None
  assert "NO_PREVIOUS_SNAPSHOT" in values["snapshot_metadata"]["quality_flags"]


def test_sold_position_profit_still_counts_as_positive_daily_pnl():
  values = _values(
    101_000,
    cash_available_cny=101_000,
    market_value_cny=0,
  )

  assert values["daily_pnl_cny"] == pytest.approx(1_000)
  assert values["market_value_cny"] == 0


def test_component_mismatch_is_flagged_without_changing_pnl():
  values = _values(
    102_000,
    cash_available_cny=80_000,
    market_value_cny=20_000,
  )

  assert values["daily_pnl_cny"] == pytest.approx(2_000)
  assert "ASSET_COMPONENT_MISMATCH" in values["snapshot_metadata"]["quality_flags"]
