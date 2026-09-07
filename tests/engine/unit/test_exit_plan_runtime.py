from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine import exit_plan_runtime as runtime_module
from quantx_engine.exit_plan_runtime import ExitPlanRuntime
from quantx_infrastructure.core.data.whole_quote_hub import WholeQuoteStatus


def test_exit_plan_runtime_does_not_read_cached_states_while_hub_stale() -> None:
  class StaleScanner:
    hub = SimpleNamespace(is_ready=False, status=WholeQuoteStatus.STALE)

    def snapshot_states(self):
      raise AssertionError("stale cached states must not be read")

  runtime = ExitPlanRuntime(scanner=StaleScanner())

  states = runtime._ready_states()
  context = runtime.context_from_state(
    states.get("600000.SH"),
    now=datetime(2026, 8, 19, 10, 0),
  )

  assert context.source == "WHOLE_QUOTE_UNAVAILABLE"
  assert context.market_data_age_seconds == 999.0
  assert runtime.market_data_gate_rejections == 1


@pytest.mark.asyncio
async def test_runtime_evaluates_every_valid_source_independent_of_source_run(
  monkeypatch,
) -> None:
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
        "source_id": plan_id,
        "run_id": run_id,
        "metadata": dict(metadata or {}),
      }
    }

  manual_plan = SimpleNamespace(
    plan_id="manual-plan",
    enabled=True,
    strategy_run_id=None,
    source_type="MANUAL_LIQUIDATION",
    source_id="manual-plan",
    group_id="manual-group",
    source_execution_owner_type="MANUAL_COMMAND",
    source_execution_owner_id="manual-group",
    source_execution_environment="PAPER",
    environment="PAPER",
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
    source_id="strategy-plan",
    group_id=None,
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id="run-1",
    source_execution_environment="PAPER",
    environment="PAPER",
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
    source_id="orphan-t-plan",
    group_id=None,
    source_execution_owner_type="T_ASSISTANT_EXECUTION",
    source_execution_owner_id="execution-missing-template-binding",
    source_execution_environment="PAPER",
    environment="PAPER",
    plan_state=plan_state(
      plan_id="orphan-t-plan",
      source_type="T_TRADE_BATCH",
    ),
    last_error=None,
    account_id="account-1",
    instrument_code="600000.SH",
  )
  independent_t_plan = SimpleNamespace(
    plan_id="independent-t-plan",
    enabled=True,
    strategy_run_id=None,
    source_type="T_TRADE_BATCH",
    source_id="independent-t-plan",
    group_id=None,
    source_execution_owner_type="T_ASSISTANT_EXECUTION",
    source_execution_owner_id="terminal-t-execution",
    source_execution_environment="PAPER",
    environment="PAPER",
    plan_state=plan_state(
      plan_id="independent-t-plan",
      source_type="T_TRADE_BATCH",
      metadata={
        "source_execution_owner_type": "T_ASSISTANT_EXECUTION",
        "source_execution_owner_id": "terminal-t-execution",
        "source_execution_environment": "PAPER",
      },
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
    source_id="unbound-dedicated-plan",
    group_id=None,
    source_execution_owner_type="MANUAL_COMMAND",
    source_execution_owner_id="unbound-dedicated-plan",
    source_execution_environment="PAPER",
    environment="PAPER",
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
        independent_t_plan,
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
  monkeypatch.setattr(runtime_module, "AsyncSessionLocal", lambda: Session())
  monkeypatch.setattr(runtime_module, "AutoExitPlanRepository", PlanRepository)
  monkeypatch.setattr(runtime_module, "PositionRepository", Positions)
  monkeypatch.setattr(runtime_module, "AutoExitPlanService", lambda: service)

  results = await ExitPlanRuntime(scanner=scanner).evaluate_all_active_plans()

  # Legacy managed-runtime markers are migration inputs and never hide an
  # unbound manual plan from its sole PAPER/LIVE consumer.
  assert [result["plan_id"] for result in results] == [
    "independent-t-plan",
    "manual-plan",
    "strategy-plan",
    "unbound-dedicated-plan",
  ]
  assert evaluate_and_submit.await_count == 4
  assert evaluate_and_submit.await_args_list[2].kwargs["plan_id"] == "strategy-plan"


@pytest.mark.asyncio
async def test_damaged_first_plan_does_not_starve_later_exit(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  def plan(plan_id: str):
    return SimpleNamespace(
      plan_id=plan_id,
      enabled=True,
      strategy_run_id=None,
      source_type="MANUAL_POSITION",
      source_id=plan_id,
      group_id=None,
      source_execution_owner_type="MANUAL_COMMAND",
      source_execution_owner_id=plan_id,
      source_execution_environment="PAPER",
      environment="PAPER",
      plan_state={
        "template": {
          "plan_id": plan_id,
          "account_id": "account-1",
          "instrument_code": "600000.SH",
          "source_type": "MANUAL_POSITION",
          "source_id": plan_id,
          "metadata": {},
        }
      },
      last_error=None,
      account_id="account-1",
      instrument_code="600000.SH",
      created_at=datetime(2026, 9, 3, 9),
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
      return [plan("a-broken"), plan("b-healthy")]

  class Positions:
    def __init__(self, _db):
      pass

    async def find_by_stock_code(self, *_args, **_kwargs):
      return SimpleNamespace(volume=100, can_use_volume=100)

  now = datetime(2026, 9, 3, 10)
  scanner = SimpleNamespace(
    is_running=True,
    hub=SimpleNamespace(
      is_ready=True,
      status=WholeQuoteStatus.READY,
      is_trading_session=AsyncMock(return_value=True),
    ),
    touch=lambda: None,
    snapshot_states=lambda: {
      "600000.SH": SimpleNamespace(
        updated_at=now,
        current_price=10,
        bid_price=[9.99],
        ask_price=[10],
        bid_vol=[100],
        ask_vol=[100],
        up_stop_price=11,
        down_stop_price=9,
        price_tick=0.01,
        volume=1000,
        amount=10000,
      )
    },
  )
  evaluate = AsyncMock(
    side_effect=[RuntimeError("corrupted plan"), {"success": True}]
  )
  record_failure = AsyncMock()
  service = SimpleNamespace(
    evaluate_and_submit=evaluate,
    record_runtime_evaluation_failure=record_failure,
  )
  monkeypatch.setattr(runtime_module, "AsyncSessionLocal", lambda: Session())
  monkeypatch.setattr(runtime_module, "AutoExitPlanRepository", PlanRepository)
  monkeypatch.setattr(runtime_module, "PositionRepository", Positions)
  monkeypatch.setattr(runtime_module, "AutoExitPlanService", lambda: service)
  runtime = ExitPlanRuntime(scanner=scanner)

  results = await runtime.evaluate_all_active_plans()

  assert [item["plan_id"] for item in results] == ["a-broken", "b-healthy"]
  assert results[0]["result"]["error"] == "EXIT_PLAN_RUNTIME_EVALUATION_FAILED"
  assert results[1]["submitted"] is True
  assert runtime.plan_evaluation_failures == 1
  record_failure.assert_awaited_once()
