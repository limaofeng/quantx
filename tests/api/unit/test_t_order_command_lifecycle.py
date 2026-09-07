from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api import agent_api
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_status", ["PARTIAL_FILLED", "EXECUTION_READY"])
async def test_t_attempt_expiry_retains_original_intent_and_exact_attempt_proof(monkeypatch, prior_status):
  scope = dict(owner_type="STRATEGY_RUN", owner_id="run-1", environment="LIVE")
  pending = SimpleNamespace(
    **scope, client_order_id="client-1", broker_order_id=None,
    intent_id="intent-1", batch_id=None, t_trade_role="ENTRY", side="BUY",
    strategy_run_id="run-1", account_id="account-1", instrument_code="600000.SH",
    status="QUEUED", status_reason=None, request_metadata={},
    t_order_original_created_at=datetime(2026, 9, 6),
  )
  intent = SimpleNamespace(
    **scope, id="intent-1", intent_metadata={}, status=prior_status, notes=None,
    executed_volume=100, executed_price=10, executed_time=datetime(2026, 9, 6),
    strategy_run_id="run-1", direction="BUY",
  )
  correlation = SimpleNamespace(
    **scope, broker_order_id=None, intent_id="intent-1", request_metadata={},
    strategy_run_id="run-1",
  )
  command = SimpleNamespace(
    client_order_id="client-1", message_id="command-1",
    delivery_status="QUEUED", delivered_at=None,
  )

  async def get(model, *_args, **_kwargs):
    return pending if model is PendingTradeOrder else intent

  db = SimpleNamespace(
    get=get, execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: correlation)),
  )
  monkeypatch.setattr(agent_api, "_durable_owner_chain_matches", lambda *_args: True)
  staged = AsyncMock(return_value=True)
  monkeypatch.setattr(agent_api, "_stage_command_runtime_event", staged)
  batch_projection = AsyncMock()
  monkeypatch.setattr(agent_api, "_project_command_batch_status", batch_projection)
  assert await agent_api._transition_place_order_command(
    db, command=command, requested_status="EXPIRED",
    reason="command_expired_before_delivery", now=datetime(2026, 9, 6),
    pre_execution_proven=True,
  )
  assert pending.status == "EXPIRED"
  assert intent.status == prior_status
  assert pending.request_metadata["execution_terminal_source"] == "LOCAL_OUTBOX_EXPIRED"
  assert pending.request_metadata["command_lifecycle_message_id"] == "command-1"
  assert intent.executed_volume == 100
  batch_projection.assert_not_awaited()
  staged.assert_awaited_once()
