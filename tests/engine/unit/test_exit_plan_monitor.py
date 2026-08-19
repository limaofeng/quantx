from datetime import datetime
from types import SimpleNamespace

from quantx_engine.exit_plan_monitor import ExitPlanMonitor
from quantx_infrastructure.core.data.whole_quote_hub import WholeQuoteStatus


def test_exit_plan_monitor_does_not_read_cached_states_while_hub_stale() -> None:
  class StaleScanner:
    hub = SimpleNamespace(is_ready=False, status=WholeQuoteStatus.STALE)

    def snapshot_states(self):
      raise AssertionError("stale cached states must not be read")

  monitor = ExitPlanMonitor(scanner=StaleScanner())

  states = monitor._ready_states()
  context = monitor.context_from_state(
    states.get("600000.SH"),
    now=datetime(2026, 8, 19, 10, 0),
  )

  assert context.source == "WHOLE_QUOTE_UNAVAILABLE"
  assert context.market_data_age_seconds == 999.0
  assert monitor.market_data_gate_rejections == 1
