from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.trading.exit_plan import (
  ExitPlan,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)
from quantx_engine import report_processor
from quantx_engine.strategy_manager import strategy_manager
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_execution_quarantine_service import (
  AccountExecutionQuarantineService,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _owner_value(owner_type: ExecutionOwnerType | str) -> str:
  return getattr(owner_type, "value", owner_type)


def _runtime_event(
  *,
  event_id: str,
  owner_type: ExecutionOwnerType | str,
  owner_id: str,
  client_order_id: str,
  broker_order_id: str,
  event_type: str = "ORDER",
) -> SimpleNamespace:
  owner_type_value = _owner_value(owner_type)
  return SimpleNamespace(
    event_id=event_id,
    event_type=event_type,
    owner_type=owner_type_value,
    owner_id=owner_id,
    environment=ExecutionEnvironment.PAPER.value,
    strategy_run_id=(owner_id if owner_type_value == "STRATEGY_RUN" else None),
    client_order_id=client_order_id,
    broker_order_id=broker_order_id,
    payload={
      # These direct payload identities are intentionally wrong.  The staged
      # StrategyRuntimeEvent columns must remain authoritative.
      "client_order_id": "payload-cross-owner",
      "broker_order_id": "payload-cross-broker",
      "report": {
        "order_id": broker_order_id,
        "account_id": "account-1",
        "stock_code": "600000.SH",
        "order_status": 49,
        "order_volume": 100,
        "traded_volume": 0,
        "traded_price": 10.0,
      },
      "metadata": {
        "owner_type": "MANUAL_COMMAND",
        "owner_id": "metadata-cross-owner",
        "strategy_run_id": "metadata-cross-run",
        "strategy_order_id": "metadata-cross-order",
        "intent_id": "metadata-cross-intent",
        "instrument_code": "000001.SZ",
        "requested_entry_volume": 999,
      },
    },
  )


def _exit_plan_state(plan_id: str) -> dict:
  plan = ExitPlan(
    template=ExitPlanTemplate(
      plan_id=plan_id,
      source_type="MANUAL_POSITION",
      source_id="position-1",
      account_id="account-1",
      instrument_code="600000.SH",
      bucket="manual",
      rules=[
        ExitRuleSpec(
          rule_id=f"{plan_id}:target",
          strategy=ExitRuleType.TARGET_PRICE,
          parameters={"target_price": 11.0},
        )
      ],
    ),
    status=ExitPlanStatus.ACTIVE,
    entry_filled_volume=100,
    entry_avg_price=10.0,
  )
  return plan.to_dict()


@pytest.fixture
async def owner_runtime_database(monkeypatch: pytest.MonkeyPatch):
  database = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    PendingTradeOrder.__table__,
    OrderCorrelation.__table__,
    AutoExitPlanRecord.__table__,
    TradeIntentRecord.__table__,
    StrategyRuntimeEvent.__table__,
  ]
  async with database.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=tables,
      )
    )
  sessions = async_sessionmaker(database, expire_on_commit=False)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
  try:
    yield sessions
  finally:
    await database.dispose()


async def _seed_owner_rows(sessions) -> None:
  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="owner-runtime-user",
        display_name="Owner Runtime User",
        password_hash="unused",
        permissions=[],
      )
    )
    db.add(
      AutoExitPlanRecord(
        plan_id="plan-1",
        account_id="account-1",
        instrument_code="600000.SH",
        bucket="manual",
        source_type="MANUAL_POSITION",
        source_id="position-1",
        source_execution_owner_type="MANUAL_COMMAND",
        source_execution_owner_id="source-command-1",
        source_execution_environment="PAPER",
        enabled=True,
        status="ACTIVE",
        environment="PAPER",
        auto_exit_authorized=False,
        config_version=1,
        state_version=1,
        protected_volume=100,
        exited_volume=0,
        remaining_volume=100,
        entry_avg_price=10.0,
        plan_state=_exit_plan_state("plan-1"),
        pending_client_order_id="client-exit",
      )
    )
    db.add(
      TradeIntentRecord(
        id="intent-exit",
        owner_type="EXIT_PLAN",
        owner_id="plan-1",
        environment="PAPER",
        idempotency_key="exit-intent-1",
        account_id="account-1",
        instrument_code="600000.SH",
        direction="SELL",
        bucket="manual",
        intent_metadata={"exit_plan_id": "plan-1"},
      )
    )
    db.add(
      TradeIntentRecord(
        id="intent-strategy",
        owner_type="STRATEGY_RUN",
        owner_id="run-1",
        environment="PAPER",
        idempotency_key="strategy-intent-1",
        account_id="account-1",
        instrument_code="600000.SH",
        direction="BUY",
        bucket="manual",
        intent_metadata={},
      )
    )
    for client_order_id, owner_type, owner_id, broker_order_id, intent_id in (
      (
        "client-exit",
        "EXIT_PLAN",
        "plan-1",
        "broker-exit",
        "intent-exit",
      ),
      (
        "client-manual",
        "MANUAL_COMMAND",
        "manual-1",
        "broker-manual",
        "intent-manual",
      ),
    ):
      db.add(
        PendingTradeOrder(
          client_order_id=client_order_id,
          user_id="user-1",
          account_id="account-1",
          owner_type=owner_type,
          owner_id=owner_id,
          environment="PAPER",
          instrument_code="600000.SH",
          side="SELL",
          order_type="LIMIT",
          limit_price="10.0",
          volume=100,
          status="SUBMITTED",
          broker_order_id=broker_order_id,
          strategy_order_id=None,
          intent_id=intent_id if owner_type == "EXIT_PLAN" else None,
          bucket="manual",
          request_metadata={},
        )
      )
      db.add(
        OrderCorrelation(
          id=f"correlation-{owner_type.lower()}",
          client_order_id=client_order_id,
          broker_order_id=broker_order_id,
          account_id="account-1",
          owner_type=owner_type,
          owner_id=owner_id,
          environment="PAPER",
          strategy_order_id=None,
          intent_id=intent_id if owner_type == "EXIT_PLAN" else None,
          bucket="manual",
          trace_id=f"trace-{owner_type.lower()}",
          request_metadata={},
        )
      )
    db.add(
      PendingTradeOrder(
        client_order_id="client-strategy",
        user_id="user-1",
        account_id="account-1",
        owner_type="STRATEGY_RUN",
        owner_id="run-1",
        environment="PAPER",
        instrument_code="600000.SH",
        side="BUY",
        order_type="LIMIT",
        limit_price="10.0",
        volume=100,
        status="SUBMITTED",
        broker_order_id="broker-strategy",
        strategy_run_id="run-1",
        strategy_order_id="strategy-order-1",
        intent_id="intent-strategy",
        bucket="manual",
        request_metadata={},
      )
    )
    db.add(
      OrderCorrelation(
        id="correlation-strategy",
        client_order_id="client-strategy",
        broker_order_id="broker-strategy",
        account_id="account-1",
        owner_type="STRATEGY_RUN",
        owner_id="run-1",
        environment="PAPER",
        strategy_run_id="run-1",
        strategy_order_id="strategy-order-1",
        intent_id="intent-strategy",
        bucket="manual",
        trace_id="trace-strategy",
        request_metadata={},
      )
    )
    await db.commit()


def test_source_owner_environment_is_canonical_and_conflicts_fail_closed() -> None:
  matching = SimpleNamespace(
    source_execution_owner_type="MANUAL_COMMAND",
    source_execution_owner_id="source-command-1",
    source_execution_environment="PAPER",
    environment="PAPER",
    strategy_run_id=None,
  )
  assert report_processor._durable_owner_triple(matching) == (
    "MANUAL_COMMAND",
    "source-command-1",
    "PAPER",
  )

  mixed_environment = SimpleNamespace(
    source_execution_owner_type="MANUAL_COMMAND",
    source_execution_owner_id="source-command-1",
    source_execution_environment="LIVE",
    environment="PAPER",
    strategy_run_id=None,
  )
  assert report_processor._durable_owner_triple(mixed_environment) is None

  incomplete_source = SimpleNamespace(
    source_execution_owner_type="MANUAL_COMMAND",
    source_execution_owner_id="source-command-1",
    source_execution_environment=None,
    owner_type="EXIT_PLAN",
    owner_id="plan-1",
    environment="PAPER",
    strategy_run_id=None,
  )
  assert report_processor._durable_owner_triple(incomplete_source) is None

  # The typed triple is sufficient for routing; the optional denormalized
  # strategy_run_id must not be a hidden owner source or a required field.
  typed_strategy_without_witness = SimpleNamespace(
    owner_type="STRATEGY_RUN",
    owner_id="run-1",
    environment="PAPER",
    strategy_run_id=None,
  )
  assert report_processor._durable_owner_triple(typed_strategy_without_witness) == (
    "STRATEGY_RUN",
    "run-1",
    "PAPER",
  )


def test_metadata_cannot_create_or_redirect_exit_plan_binding() -> None:
  pending = SimpleNamespace(
    owner_type="EXIT_PLAN",
    owner_id="plan-1",
    environment="PAPER",
    side="SELL",
    request_metadata={
      "owner_type": "MANUAL_COMMAND",
      "owner_id": "manual-1",
      "exit_plan_id": "manual-1",
    },
  )
  correlation = SimpleNamespace(
    owner_type="EXIT_PLAN",
    owner_id="plan-1",
    environment="PAPER",
    strategy_run_id=None,
  )
  assert (
    report_processor._durable_exit_plan_sell_binding(pending, correlation) is None
  )

  metadata_only = SimpleNamespace(
    owner_type="MANUAL_COMMAND",
    owner_id="manual-1",
    environment="PAPER",
    side="SELL",
    request_metadata={"exit_plan_id": "plan-1"},
  )
  assert report_processor._durable_exit_plan_sell_binding(metadata_only) is False


def test_runtime_intent_binding_requires_exact_durable_intent_witness() -> None:
  pending = SimpleNamespace(
    owner_type="EXIT_PLAN",
    owner_id="plan-1",
    environment="PAPER",
    account_id="account-1",
    instrument_code="600000.SH",
    side="SELL",
    intent_id="intent-exit",
  )
  correlation = SimpleNamespace(
    owner_type="EXIT_PLAN",
    owner_id="plan-1",
    environment="PAPER",
    account_id="account-1",
    client_order_id="client-exit",
    intent_id="intent-exit",
    strategy_run_id=None,
  )
  assert report_processor._exact_intent_binding(pending, correlation, None) is False
  wrong_owner_intent = SimpleNamespace(
    id="intent-exit",
    owner_type="MANUAL_COMMAND",
    owner_id="manual-1",
    environment="PAPER",
    strategy_run_id=None,
    account_id="account-1",
    instrument_code="600000.SH",
    direction="SELL",
  )
  assert (
    report_processor._exact_intent_binding(
      pending,
      correlation,
      wrong_owner_intent,
    )
    is False
  )


def test_trade_business_key_is_owner_and_client_scoped() -> None:
  item = {"order_id": "broker-1", "execution_id": "execution-1"}
  strategy_correlation = SimpleNamespace(
    owner_type="STRATEGY_RUN",
    owner_id="run-1",
    environment="PAPER",
    client_order_id="client-strategy",
  )
  exit_correlation = SimpleNamespace(
    owner_type="EXIT_PLAN",
    owner_id="plan-1",
    environment="PAPER",
    client_order_id="client-exit",
  )

  strategy_key = report_processor._runtime_business_key(
    "TRADE", strategy_correlation, item
  )
  assert strategy_key == report_processor._runtime_business_key(
    "TRADE", strategy_correlation, item
  )
  assert strategy_key != report_processor._runtime_business_key(
    "TRADE", exit_correlation, item
  )
  long_correlation = SimpleNamespace(
    owner_type="STRATEGY_RUN",
    owner_id="run-" + "x" * 120,
    environment="PAPER",
    account_id="account-1",
    client_order_id="client-" + "x" * 120,
  )
  long_item_a = {"order_id": "broker-1", "execution_id": "execution-" + "x" * 120 + "a"}
  long_item_b = {"order_id": "broker-1", "execution_id": "execution-" + "x" * 120 + "b"}
  long_key = report_processor._runtime_business_key(
    "TRADE", long_correlation, long_item_a
  )
  assert long_key == report_processor._runtime_business_key(
    "TRADE", long_correlation, long_item_a
  )
  assert long_key != report_processor._runtime_business_key(
    "TRADE", long_correlation, long_item_b
  )


def test_runtime_payload_does_not_duplicate_owner_run_identity() -> None:
  correlation = SimpleNamespace(
    owner_type="STRATEGY_RUN",
    owner_id="run-1",
    environment="PAPER",
    strategy_order_id="strategy-order-1",
    intent_id="intent-strategy",
    batch_id=None,
    bucket="manual",
    t_trade_role=None,
    risk_decision_id=None,
    trace_id="trace-strategy",
    substitution_plan=None,
    request_metadata={
      "owner_type": "MANUAL_COMMAND",
      "owner_id": "metadata-owner",
      "environment": "LIVE",
      "strategy_run_id": "metadata-run",
      "execution_owner_type": "MANUAL_COMMAND",
      "execution_owner_id": "metadata-owner",
      "source_execution_owner_type": "MANUAL_COMMAND",
      "source_execution_owner_id": "metadata-owner",
      "source_execution_environment": "LIVE",
      "run_id": "metadata-run",
      "runtime_run_id": "metadata-run",
      "client_order_id": "payload-client",
      "broker_order_id": "payload-broker",
      "candidate_id": "candidate-1",
    },
  )

  metadata = report_processor._event_payload(
    correlation,
    {"order_status": 49},
    business_key="order:client-strategy",
  )["metadata"]

  assert all(
    key not in metadata
    for key in (
      "owner_type",
      "owner_id",
      "environment",
      "execution_owner_type",
      "execution_owner_id",
      "source_execution_owner_type",
      "source_execution_owner_id",
      "source_execution_environment",
      "run_id",
      "runtime_run_id",
      "client_order_id",
      "broker_order_id",
    )
  )
  assert metadata["candidate_id"] == "candidate-1"
  assert metadata["strategy_order_id"] == "strategy-order-1"


@pytest.mark.asyncio
async def test_strategy_exit_and_manual_handlers_use_equivalent_client_routing(
  owner_runtime_database,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  await _seed_owner_rows(owner_runtime_database)

  class Executor:
    def __init__(self) -> None:
      self.runs = {
        "run-1": SimpleNamespace(
          context=SimpleNamespace(mode=ExecutionEnvironment.PAPER)
        )
      }
      self.orders: list[tuple[str, str]] = []
      self.order_facts: list[tuple[str, int, str]] = []
      self.order_metadata: list[dict] = []
      self.order_owners: list[tuple[ExecutionOwnerRef, ExecutionEnvironment]] = []

    def require_durable_event_consumer(self, run_id: str):
      return self.runs[run_id]

    async def apply_durable_order_report(self, run_id, order) -> None:
      self.orders.append((run_id, order.order_id))
      self.order_facts.append(
        (
          order.request.instrument_code,
          order.request.volume,
          order.request.metadata["strategy_order_id"],
        )
      )
      self.order_metadata.append(dict(order.request.metadata))
      self.order_owners.append(
        (order.request.execution_ref, order.request.environment)
      )

  executor = Executor()
  monkeypatch.setattr(strategy_manager, "executor", executor)

  await report_processor._apply_runtime_event(
    _runtime_event(
      event_id="event-strategy",
      owner_type=ExecutionOwnerType.STRATEGY_RUN,
      owner_id="run-1",
      client_order_id="client-strategy",
      broker_order_id="broker-strategy",
    )
  )
  await report_processor._apply_runtime_event(
    _runtime_event(
      event_id="event-exit",
      owner_type=ExecutionOwnerType.EXIT_PLAN,
      owner_id="plan-1",
      client_order_id="client-exit",
      broker_order_id="broker-exit",
    )
  )
  await report_processor._apply_runtime_event(
    _runtime_event(
      event_id="event-manual",
      owner_type=ExecutionOwnerType.MANUAL_COMMAND,
      owner_id="manual-1",
      client_order_id="client-manual",
      broker_order_id="broker-manual",
    )
  )

  assert executor.orders == [("run-1", "strategy-order-1")]
  assert executor.order_facts == [("600000.SH", 100, "strategy-order-1")]
  assert executor.order_metadata[0]["strategy_order_id"] == "strategy-order-1"
  assert executor.order_metadata[0]["intent_id"] == "intent-strategy"
  assert executor.order_metadata[0]["execution_mode"] == "paper"
  assert executor.order_owners == [
    (
      ExecutionOwnerRef.strategy_run("run-1"),
      ExecutionEnvironment.PAPER,
    )
  ]
  assert all(
    key not in executor.order_metadata[0]
    for key in (
      "owner_type",
      "owner_id",
      "environment",
      "execution_owner_type",
      "execution_owner_id",
      "source_execution_owner_type",
      "source_execution_owner_id",
      "source_execution_environment",
      "run_id",
      "runtime_run_id",
      "client_order_id",
      "broker_order_id",
      "order_id",
    )
  )

  with pytest.raises(report_processor.OwnerRuntimeRoutingError) as captured:
    await report_processor._apply_runtime_event(
      _runtime_event(
        event_id="event-cross-owner",
        owner_type=ExecutionOwnerType.EXIT_PLAN,
        owner_id="plan-1",
        client_order_id="client-manual",
        broker_order_id="broker-manual",
      )
    )
  assert captured.value.code == report_processor.OWNER_TARGET_CONFLICT


@pytest.mark.asyncio
async def test_exit_plan_run_id_is_optional_witness_not_source_owner() -> None:
  nullable_state = _exit_plan_state("plan-nullable")
  nullable_state["template"]["run_id"] = "legacy-source-run"
  nullable_record = SimpleNamespace(
    plan_id="plan-nullable",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="manual",
    strategy_run_id=None,
    environment="PAPER",
    source_execution_owner_type="MANUAL_COMMAND",
    source_execution_owner_id="manual-1",
    source_execution_environment="PAPER",
    plan_state=nullable_state,
  )

  class Database:
    def __init__(self, record) -> None:
      self.record = record

    async def get(self, model, _key, **_kwargs):
      return self.record if model is AutoExitPlanRecord else None

  handler = report_processor._ExitPlanRuntimeHandler(Database(nullable_record))
  target = await handler.resolve(
    ExecutionOwnerRef(ExecutionOwnerType.EXIT_PLAN, "plan-nullable")
  )
  assert target is not None
  assert target.environment is ExecutionEnvironment.PAPER

  conflicting_state = deepcopy(nullable_state)
  conflicting_state["template"]["run_id"] = "different-run"
  conflicting_record = SimpleNamespace(
    **{
      **vars(nullable_record),
      "strategy_run_id": "source-run",
      "source_execution_owner_type": "STRATEGY_RUN",
      "source_execution_owner_id": "source-run",
      "plan_state": conflicting_state,
    }
  )
  conflicting_handler = report_processor._ExitPlanRuntimeHandler(
    Database(conflicting_record)
  )
  with pytest.raises(report_processor.OwnerRuntimeRoutingError) as captured:
    await conflicting_handler.resolve(
      ExecutionOwnerRef(ExecutionOwnerType.EXIT_PLAN, "plan-nullable")
    )
  assert captured.value.code == report_processor.OWNER_TARGET_CONFLICT


@pytest.mark.asyncio
async def test_non_strategy_duplicate_reports_are_noop_and_correlation_id_is_not_pk(
  owner_runtime_database,
) -> None:
  await _seed_owner_rows(owner_runtime_database)
  event = _runtime_event(
    event_id="event-exit-duplicate",
    owner_type=ExecutionOwnerType.EXIT_PLAN,
    owner_id="plan-1",
    client_order_id="client-exit",
    broker_order_id="broker-exit",
  )

  await report_processor._apply_runtime_event(event)
  await report_processor._apply_runtime_event(event)

  async with owner_runtime_database() as db:
    pending = await db.get(PendingTradeOrder, "client-exit")
    plan = await db.get(AutoExitPlanRecord, "plan-1")
    correlation = (
      await db.execute(
        select(OrderCorrelation).where(
          OrderCorrelation.client_order_id == "client-exit"
        )
      )
    ).scalar_one()
  assert pending is not None
  assert pending.status == "SUBMITTED"
  assert plan is not None
  assert plan.exited_volume == 0
  assert plan.state_version == 1
  assert correlation.id != correlation.client_order_id


@pytest.mark.asyncio
async def test_runtime_route_fails_closed_for_missing_correlation_or_intent(
  owner_runtime_database,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  await _seed_owner_rows(owner_runtime_database)

  class Executor:
    runs = {
      "run-1": SimpleNamespace(
        context=SimpleNamespace(mode=ExecutionEnvironment.PAPER)
      )
    }

    def require_durable_event_consumer(self, run_id: str):
      return self.runs[run_id]

  monkeypatch.setattr(strategy_manager, "executor", Executor())
  with pytest.raises(report_processor.OwnerRuntimeRoutingError) as missing_correlation:
    await report_processor._apply_runtime_event(
      _runtime_event(
        event_id="event-missing-correlation",
        owner_type=ExecutionOwnerType.STRATEGY_RUN,
        owner_id="run-1",
        client_order_id="client-not-durable",
        broker_order_id="broker-not-durable",
      )
    )
  assert missing_correlation.value.code == report_processor.OWNER_TARGET_NOT_FOUND

  async with owner_runtime_database() as db:
    intent = await db.get(TradeIntentRecord, "intent-exit")
    assert intent is not None
    await db.delete(intent)
    await db.commit()
  with pytest.raises(report_processor.OwnerRuntimeRoutingError) as missing_intent:
    await report_processor._apply_runtime_event(
      _runtime_event(
        event_id="event-missing-intent",
        owner_type=ExecutionOwnerType.EXIT_PLAN,
        owner_id="plan-1",
        client_order_id="client-exit",
        broker_order_id="broker-exit",
      )
    )
  assert missing_intent.value.code == report_processor.OWNER_TARGET_NOT_FOUND

  with pytest.raises(report_processor.OwnerRuntimeRoutingError) as unknown_owner:
    await report_processor._apply_runtime_event(
      _runtime_event(
        event_id="event-unknown-owner",
        owner_type="UNKNOWN_OWNER",
        owner_id="unknown-1",
        client_order_id="client-exit",
        broker_order_id="broker-exit",
      )
    )
  assert unknown_owner.value.code == report_processor.OWNER_TARGET_CONFLICT
  async with owner_runtime_database() as db:
    assert (await db.execute(select(StrategyRuntimeEvent))).scalars().all() == []


@pytest.mark.asyncio
async def test_update_pending_never_uses_metadata_to_select_exit_replay(
  owner_runtime_database,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  await _seed_owner_rows(owner_runtime_database)
  async with owner_runtime_database() as db:
    pending = await db.get(PendingTradeOrder, "client-manual")
    assert pending is not None
    pending.request_metadata = {
      "owner_type": "EXIT_PLAN",
      "owner_id": "plan-1",
      "exit_plan_id": "plan-1",
    }
    await db.commit()

  # The quarantine lock is orthogonal to this owner-proof unit and would
  # otherwise require the command outbox tables.  It must still be reached
  # before the pending row is considered for convergence.
  monkeypatch.setattr(
    AccountExecutionQuarantineService,
    "lock_client_order_for_lifecycle",
    AsyncMock(return_value=None),
  )
  finalized_replay = AsyncMock(return_value=True)
  monkeypatch.setattr(
    report_processor,
    "is_exact_finalized_exit_order_replay",
    finalized_replay,
  )

  result = await report_processor._update_pending(
    "client-manual",
    status="FILLED",
    broker_order_id="broker-manual",
    source_sequence=1,
    cumulative_filled_volume=100,
  )

  assert result.accepted is True
  finalized_replay.assert_not_awaited()
  async with owner_runtime_database() as db:
    pending = await db.get(PendingTradeOrder, "client-manual")
    assert pending is not None
    assert pending.status == "FILLED"


@pytest.mark.asyncio
async def test_manual_runtime_event_marker_makes_duplicate_drain_idempotent(
  owner_runtime_database,
) -> None:
  await _seed_owner_rows(owner_runtime_database)
  async with owner_runtime_database() as db:
    db.add(
      StrategyRuntimeEvent(
        event_id="event-manual-drain",
        business_key="order:manual-drain",
        owner_type="MANUAL_COMMAND",
        owner_id="manual-1",
        environment="PAPER",
        strategy_run_id=None,
        client_order_id="client-manual",
        broker_order_id="broker-manual",
        event_type="ORDER",
        payload={
          "report": {
            "order_id": "broker-manual",
            "account_id": "account-1",
            "stock_code": "600000.SH",
            "order_status": 49,
          },
          "metadata": {},
        },
        application_status="PENDING",
        application_attempts=0,
        created_at=report_processor.utcnow(),
      )
    )
    await db.commit()

  await report_processor._drain_runtime_events()
  await report_processor._drain_runtime_events()

  async with owner_runtime_database() as db:
    event = await db.get(StrategyRuntimeEvent, "event-manual-drain")
    pending = await db.get(PendingTradeOrder, "client-manual")
    plan = await db.get(AutoExitPlanRecord, "plan-1")
  assert event is not None
  assert event.application_status == "APPLIED"
  assert event.application_attempts == 1
  assert pending is not None
  assert pending.status == "SUBMITTED"
  assert plan is not None
  assert plan.state_version == 1


@pytest.mark.asyncio
async def test_strategy_runtime_event_marker_deduplicates_callback(
  owner_runtime_database,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  await _seed_owner_rows(owner_runtime_database)

  class Executor:
    def __init__(self) -> None:
      self.runs = {
        "run-1": SimpleNamespace(
          context=SimpleNamespace(mode=ExecutionEnvironment.PAPER)
        )
      }
      self.orders: list[str] = []

    def require_durable_event_consumer(self, run_id: str):
      return self.runs[run_id]

    async def apply_durable_order_report(self, _run_id, order) -> None:
      self.orders.append(order.order_id)

  executor = Executor()
  monkeypatch.setattr(strategy_manager, "executor", executor)
  async with owner_runtime_database() as db:
    db.add(
      StrategyRuntimeEvent(
        event_id="event-strategy-drain",
        business_key="order:strategy-drain",
        owner_type="STRATEGY_RUN",
        owner_id="run-1",
        environment="PAPER",
        strategy_run_id="run-1",
        client_order_id="client-strategy",
        broker_order_id="broker-strategy",
        event_type="ORDER",
        payload={
          "report": {
            "order_id": "broker-strategy",
            "account_id": "account-1",
            "stock_code": "600000.SH",
            "order_status": 49,
            "order_volume": 100,
          },
          "metadata": {
            "strategy_order_id": "metadata-cross-order",
            "instrument_code": "000001.SZ",
            "requested_entry_volume": 999,
          },
        },
        application_status="PENDING",
        application_attempts=0,
        created_at=report_processor.utcnow(),
      )
    )
    await db.commit()

  await report_processor._drain_runtime_events()
  await report_processor._drain_runtime_events()

  async with owner_runtime_database() as db:
    event = await db.get(StrategyRuntimeEvent, "event-strategy-drain")
  assert executor.orders == ["strategy-order-1"]
  assert event is not None
  assert event.application_status == "APPLIED"
  assert event.application_attempts == 1
