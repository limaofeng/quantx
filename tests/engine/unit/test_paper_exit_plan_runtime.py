"""Engine inputs use the source PAPER ledger even after its BUY producer stops."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine import exit_plan_runtime as runtime_module
from quantx_engine.exit_plan_runtime import ExitPlanRuntime
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord

from tests.infrastructure import test_paper_exit_execution as exit_tests

allocation_sessions = exit_tests.allocation_sessions
base_sessions = exit_tests.base_sessions
ledger_sessions = exit_tests.ledger_sessions
sessions = exit_tests.sessions
local_sessions = exit_tests.local_sessions


@pytest.mark.parametrize("confirm", [False, True])
async def test_stopped_paper_source_reads_scoped_holdings_and_persisted_quote(
  sessions,
  local_sessions,
  monkeypatch,
  confirm,
):
  await exit_tests.prepared(sessions, stopped=True)
  monkeypatch.setattr(runtime_module, "AsyncSessionLocal", sessions)
  now = exit_tests.quote(2).timestamp
  monkeypatch.setattr(runtime_module.time_utils, "now", lambda: now)

  def forbidden(*args, **kwargs):
    raise AssertionError("PAPER engine must never read LIVE positions")

  monkeypatch.setattr(runtime_module, "PositionRepository", forbidden)
  scanner = SimpleNamespace(
    is_running=True,
    touch=lambda: None,
    hub=SimpleNamespace(is_ready=True, is_trading_session=AsyncMock(return_value=True)),
    snapshot_states=lambda: {"600000.SH": SimpleNamespace(current_price=999)},
  )
  runtime = ExitPlanRuntime(scanner=scanner)
  if confirm:
    service = SimpleNamespace(
      confirm_exit_intent=AsyncMock(return_value={"success": True})
    )
    monkeypatch.setattr(runtime_module, "AutoExitPlanService", lambda: service)
    assert await runtime.confirm_exit_intent(
      plan_id="paper-plan", intent_id="sell-intent"
    ) == {"success": True}
    inputs = service.confirm_exit_intent.await_args.kwargs
    position, context = inputs["position"], inputs["context"]
  else:
    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "paper-plan")
    position, context = await runtime._evaluation_inputs(
      record, states=scanner.snapshot_states()
    )
  assert position.volume == 1100 and position.can_use_volume == 1000
  assert context.current_price == 9.9 and context.bid_price == 9.89
  assert context.source == "PAPER_ACCEPTED_QUOTE"
  assert context.market_data_age_seconds == 1
  assert context.timestamp == runtime_module.time_utils.to_shanghai(now)
