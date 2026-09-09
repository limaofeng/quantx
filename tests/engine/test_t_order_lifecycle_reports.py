from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.trading.exit_plan import ExitPlanBook, ExitPlanTemplate, ExitRuleSpec
from quantx_engine import report_processor as reports
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord


def rows(values):
  return SimpleNamespace(all=lambda: values)


def attempt(index, volume):
  return SimpleNamespace(
    client_order_id=f"client-{index}", broker_order_id=str(101 + index),
    t_order_attempt=index, t_order_parent_client_id=f"client-{index - 1}" if index else None,
    t_order_original_created_at=datetime(2026, 9, 6), request_metadata={},
    owner_type="STRATEGY_RUN", owner_id="run-1", environment="LIVE",
    account_id="account-1", intent_id="intent-1", instrument_code="600000.SH",
    side="BUY", t_trade_role="ENTRY", volume=volume, status="CANCELLED",
    bucket="swing", batch_id="batch-1",
  )


def finalizer_db(*, missing_fill=False, unapplied=False):
  attempts = [attempt(0, 300), attempt(1, 200)]
  intent = SimpleNamespace(
    id="intent-1", owner_type="STRATEGY_RUN", owner_id="run-1", environment="LIVE",
    account_id="account-1", instrument_code="600000.SH", direction="BUY",
    executed_volume=300, status="PARTIAL_FILLED",
  )
  correlations = [SimpleNamespace(
    **{key: getattr(row, key) for key in (
      "client_order_id", "broker_order_id", "intent_id", "account_id",
      "owner_type", "owner_id", "environment",
    )}, strategy_order_id="strategy-order", request_metadata={}, batch_id="batch-1",
    bucket="swing", t_trade_role="ENTRY", risk_decision_id="risk-1", trace_id="trace-1",
    substitution_plan=None,
  ) for row in attempts]
  orders = {
    101: SimpleNamespace(account_id="account-1", stock_code="600000.SH", volume=300, status=54, traded_volume=100),
    102: SimpleNamespace(account_id="account-1", stock_code="600000.SH", volume=200, status=56, traded_volume=200),
  }

  async def get(model, key, **_kwargs):
    if model is TradeIntentRecord:
      return intent
    if model is Order:
      return orders[key]
    return None

  added = []
  db = SimpleNamespace(
    get=get, add=added.append,
    correlations=correlations,
    scalar=AsyncMock(side_effect=[correlations[0], "unapplied" if unapplied else None, correlations[1], None]),
    scalars=AsyncMock(side_effect=[rows(attempts), rows([SimpleNamespace(volume=100)]), rows([SimpleNamespace(volume=100 if missing_fill else 200)])]),
  )
  return db, attempts, intent, added


@pytest.mark.asyncio
async def test_lifecycle_finalizer_aggregates_attempts_and_stages_single_terminal():
  db, attempts, intent, added = finalizer_db()
  assert await reports.finalize_t_order_lifecycle(db, attempts[-1]) is True
  assert intent.status == "FILLED"
  assert all(row.request_metadata["t_order_lifecycle_finished"] for row in attempts)
  assert len(added) == 1
  report = added[0].payload["report"]
  assert report["traded_volume"] == 300
  assert report["order_volume"] == 300
  assert report["t_order_lifecycle_finalized"] is True
  assert await reports.finalize_t_order_lifecycle(db, attempts[-1]) is False
  assert len(added) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_fill,unapplied", [(True, False), (False, True)])
async def test_lifecycle_finalizer_does_not_release_unconverged_attempt(missing_fill, unapplied):
  db, attempts, intent, added = finalizer_db(missing_fill=missing_fill, unapplied=unapplied)
  assert await reports.finalize_t_order_lifecycle(db, attempts[-1]) is False
  assert added == []
  assert intent.status == "PARTIAL_FILLED"
  assert not attempts[-1].request_metadata


@pytest.mark.asyncio
async def test_attempt_terminal_uses_only_its_own_execution_reports():
  pending = attempt(1, 200)
  events = [SimpleNamespace(payload={"report": {"traded_volume": 50}})]
  db = SimpleNamespace(
    get=AsyncMock(return_value=pending),
    execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: events)),
  )
  projection = await reports._terminal_order_fill_projection(
    db, SimpleNamespace(client_order_id="client-1", t_trade_role="ENTRY"),
    SimpleNamespace(executed_volume=150, target_volume=300),
    current_order={"status": "CANCELLED", "traded_volume": 100},
  )
  assert projection["received"] == 50
  assert projection["expected"] == 100


@pytest.mark.asyncio
async def test_lifecycle_aggregate_terminal_is_not_compared_to_last_attempt_fills():
  db = SimpleNamespace(get=AsyncMock(return_value=attempt(1, 200)))
  projection = await reports._terminal_order_fill_projection(
    db, SimpleNamespace(client_order_id="client-1", t_trade_role="ENTRY"),
    SimpleNamespace(executed_volume=300),
    current_order={"effective_order_status": "FILLED", "traded_volume": 300,
                   "t_order_lifecycle_finalized": True},
  )
  assert projection["received"] == projection["expected"] == 300


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_status", ["PARTIAL_FILLED", "EXECUTION_READY"])
async def test_old_attempt_terminal_keeps_original_intent_active(monkeypatch, prior_status):
  pending = attempt(0, 300)
  intent = SimpleNamespace(
    intent_metadata={}, order_id="strategy-order", executed_volume=100,
    status=prior_status, notes=None,
  )

  async def get(model, *_args, **_kwargs):
    return pending if model is PendingTradeOrder else intent

  monkeypatch.setattr(reports, "_terminal_order_fill_projection", AsyncMock(return_value={
    "status": "CANCELLED", "expected": 100, "received": 100, "role": "ENTRY",
  }))
  item = {"status": "CANCELLED", "traded_volume": 100}
  await reports._project_trade_intent_event(
    SimpleNamespace(get=get),
    SimpleNamespace(intent_id="intent-1", client_order_id="client-0", strategy_order_id="strategy-order", risk_decision_id=None),
    event_type="ORDER", item=item,
  )
  # An open lifecycle is nonterminal, but its already-received fills must remain visible.
  assert intent.status == "PARTIAL_FILLED"
  assert item == {"status": "CANCELLED", "traded_volume": 100}


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [None, "client", "delivered", "owner", "missing"])
async def test_local_attempt_finalization_requires_exact_outbox_zero_proof(conflict):
  db, attempts, intent, added = finalizer_db()
  last = attempts[-1]
  last.broker_order_id = None
  last.status = "EXPIRED"
  last.request_metadata = {
    "execution_terminal_source": "LOCAL_OUTBOX_EXPIRED",
    "command_lifecycle_message_id": "command-last",
  }
  db.correlations[-1].broker_order_id = None
  intent.executed_volume = 100
  command = SimpleNamespace(
    message_id="command-last", client_order_id=last.client_order_id,
    account_id=last.account_id, owner_type=last.owner_type, owner_id=last.owner_id,
    environment=last.environment, delivery_status="EXPIRED", delivered_at=None,
    acknowledged_at=None, expires_at=datetime(2026, 1, 1),
    payload={
      "command_kind": "PLACE_ORDER", "client_order_id": last.client_order_id,
      "account_id": last.account_id, "instrument_code": last.instrument_code,
      "side": last.side, "volume": last.volume,
    },
  )
  if conflict == "client":
    command.client_order_id = "different-attempt"
  elif conflict == "delivered":
    command.delivered_at = datetime(2026, 1, 1)
  elif conflict == "owner":
    command.owner_id = "another-run"
  original_get = db.get

  async def get(model, key, **kwargs):
    if model is TradeCommandOutbox:
      return None if conflict == "missing" else command
    return await original_get(model, key, **kwargs)

  db.get = get
  assert await reports.finalize_t_order_lifecycle(db, last) is (conflict is None)
  assert len(added) == (1 if conflict is None else 0)
  if conflict is None:
    assert added[0].payload["report"]["traded_volume"] == 100
    assert intent.status == "CANCELLED"


@pytest.mark.asyncio
async def test_all_attempts_converged_finalize_public_exit_plan_pending(monkeypatch):
  db, attempts, intent, added = finalizer_db()
  for row in [*attempts, *db.correlations, intent]:
    row.owner_type = "EXIT_PLAN"
  for row in attempts:
    row.side = "SELL"
    row.t_trade_role = "EXIT"
  intent.direction = "SELL"
  plan = ExitPlanBook().register_entry_fill(
    ExitPlanTemplate(
      plan_id="run-1", account_id="account-1", instrument_code="600000.SH",
      source_type="MANUAL_POSITION", source_id="source-1", bucket="swing",
      rules=[ExitRuleSpec(rule_id="rule-1", strategy="HARD_STOP")],
    ), volume=300, price=10,
  )
  plan.pending_intent_id = "intent-1"
  plan.pending_requested_volume = 300
  plan.pending_filled_volume = 300
  plan.exited_volume = 300
  record = SimpleNamespace(
    account_id="account-1", instrument_code="600000.SH", environment="LIVE",
    plan_state=plan.to_dict(),
  )
  original_get = db.get

  async def get(model, key, **kwargs):
    return record if model is AutoExitPlanRecord else await original_get(model, key, **kwargs)

  db.get = get
  monkeypatch.setattr(reports.AutoExitPlanService, "_sync_record", staticmethod(
    lambda row, value: setattr(row, "plan_state", value.to_dict())
  ))
  event = AsyncMock()
  monkeypatch.setattr(reports.AutoExitPlanService, "_append_event", event)
  assert await reports.finalize_t_order_lifecycle(db, attempts[-1]) is True
  assert record.plan_state["pending_intent_id"] == ""
  assert record.plan_state["status"] == "COMPLETED"
  assert added == []
  event.assert_awaited_once()
