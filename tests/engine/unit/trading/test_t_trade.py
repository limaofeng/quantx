from dataclasses import asdict

import quantx_domain.trading.t_trade as t_trade
from quantx_domain.trading.exit_plan import TradingCostPolicy
from quantx_domain.trading.t_trade import TickSample


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
