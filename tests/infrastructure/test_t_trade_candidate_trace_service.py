from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models import (
  AuthUser,
  AutoExitPlanEvent,
  AutoExitPlanRecord,
  Order,
  PendingTradeOrder,
  StrategyOrderCorrelation,
  Trade,
  TradeIntentRecord,
  TTradeBatch,
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.enums import OrderPriceType, OrderStatus, OrderType
from quantx_infrastructure.services.t_trade_candidate_trace_service import (
  TTradeCandidateTraceService,
)
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TABLES = [
  AuthUser.__table__,
  TTradeOpportunityEvaluation.__table__,
  TradeIntentRecord.__table__,
  PendingTradeOrder.__table__,
  StrategyOrderCorrelation.__table__,
  TTradeBatch.__table__,
  Order.__table__,
  Trade.__table__,
  AutoExitPlanRecord.__table__,
  AutoExitPlanEvent.__table__,
]
BASE_TIME = datetime(2026, 8, 23, 9, 30)


@asynccontextmanager
async def _database():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  try:
    async with session_factory() as db:
      yield engine, db
  finally:
    await engine.dispose()


def _evaluation(
  *,
  row_id: str,
  account_id: str = "account-a",
  candidate_id: str = "candidate-1",
  strategy_run_id: str = "run-1",
  instrument_code: str = "600000.SH",
  seconds: int = 0,
  candidate_status: str = "AWAITING_APPROVAL",
  pending_intent_id: str | None = "intent-entry",
) -> TTradeOpportunityEvaluation:
  evaluated_at = BASE_TIME + timedelta(seconds=seconds)
  return TTradeOpportunityEvaluation(
    id=row_id,
    event_key=f"event-{row_id}",
    account_id=account_id,
    strategy_run_id=strategy_run_id,
    instrument_code=instrument_code,
    candidate_id=candidate_id,
    evaluated_at=evaluated_at,
    record_kind="MATERIAL",
    event_type="CANDIDATE_STATE_CHANGED",
    coalesced_count=1,
    policy_version="policy-v3",
    schema_version="3",
    content_fingerprint=(row_id[-1:] or "a") * 64,
    payload={
      "signal_snapshot": {
        "candidate_id": candidate_id,
        "candidate_fingerprint": "fingerprint-1",
        "candidate_status": candidate_status,
        "pending_entry_intent_id": pending_intent_id,
        "episode_id": "episode-1",
        "instrument_code": instrument_code,
        "trade_date": "2026-08-23",
        "source_time_ms": 1_787_451_400_000 + seconds * 1000,
        "tick_ordinal": 10 + seconds,
        "continuity_generation": "4",
        "selected_path": "PULLBACK_REBOUND",
        "dominant_phase": "PULLBACK_CANDIDATE_LATCHED",
        "opportunity_score": 74,
        "data_health": "READY",
        "policy_version": "policy-v3",
        "feature_schema_version": "1",
        "profile_version": "profile-v1",
        "password": "must-not-leak",
      },
      "device_secret": "must-not-leak",
    },
    metrics={},
    created_at=evaluated_at,
  )


def _intent(
  *,
  row_id: str = "intent-entry",
  account_id: str = "account-a",
  candidate_id: str = "candidate-1",
  strategy_run_id: str = "run-1",
  instrument_code: str = "600000.SH",
  status: str = "FILLED",
  seconds: int = 1,
) -> TradeIntentRecord:
  created_at = BASE_TIME + timedelta(seconds=seconds)
  return TradeIntentRecord(
    id=row_id,
    strategy_run_id=strategy_run_id,
    owner_type="STRATEGY_RUN",
    owner_id=strategy_run_id,
    account_id=account_id,
    strategy_id="ashare-intraday-t-assistant",
    instrument_code=instrument_code,
    direction="BUY",
    bucket="swing",
    reason="T_TRADE_PULLBACK_REBOUND_ENTRY",
    priority="NORMAL",
    confidence=1.0,
    target_amount=10_000,
    limit_price_hint=10.12,
    trace_id="trace-1",
    order_id="1001" if status == "FILLED" else None,
    status=status,
    executed_price=10.12 if status == "FILLED" else None,
    executed_volume=100 if status == "FILLED" else None,
    executed_time=BASE_TIME + timedelta(seconds=4) if status == "FILLED" else None,
    intent_metadata={
      "candidate_id": candidate_id,
      "candidate_fingerprint": "fingerprint-1",
      "opportunity_schema_version": 3,
      "t_trade_role": "entry",
      "t_batch_id": "batch-1",
      "exit_plan_id": "exit-plan-1",
      "device_secret": "must-not-leak",
    },
    notes="must-not-leak",
    created_at=created_at,
    updated_at=created_at,
  )


def _pending_order() -> PendingTradeOrder:
  created_at = BASE_TIME + timedelta(seconds=2)
  return PendingTradeOrder(
    client_order_id="client-entry",
    user_id="user-secret",
    account_id="account-a",
    instrument_code="600000.SH",
    side="BUY",
    order_type="FIX_PRICE",
    limit_price="10.12",
    volume=100,
    status="FILLED",
    broker_order_id="0001001",
    execution_mode="paper",
    strategy_run_id="run-1",
    strategy_order_id="strategy-order-1",
    intent_id="intent-entry",
    batch_id="batch-1",
    bucket="swing",
    t_trade_role="ENTRY",
    trace_id="trace-1",
    request_metadata={"device_secret": "must-not-leak"},
    created_at=created_at,
    updated_at=BASE_TIME + timedelta(seconds=5),
  )


def _correlation() -> StrategyOrderCorrelation:
  created_at = BASE_TIME + timedelta(seconds=2)
  return StrategyOrderCorrelation(
    id="correlation-1",
    client_order_id="client-entry",
    broker_order_id="1001",
    account_id="account-a",
    strategy_run_id="run-1",
    strategy_order_id="strategy-order-1",
    intent_id="intent-entry",
    batch_id="batch-1",
    bucket="swing",
    t_trade_role="ENTRY",
    execution_mode="paper",
    trace_id="trace-1",
    request_metadata={"password": "must-not-leak"},
    created_at=created_at,
    updated_at=created_at,
  )


def _batch() -> TTradeBatch:
  created_at = BASE_TIME + timedelta(seconds=2)
  return TTradeBatch(
    batch_id="batch-1",
    account_id="account-a",
    instrument_code="600000.SH",
    strategy_run_id="run-1",
    status="OPEN",
    entry_intent_id="intent-entry",
    entry_client_order_id="client-entry",
    entry_broker_order_id="1001",
    target_volume=100,
    entry_filled_volume=100,
    entry_avg_price=10.12,
    policy_version=3,
    version=2,
    created_at=created_at,
    updated_at=BASE_TIME + timedelta(seconds=5),
  )


def _order() -> Order:
  created_at = BASE_TIME + timedelta(seconds=3)
  return Order(
    id=1001,
    account_id="account-a",
    stock_code="600000.SH",
    sysid="SYS1001",
    time=created_at,
    type=OrderType.BUY,
    volume=100,
    price_type=OrderPriceType.LIMIT,
    price=10.12,
    traded_volume=100,
    traded_price=10.12,
    status=OrderStatus.SUCCEEDED,
    status_msg="成交",
    secu_account="shareholder-secret",
    remark="must-not-leak",
    created_at=created_at,
    updated_at=BASE_TIME + timedelta(seconds=5),
  )


def _trade(row_id: str, seconds: int, volume: int) -> Trade:
  occurred_at = BASE_TIME + timedelta(seconds=seconds)
  return Trade(
    id=row_id,
    time=occurred_at,
    price=10.12,
    volume=volume,
    amount=10.12 * volume,
    account_id="account-a",
    stock_code="600000.SH",
    order_id=1001,
    order_sysid="SYS1001",
    order_type=int(OrderType.BUY),
    order_remark="must-not-leak",
    created_at=occurred_at,
    updated_at=occurred_at,
  )


def _exit_plan() -> AutoExitPlanRecord:
  created_at = BASE_TIME + timedelta(seconds=6)
  return AutoExitPlanRecord(
    plan_id="exit-plan-1",
    account_id="account-a",
    instrument_code="600000.SH",
    bucket="swing",
    source_type="T_TRADE_BATCH",
    source_id="batch-1",
    strategy_run_id="run-1",
    enabled=True,
    status="ACTIVE",
    execution_mode="paper",
    auto_exit_authorized=False,
    config_version=2,
    protected_volume=100,
    exited_volume=0,
    remaining_volume=100,
    entry_avg_price=10.12,
    cost_basis_mode="ENTRY_FILL",
    cost_basis_snapshot={"secu_account": "must-not-leak"},
    capacity_status="READY",
    plan_state={"password": "must-not-leak"},
    phase="WAITING_ARM",
    data_quality="READY",
    created_at=created_at,
    updated_at=created_at,
  )


def _plan_event(row_id: str, seconds: int, event_type: str) -> AutoExitPlanEvent:
  return AutoExitPlanEvent(
    event_id=row_id,
    business_key=f"business-{row_id}",
    plan_id="exit-plan-1",
    event_type=event_type,
    payload={
      "status": "ACTIVE",
      "phase": "WAITING_ARM",
      "config_version": 2,
      "device_session_id": "must-not-leak",
    },
    created_at=BASE_TIME + timedelta(seconds=seconds),
  )


@pytest.mark.asyncio
async def test_candidate_trace_builds_complete_sorted_chain_without_n_plus_one() -> (
  None
):
  async with _database() as (engine, db):
    db.add_all(
      [
        _evaluation(
          row_id="evaluation-b",
          candidate_status="LATCHED",
          pending_intent_id=None,
        ),
        _evaluation(row_id="evaluation-a", seconds=1),
        _intent(),
        _pending_order(),
        _correlation(),
        _batch(),
        _order(),
        _trade("trade-later", 5, 60),
        _trade("trade-earlier", 4, 40),
        _exit_plan(),
        _plan_event("plan-event-later", 8, "PLAN_ARMED"),
        _plan_event("plan-event-earlier", 7, "PLAN_CREATED"),
      ]
    )
    await db.commit()

    statements: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _many):
      if statement.lstrip().upper().startswith("SELECT"):
        statements.append(statement)

    sqlalchemy_event.listen(engine.sync_engine, "before_cursor_execute", count_selects)
    try:
      trace = await TTradeCandidateTraceService(db).get_trace(
        account_id="account-a",
        strategy_run_id="run-1",
        candidate_id="candidate-1",
      )
    finally:
      sqlalchemy_event.remove(
        engine.sync_engine,
        "before_cursor_execute",
        count_selects,
      )

    assert trace is not None
    assert trace.integrity_status == "COMPLETE"
    assert trace.missing_reasons == ()
    assert trace.source_evaluation_id == "evaluation-b"
    assert trace.source_identity.continuity_generation == "4"
    assert trace.links.broker_order_ids == ("1001",)
    assert trace.links.trade_ids == ("trade-earlier", "trade-later")
    assert trace.links.exit_plan_event_ids == (
      "plan-event-earlier",
      "plan-event-later",
    )
    assert len(statements) == 10
    event_times = [
      event.occurred_at.replace(tzinfo=None).timestamp() for event in trace.events
    ]
    assert event_times == sorted(event_times)
    assert [
      event.entity_id for event in trace.events if event.stage == "BROKER_TRADE"
    ] == ["trade-earlier", "trade-later"]
    serialized = str(trace.to_dict())
    assert "must-not-leak" not in serialized
    assert "shareholder-secret" not in serialized
    assert "user-secret" not in serialized


@pytest.mark.asyncio
async def test_candidate_trace_marks_normal_pre_approval_chain_in_progress() -> None:
  async with _database() as (_engine, db):
    db.add_all(
      [
        _evaluation(row_id="evaluation-partial"),
        _intent(status="AWAITING_APPROVAL"),
      ]
    )
    await db.commit()

    trace = await TTradeCandidateTraceService(db).get_trace(
      account_id="account-a",
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )

    assert trace is not None
    assert trace.integrity_status == "IN_PROGRESS"
    assert trace.links.intent_ids == ("intent-entry",)
    assert trace.links.client_order_ids == ()
    reasons = {item.code: item for item in trace.missing_reasons}
    assert reasons.keys() == {
      "AUTO_EXIT_PLAN_NOT_FOUND",
      "ORDER_COMMAND_NOT_FOUND",
      "T_TRADE_BATCH_NOT_FOUND",
    }
    assert all(item.expected for item in reasons.values())


@pytest.mark.asyncio
async def test_candidate_trace_treats_unfilled_order_without_plan_as_in_progress() -> (
  None
):
  async with _database() as (_engine, db):
    intent = _intent(status="ROUTED")
    pending_order = _pending_order()
    pending_order.status = "ACCEPTED"
    batch = _batch()
    batch.status = "ENTRY_SUBMITTED"
    batch.entry_filled_volume = 0
    batch.entry_avg_price = 0.0
    order = _order()
    order.status = OrderStatus.REPORTED
    order.traded_volume = 0
    order.traded_price = 0.0
    db.add_all(
      [
        _evaluation(row_id="evaluation-unfilled"),
        intent,
        pending_order,
        _correlation(),
        batch,
        order,
      ]
    )
    await db.commit()

    trace = await TTradeCandidateTraceService(db).get_trace(
      account_id="account-a",
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )

    assert trace is not None
    assert trace.integrity_status == "IN_PROGRESS"
    assert trace.links.broker_order_ids == ("1001",)
    assert trace.links.order_ids == ("1001",)
    assert trace.links.trade_ids == ()
    assert trace.links.exit_plan_ids == ()
    reasons = {item.code: item for item in trace.missing_reasons}
    assert reasons.keys() == {
      "AUTO_EXIT_PLAN_NOT_FOUND",
      "TRADE_FACT_NOT_FOUND",
    }
    assert all(item.expected for item in reasons.values())


@pytest.mark.asyncio
async def test_candidate_trace_never_uses_cross_account_downstream_rows() -> None:
  async with _database() as (_engine, db):
    db.add_all(
      [
        _evaluation(row_id="evaluation-a"),
        _intent(
          row_id="intent-other-account",
          account_id="account-b",
          status="AWAITING_APPROVAL",
        ),
      ]
    )
    await db.commit()

    service = TTradeCandidateTraceService(db)
    trace = await service.get_trace(
      account_id="account-a",
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )
    other_account = await service.get_trace(
      account_id="account-b",
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )

    assert trace is not None
    assert trace.links.intent_ids == ()
    assert trace.integrity_status == "BROKEN"
    assert trace.missing_reasons[0].code == "TRADE_INTENT_NOT_FOUND"
    assert trace.missing_reasons[0].expected is False
    assert other_account is None


@pytest.mark.asyncio
async def test_candidate_trace_rejects_unscoped_exit_plan_event_hint() -> None:
  async with _database() as (_engine, db):
    intent = _intent(status="AWAITING_APPROVAL")
    intent.intent_metadata = {
      **dict(intent.intent_metadata or {}),
      "exit_plan_id": "exit-plan-foreign",
      "t_batch_id": "",
    }
    foreign_plan = _exit_plan()
    foreign_plan.plan_id = "exit-plan-foreign"
    foreign_plan.account_id = "account-b"
    foreign_plan.source_id = "batch-foreign"
    foreign_event = _plan_event("foreign-plan-event", 7, "PLAN_CREATED")
    foreign_event.plan_id = "exit-plan-foreign"
    db.add_all(
      [
        _evaluation(row_id="evaluation-a"),
        intent,
        foreign_plan,
        foreign_event,
      ]
    )
    await db.commit()

    trace = await TTradeCandidateTraceService(db).get_trace(
      account_id="account-a",
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )

    assert trace is not None
    assert trace.links.exit_plan_ids == ()
    assert trace.links.exit_plan_event_ids == ()
    assert all(event.entity_id != "foreign-plan-event" for event in trace.events)


@pytest.mark.asyncio
async def test_candidate_trace_filters_unrelated_intents_before_bounded_read() -> None:
  async with _database() as (_engine, db):
    unrelated = []
    for index in range(4):
      row = _intent(
        row_id=f"intent-unrelated-{index}",
        candidate_id=f"candidate-unrelated-{index}",
        status="AWAITING_APPROVAL",
        seconds=index + 2,
      )
      row.intent_metadata = {
        **dict(row.intent_metadata or {}),
        "t_batch_id": f"batch-unrelated-{index}",
        "exit_plan_id": "",
      }
      unrelated.append(row)
    db.add_all(
      [
        _evaluation(row_id="evaluation-bounded"),
        _intent(status="AWAITING_APPROVAL"),
        *unrelated,
      ]
    )
    await db.commit()

    trace = await TTradeCandidateTraceService(
      db,
      stage_row_limit=1,
    ).get_trace(
      account_id="account-a",
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )

    assert trace is not None
    assert trace.links.intent_ids == ("intent-entry",)


@pytest.mark.asyncio
async def test_candidate_trace_rejects_unbounded_exit_plan_event_history() -> None:
  async with _database() as (_engine, db):
    db.add_all(
      [
        _evaluation(row_id="evaluation-plan-bound"),
        _intent(status="AWAITING_APPROVAL"),
        _exit_plan(),
        _plan_event("plan-event-1", 7, "PLAN_CREATED"),
        _plan_event("plan-event-2", 8, "PLAN_ARMED"),
      ]
    )
    await db.commit()

    with pytest.raises(ValueError, match="自动退出计划事件|有界上限"):
      await TTradeCandidateTraceService(db, stage_row_limit=1).get_trace(
        account_id="account-a",
        strategy_run_id="run-1",
        candidate_id="candidate-1",
      )


@pytest.mark.asyncio
async def test_candidate_trace_is_repeatable_and_stably_orders_equal_timestamps() -> (
  None
):
  async with _database() as (_engine, db):
    db.add_all(
      [
        _evaluation(
          row_id="evaluation-z",
          candidate_status="LATCHED",
          pending_intent_id=None,
        ),
        _evaluation(
          row_id="evaluation-a",
          candidate_status="LATCHED",
          pending_intent_id=None,
        ),
      ]
    )
    await db.commit()

    service = TTradeCandidateTraceService(db)
    first = await service.get_trace(
      account_id="account-a",
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )
    second = await service.get_trace(
      account_id="account-a",
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )

    assert first == second
    assert first is not None
    assert [event.entity_id for event in first.events] == [
      "evaluation-a",
      "evaluation-z",
    ]
    assert first.links.evaluation_ids == ("evaluation-a", "evaluation-z")


@pytest.mark.asyncio
async def test_candidate_trace_is_scoped_by_strategy_run_for_repeated_replay() -> None:
  async with _database() as (_engine, db):
    db.add_all(
      [
        _evaluation(row_id="evaluation-one"),
        _evaluation(row_id="evaluation-two", strategy_run_id="run-2"),
      ]
    )
    await db.commit()

    service = TTradeCandidateTraceService(db)
    first = await service.get_trace(
      account_id="account-a",
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )
    second = await service.get_trace(
      account_id="account-a",
      strategy_run_id="run-2",
      candidate_id="candidate-1",
    )

    assert first is not None
    assert second is not None
    assert first.strategy_run_id == "run-1"
    assert second.strategy_run_id == "run-2"
    assert first.links.evaluation_ids == ("evaluation-one",)
    assert second.links.evaluation_ids == ("evaluation-two",)
