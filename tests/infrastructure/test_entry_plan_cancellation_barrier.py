from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from quantx_engine import report_processor
from quantx_engine.strategy_executor import StrategyExecutor
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.trade_command_service import (
  QueuedTradeCommand,
  StrategyOrderCancelRequest,
  TradeCommandService,
)


class _Result:
  def __init__(self, values):
    self._values = values

  def scalars(self):
    return SimpleNamespace(all=lambda: list(self._values))

  def scalar_one_or_none(self):
    return self._values[0] if self._values else None


def _pending(*, broker_order_id: str | None) -> SimpleNamespace:
  return SimpleNamespace(
    client_order_id="client-1",
    user_id="user-1",
    account_id="account-1",
    instrument_code="605499.SH",
    side="BUY",
    status="SUBMITTED" if broker_order_id else "QUEUED",
    status_reason=None,
    broker_order_id=broker_order_id,
    execution_mode="live",
    strategy_run_id="plan-1",
    strategy_order_id="strategy-order-1",
    intent_id="intent-1",
    request_metadata={"entry_plan_id": "plan-1"},
    last_source_sequence=0,
    last_source_event_at=None,
  )


@pytest.mark.asyncio
async def test_broker_backed_cancel_stays_requested_until_terminal_report() -> None:
  pending = _pending(broker_order_id="9001")
  db = SimpleNamespace(
    execute=AsyncMock(side_effect=[_Result([pending]), _Result([])]),
    commit=AsyncMock(),
  )
  service = TradeCommandService(db)
  service.enqueue_cancel = AsyncMock(
    return_value=QueuedTradeCommand("cancel-1", "message-1", "QUEUED")
  )

  requests = await service.request_strategy_buy_cancellations(
    strategy_run_id="plan-1",
    reason="ENTRY_PLAN_CANCELLED",
  )

  assert pending.status == "CANCEL_REQUESTED"
  assert requests[0].local_terminal is False
  service.enqueue_cancel.assert_awaited_once()
  db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_never_delivered_order_can_cancel_locally() -> None:
  pending = _pending(broker_order_id=None)
  pending.request_metadata = {}
  outbox = SimpleNamespace(client_order_id="client-1", delivery_status="QUEUED")
  db = SimpleNamespace(
    execute=AsyncMock(
      side_effect=[_Result([pending]), _Result([outbox]), _Result([])]
    ),
    get=AsyncMock(return_value=None),
    commit=AsyncMock(),
  )
  service = TradeCommandService(db)
  service.enqueue_cancel = AsyncMock()

  requests = await service.request_strategy_buy_cancellations(
    strategy_run_id="plan-1",
    reason="ENTRY_PLAN_CANCELLED",
  )

  assert pending.status == "CANCELLED"
  assert outbox.delivery_status == "CANCELLED"
  assert requests[0].local_terminal is True
  service.enqueue_cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_cancel_durably_terminalizes_managed_entry_intent() -> None:
  pending = _pending(broker_order_id=None)
  outbox = SimpleNamespace(client_order_id="client-1", delivery_status="QUEUED")
  intent = SimpleNamespace(
    strategy_run_id="plan-1",
    direction="BUY",
    status="PENDING",
    executed_volume=0,
    executed_price=None,
    executed_time=None,
    # TradeIntent.order_id is the internal strategy-order id, not proof that
    # the command reached the broker.
    order_id="strategy-order-1",
    notes=None,
    intent_metadata={"entry_plan_id": "plan-1"},
  )

  async def get(model, key, **kwargs):
    assert model is TradeIntentRecord
    assert key == "intent-1"
    assert kwargs == {"with_for_update": True}
    return intent

  db = SimpleNamespace(
    execute=AsyncMock(
      side_effect=[_Result([pending]), _Result([outbox]), _Result([])]
    ),
    get=AsyncMock(side_effect=get),
    commit=AsyncMock(),
  )
  service = TradeCommandService(db)
  service.enqueue_cancel = AsyncMock()

  requests = await service.request_strategy_buy_cancellations(
    strategy_run_id="plan-1",
    reason="ENTRY_PLAN_CANCELLED",
  )

  assert requests[0].local_terminal is True
  assert pending.status == "CANCELLED"
  assert outbox.delivery_status == "CANCELLED"
  assert intent.status == "RECONCILED_ZERO_FILL"
  assert intent.notes == "ENTRY_PLAN_CANCELLED_BEFORE_AGENT_DELIVERY"
  assert intent.intent_metadata["execution_terminal_source"] == (
    "LOCAL_OUTBOX_CANCEL"
  )
  assert requests[0].request_metadata == intent.intent_metadata
  db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "execution_fact",
  [
    {"executed_volume": 1},
    {"executed_time": object()},
    {"executed_price": 1},
  ],
)
async def test_local_cancel_does_not_claim_zero_fill_with_execution_fact(
  execution_fact,
) -> None:
  pending = _pending(broker_order_id=None)
  outbox = SimpleNamespace(client_order_id="client-1", delivery_status="QUEUED")
  intent_values = {
    "strategy_run_id": "plan-1",
    "direction": "BUY",
    "status": "PENDING",
    "executed_volume": 0,
    "executed_price": None,
    "executed_time": None,
    "order_id": None,
    "notes": None,
    "intent_metadata": {"entry_plan_id": "plan-1"},
    **execution_fact,
  }
  intent = SimpleNamespace(**intent_values)
  db = SimpleNamespace(
    execute=AsyncMock(
      side_effect=[_Result([pending]), _Result([outbox]), _Result([])]
    ),
    get=AsyncMock(return_value=intent),
    commit=AsyncMock(),
  )
  service = TradeCommandService(db)
  service.enqueue_cancel = AsyncMock()

  requests = await service.request_strategy_buy_cancellations(
    strategy_run_id="plan-1",
    reason="ENTRY_PLAN_CANCELLED",
  )

  assert requests[0].local_terminal is False
  assert pending.status == "RECONCILE_REQUIRED"
  assert outbox.delivery_status == "QUEUED"
  assert intent.status == "PENDING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "pending_fact",
  [
    {"last_source_sequence": 1},
    {"last_source_event_at": object()},
  ],
)
async def test_local_cancel_does_not_ignore_pending_broker_source_fact(
  pending_fact,
) -> None:
  pending = _pending(broker_order_id=None)
  for name, value in pending_fact.items():
    setattr(pending, name, value)
  outbox = SimpleNamespace(client_order_id="client-1", delivery_status="QUEUED")
  intent = SimpleNamespace(
    strategy_run_id="plan-1",
    direction="BUY",
    status="PENDING",
    executed_volume=0,
    executed_price=None,
    executed_time=None,
    order_id="strategy-order-1",
    notes=None,
    intent_metadata={"entry_plan_id": "plan-1"},
  )
  db = SimpleNamespace(
    execute=AsyncMock(
      side_effect=[_Result([pending]), _Result([outbox]), _Result([])]
    ),
    get=AsyncMock(return_value=intent),
    commit=AsyncMock(),
  )
  service = TradeCommandService(db)

  requests = await service.request_strategy_buy_cancellations(
    strategy_run_id="plan-1",
    reason="ENTRY_PLAN_CANCELLED",
  )

  assert requests[0].local_terminal is False
  assert pending.status == "RECONCILE_REQUIRED"
  assert outbox.delivery_status == "QUEUED"
  assert intent.status == "PENDING"


@pytest.mark.asyncio
async def test_delivered_outbox_cannot_be_terminalized_locally() -> None:
  pending = _pending(broker_order_id=None)
  outbox = SimpleNamespace(client_order_id="client-1", delivery_status="DELIVERED")
  db = SimpleNamespace(
    execute=AsyncMock(side_effect=[_Result([pending]), _Result([outbox])]),
    get=AsyncMock(),
    commit=AsyncMock(),
  )
  service = TradeCommandService(db)

  requests = await service.request_strategy_buy_cancellations(
    strategy_run_id="plan-1",
    reason="ENTRY_PLAN_CANCELLED",
  )

  assert requests[0].local_terminal is False
  assert pending.status == "CANCEL_REQUESTED"
  assert outbox.delivery_status == "DELIVERED"
  db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_execution_event_prevents_local_zero_fill_terminal() -> None:
  pending = _pending(broker_order_id=None)
  outbox = SimpleNamespace(client_order_id="client-1", delivery_status="QUEUED")
  db = SimpleNamespace(
    execute=AsyncMock(
      side_effect=[
        _Result([pending]),
        _Result([outbox]),
        _Result(["client-1"]),
      ]
    ),
    get=AsyncMock(),
    commit=AsyncMock(),
  )
  service = TradeCommandService(db)

  requests = await service.request_strategy_buy_cancellations(
    strategy_run_id="plan-1",
    reason="ENTRY_PLAN_CANCELLED",
  )

  assert requests[0].local_terminal is False
  assert pending.status == "RECONCILE_REQUIRED"
  assert outbox.delivery_status == "QUEUED"
  db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_absent_still_requests_durable_broker_cancel(
  monkeypatch,
) -> None:
  pending = _pending(broker_order_id="9001")
  pending.status = "ACCEPTED"
  database = SimpleNamespace(
    execute=AsyncMock(side_effect=[_Result([pending]), _Result([])]),
    commit=AsyncMock(),
  )
  service = TradeCommandService(database)
  service.enqueue_cancel = AsyncMock(
    return_value=QueuedTradeCommand("cancel-1", "message-1", "QUEUED")
  )

  class _Session:
    async def __aenter__(self):
      return database

    async def __aexit__(self, *_args):
      return False

  monkeypatch.setattr(
    "quantx_engine.strategy_executor.AsyncSessionLocal",
    _Session,
  )
  monkeypatch.setattr(
    "quantx_engine.strategy_executor.TradeCommandService",
    lambda _db: service,
  )
  executor = StrategyExecutor.__new__(StrategyExecutor)
  executor.runs = {}

  count = await executor.cancel_open_buy_orders(
    "plan-1",
    "ENTRY_PLAN_CANCELLED",
  )

  assert count == 1
  assert pending.status == "CANCEL_REQUESTED"
  service.enqueue_cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_cancel_notifies_strategy_as_reconciled_zero_fill(
  monkeypatch,
) -> None:
  cancellation = StrategyOrderCancelRequest(
    client_order_id="client-1",
    strategy_order_id="strategy-order-1",
    intent_id="intent-1",
    broker_order_id="",
    status="CANCELLED",
    request_metadata={
      "entry_plan_id": "plan-1",
      "execution_terminal_reason": (
        "ENTRY_PLAN_CANCELLED_BEFORE_AGENT_DELIVERY"
      ),
      "execution_terminal_source": "LOCAL_OUTBOX_CANCEL",
    },
    local_terminal=True,
  )

  class _Session:
    async def __aenter__(self):
      return SimpleNamespace()

    async def __aexit__(self, *_args):
      return False

  class _TradeCommands:
    def __init__(self, _db):
      pass

    async def request_strategy_buy_cancellations(self, **_kwargs):
      return [cancellation]

  state_manager = SimpleNamespace(
    release_order_resources=Mock(),
    update_trade_intent_status=AsyncMock(),
  )
  runtime = SimpleNamespace(
    approval_lock=asyncio.Lock(),
    state_manager=state_manager,
  )
  monkeypatch.setattr(
    "quantx_engine.strategy_executor.AsyncSessionLocal",
    _Session,
  )
  monkeypatch.setattr(
    "quantx_engine.strategy_executor.TradeCommandService",
    _TradeCommands,
  )
  executor = StrategyExecutor.__new__(StrategyExecutor)
  executor.runs = {"plan-1": runtime}
  executor._notify_strategy_order = AsyncMock()

  count = await executor.cancel_open_buy_orders(
    "plan-1",
    "ENTRY_PLAN_CANCELLED",
  )

  assert count == 1
  event = executor._notify_strategy_order.await_args.args[1]
  assert event.status == "RECONCILED_ZERO_FILL"
  state_manager.release_order_resources.assert_called_once_with(
    "strategy-order-1"
  )
  state_manager.update_trade_intent_status.assert_awaited_once_with(
    "intent-1",
    "RECONCILED_ZERO_FILL",
    metadata=cancellation.request_metadata,
    notes="ENTRY_PLAN_CANCELLED_BEFORE_AGENT_DELIVERY",
  )


class _ReportDb:
  def __init__(self, pending: SimpleNamespace) -> None:
    self.pending = pending
    self.commit = AsyncMock()

  async def __aenter__(self):
    return self

  async def __aexit__(self, *_args):
    return False

  async def get(self, _model, _key):
    return self.pending

  async def execute(self, _statement):
    return _Result([])


@pytest.mark.asyncio
async def test_late_fill_does_not_finish_cancel_before_broker_terminal(
  monkeypatch,
) -> None:
  pending = _pending(broker_order_id=None)
  pending.status = "CANCEL_REQUESTED"
  database = _ReportDb(pending)
  enqueue_cancel = AsyncMock(
    return_value=QueuedTradeCommand("cancel-1", "message-1", "QUEUED")
  )

  class _TradeCommands:
    def __init__(self, _db):
      pass

    async def enqueue_cancel(self, **kwargs):
      return await enqueue_cancel(**kwargs)

  monkeypatch.setattr(report_processor, "AsyncSessionLocal", lambda: database)
  monkeypatch.setattr(report_processor, "TradeCommandService", _TradeCommands)

  await report_processor._update_pending(
    "client-1",
    status="PARTIAL_FILLED",
    broker_order_id="9001",
    source_sequence=2,
  )

  assert pending.status == "CANCEL_REQUESTED"
  assert pending.broker_order_id == "9001"
  enqueue_cancel.assert_awaited_once()

  await report_processor._update_pending(
    "client-1",
    status="CANCELLED",
    broker_order_id="9001",
    source_sequence=3,
  )

  assert pending.status == "CANCELLED"
  assert enqueue_cancel.await_count == 1
