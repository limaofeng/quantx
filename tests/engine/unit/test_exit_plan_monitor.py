from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine import exit_plan_monitor as monitor_module
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


@pytest.mark.asyncio
async def test_monitor_only_evaluates_manual_plans(monkeypatch) -> None:
  def plan_state(
    *,
    plan_id: str,
    source_type: str,
    run_id: str = "",
    metadata: dict | None = None,
  ) -> dict:
    return {
      "template": {
        "plan_id": plan_id,
        "account_id": "account-1",
        "instrument_code": "600000.SH",
        "source_type": source_type,
        "run_id": run_id,
        "metadata": dict(metadata or {}),
      }
    }

  manual_plan = SimpleNamespace(
    plan_id="manual-plan",
    enabled=True,
    strategy_run_id=None,
    source_type="MANUAL_LIQUIDATION",
    plan_state=plan_state(
      plan_id="manual-plan",
      source_type="MANUAL_LIQUIDATION",
    ),
    last_error=None,
    account_id="account-1",
    instrument_code="600000.SH",
  )
  strategy_plan = SimpleNamespace(
    plan_id="strategy-plan",
    enabled=True,
    strategy_run_id="run-1",
    source_type="T_TRADE_BATCH",
    plan_state=plan_state(
      plan_id="strategy-plan",
      source_type="T_TRADE_BATCH",
      run_id="run-1",
    ),
    last_error=None,
    account_id="account-1",
    instrument_code="600000.SH",
  )
  orphan_t_plan = SimpleNamespace(
    plan_id="orphan-t-plan",
    enabled=True,
    strategy_run_id=None,
    source_type="T_TRADE_BATCH",
    plan_state=plan_state(
      plan_id="orphan-t-plan",
      source_type="T_TRADE_BATCH",
    ),
    last_error=None,
    account_id="account-1",
    instrument_code="600000.SH",
  )
  unbound_dedicated_plan = SimpleNamespace(
    plan_id="unbound-dedicated-plan",
    enabled=True,
    strategy_run_id=None,
    source_type="MANUAL_POSITION",
    plan_state=plan_state(
      plan_id="unbound-dedicated-plan",
      source_type="MANUAL_POSITION",
      metadata={"managed_runtime_command_id": ""},
    ),
    last_error=None,
    account_id="account-1",
    instrument_code="600000.SH",
  )

  class Session:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

  class PlanRepository:
    def __init__(self, _db):
      pass

    async def find_active(self, **_kwargs):
      return [
        strategy_plan,
        orphan_t_plan,
        unbound_dedicated_plan,
        manual_plan,
      ]

  class Positions:
    def __init__(self, _db):
      pass

    async def find_by_stock_code(self, *_args, **_kwargs):
      return SimpleNamespace(volume=100, can_use_volume=100)

  now = datetime(2026, 8, 19, 10, 0)
  hub = SimpleNamespace(
    is_ready=True,
    status=WholeQuoteStatus.READY,
    is_trading_session=AsyncMock(return_value=True),
  )
  scanner = SimpleNamespace(
    is_running=True,
    hub=hub,
    touch=lambda: None,
    snapshot_states=lambda: {
      "600000.SH": SimpleNamespace(
        updated_at=now,
        current_price=10.0,
        bid_price=[9.99],
        ask_price=[10.0],
        bid_vol=[100],
        ask_vol=[100],
        up_stop_price=11.0,
        down_stop_price=9.0,
        price_tick=0.01,
        volume=1_000,
        amount=10_000,
      )
    },
  )
  evaluate_and_submit = AsyncMock(return_value={"success": True})
  service = SimpleNamespace(evaluate_and_submit=evaluate_and_submit)
  monkeypatch.setattr(monitor_module, "AsyncSessionLocal", lambda: Session())
  monkeypatch.setattr(monitor_module, "AutoExitPlanRepository", PlanRepository)
  monkeypatch.setattr(monitor_module, "PositionRepository", Positions)
  monkeypatch.setattr(monitor_module, "AutoExitPlanService", lambda: service)

  results = await ExitPlanMonitor(scanner=scanner).evaluate_all_active_plans()

  # Legacy managed-runtime markers are migration inputs and never hide an
  # unbound manual plan from its sole PAPER/LIVE consumer.
  assert [result["plan_id"] for result in results] == [
    "unbound-dedicated-plan",
    "manual-plan",
  ]
  assert evaluate_and_submit.await_count == 2
  assert evaluate_and_submit.await_args_list[-1].kwargs["plan_id"] == "manual-plan"
