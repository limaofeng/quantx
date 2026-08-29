from dataclasses import asdict

import pytest
import quantx_domain.trading.t_trade as t_trade
from quantx_domain.trading.exit_plan import TradingCostPolicy
from quantx_domain.trading.t_trade import (
  AShareCumulativeVolumeSource,
  TickSample,
  normalize_ashare_cumulative_volume,
)


def test_tick_sample_remains_a_serializable_exit_projection_primitive():
  sample = TickSample(
    timestamp_ms=1_724_300_000_000,
    price=10.02,
    bid_price=10.01,
    ask_price=10.02,
    cumulative_amount=1_000_000.0,
    cumulative_volume=100_000.0,
  )

  assert asdict(sample) == {
    "timestamp_ms": 1_724_300_000_000,
    "price": 10.02,
    "bid_price": 10.01,
    "ask_price": 10.02,
    "cumulative_amount": 1_000_000.0,
    "cumulative_volume": 100_000.0,
  }


def test_t_trade_module_exposes_costs_but_no_legacy_signal_or_sizing_path():
  assert t_trade.TradingCostPolicy is TradingCostPolicy
  for legacy_name in (
    "SignalPolicy",
    "IntradayTSignal",
    "TTradeSizingResult",
    "evaluate_intraday_t_signal",
    "calculate_target_trade_volume",
  ):
    assert not hasattr(t_trade, legacy_name)


def test_ashare_cumulative_volume_prefers_native_raw_shares():
  normalized = normalize_ashare_cumulative_volume(
    pvolume=123_456,
    volume=1_234,
  )

  assert normalized.shares == 123_456
  assert normalized.source is AShareCumulativeVolumeSource.NATIVE_PVOLUME


def test_ashare_cumulative_volume_derives_shares_from_xtdata_lots():
  normalized = normalize_ashare_cumulative_volume(
    pvolume=0,
    volume=56_708,
  )

  assert normalized.shares == 5_670_800
  assert normalized.source is AShareCumulativeVolumeSource.DERIVED_VOLUME_LOTS
  assert 562_552_320 / normalized.shares == pytest.approx(99.2003, rel=1e-4)


def test_ashare_cumulative_volume_fails_closed_without_positive_counter():
  normalized = normalize_ashare_cumulative_volume(pvolume=0, volume=float("nan"))

  assert normalized.shares is None
  assert normalized.source is AShareCumulativeVolumeSource.UNAVAILABLE
