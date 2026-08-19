from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from quantx_engine.conditional_liquidation import ConditionalLiquidationMonitor
from quantx_infrastructure.core.data.whole_quote_hub import WholeQuoteStatus
from quantx_infrastructure.services.intraday_volume_scanner import (
  IntradayVolumeState,
)


def test_adaptive_context_projects_real_time_volume_price_and_depth():
  now = datetime(2026, 8, 13, 10, 0, 3)
  state = IntradayVolumeState(
    code="600000.SH",
    current_price=12.35,
    price_tick=0.01,
    up_stop_price=13.5,
    down_stop_price=11.05,
    volume=1_200_000,
    amount=14_700_000,
    bid_price=[12.34],
    ask_price=[12.35],
    bid_vol=[300, 200],
    ask_vol=[100, 100],
    updated_at=now - timedelta(seconds=3),
  )

  context = ConditionalLiquidationMonitor._adaptive_context(state, now=now)

  assert context.timestamp == state.updated_at
  assert context.bid_price == pytest.approx(12.34)
  assert context.ask_price == pytest.approx(12.35)
  assert context.cumulative_volume == pytest.approx(1_200_000)
  assert context.cumulative_amount == pytest.approx(14_700_000)
  assert context.depth_imbalance_5 == pytest.approx(3 / 7)
  assert context.market_data_age_seconds == pytest.approx(3)
  assert context.volume_data_age_seconds == pytest.approx(3)
  assert context.limit_down == pytest.approx(11.05)


def test_adaptive_context_pauses_when_whole_quote_is_unavailable():
  now = datetime(2026, 8, 13, 10, 0, 3)

  context = ConditionalLiquidationMonitor._adaptive_context(None, now=now)

  assert context.current_price == 0
  assert context.market_data_age_seconds > 5
  assert context.volume_data_age_seconds > 5
  assert context.source == "WHOLE_QUOTE_UNAVAILABLE"


def test_conditional_monitor_does_not_read_cached_states_while_hub_stale():
  class StaleScanner:
    hub = SimpleNamespace(is_ready=False, status=WholeQuoteStatus.STALE)

    def snapshot_states(self):
      raise AssertionError("stale cached states must not be read")

  monitor = ConditionalLiquidationMonitor(scanner=StaleScanner())

  assert monitor._ready_states() == {}
  assert monitor.market_data_gate_rejections == 1
