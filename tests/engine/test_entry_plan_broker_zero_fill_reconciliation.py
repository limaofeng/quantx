from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256

import pytest
from quantx_domain.clock import to_naive_utc, utcnow
from quantx_domain.enums import StrategyRunMode
from quantx_domain.strategies.ashare_managed_entry_plan import (
  MANAGED_ENTRY_STATE_KEY,
  AshareManagedEntryPlanStrategy,
)
from quantx_domain.strategies.base import OrderStateEvent, StrategyContext
from quantx_domain.trading import EntryPlanStatus, ManagedEntryPlanState
from quantx_engine import report_processor
from quantx_engine.strategy_manager import strategy_manager
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentReportInbox,
  PendingTradeOrder,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _snapshot_report(
  *,
  terminal_status: str,
  snapshot_id: str,
  source_sequence: int = 10,
) -> AgentReportInbox:
  snapshot_at = datetime(2026, 8, 20, 10, 5, tzinfo=timezone.utc)
  payload = {
    "snapshot_id": snapshot_id,
    "is_complete": True,
    "source_sequence": source_sequence,
    "source_event_at": snapshot_at.isoformat(),
    "accounts": [{"account_id": "account-1"}],
    "positions_by_account": {"account-1": []},
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "orders": [
      {
        "client_order_id": "client-1",
        "account_id": "account-1",
        "order_id": 9001,
        "stock_code": "605499.SH",
        "order_type": 23,
        "order_status": terminal_status,
        "order_volume": 100,
        "traded_volume": 0,
        "traded_price": 0,
        "price": 10,
        "source_sequence": source_sequence,
        "source_event_at": snapshot_at.isoformat(),
      }
    ],
    "trades": [],
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  return AgentReportInbox(
    message_id=f"snapshot-report-{snapshot_id}",
    device_id="device-1",
    message_type="delta_report",
    protocol_version="1.1",
    client_order_id=None,
    raw_payload_hash="a" * 64,
    business_idempotency_key=f"snapshot:{snapshot_id}",
    payload=payload,
    received_at=utcnow(),
    processing_status="PROCESSING",
  )


def _terminal_report(terminal_status: str) -> AgentReportInbox:
  return AgentReportInbox(
    message_id=f"terminal-report-{terminal_status}",
    device_id="device-1",
    message_type="order_report",
    protocol_version="1.1",
    client_order_id="client-1",
    raw_payload_hash="b" * 64,
    business_idempotency_key=f"terminal:{terminal_status}",
    payload={
      "client_order_id": "client-1",
      "source_sequence": 10,
      "order": {
        "client_order_id": "client-1",
        "account_id": "account-1",
        "order_id": 9001,
        "stock_code": "605499.SH",
        "order_type": 23,
        "order_status": terminal_status,
        "order_volume": 100,
        "traded_volume": 0,
        "price": 10,
      },
    },
    received_at=utcnow(),
    processing_status="PROCESSING",
  )


async def _database(monkeypatch: pytest.MonkeyPatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    PendingTradeOrder.__table__,
    StrategyOrderCorrelation.__table__,
    StrategyRuntimeEvent.__table__,
    AccountExecutionControl.__table__,
    TradeIntentRecord.__table__,
    Order.__table__,
    Trade.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=tables,
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
  return engine, sessions


async def _seed_managed_order(
  sessions,
  *,
  terminal_status: str,
  snapshot: AgentReportInbox,
) -> None:
  snapshot_at = to_naive_utc(
    datetime.fromisoformat(snapshot.payload["source_event_at"])
  )
  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="entry-zero-fill",
        display_name="Entry Zero Fill",
        password_hash="unused",
        permissions=[],
      )
    )
    db.add(
      TradeIntentRecord(
        id="intent-1",
        strategy_run_id="plan-1",
        owner_type="STRATEGY_RUN",
        owner_id="plan-1",
        account_id="account-1",
        instrument_code="605499.SH",
        direction="BUY",
        bucket="core",
        reason="MANAGED_ENTRY",
        target_volume=100,
        status=terminal_status,
        executed_volume=0,
        executed_price=None,
        executed_time=None,
        intent_metadata={"entry_plan_id": "plan-1"},
      )
    )
    db.add(
      PendingTradeOrder(
        client_order_id="client-1",
        user_id="user-1",
        account_id="account-1",
        instrument_code="605499.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price="10",
        volume=100,
        status=terminal_status,
        broker_order_id="9001",
        execution_mode="live",
        strategy_run_id="plan-1",
        strategy_order_id="strategy-order-1",
        intent_id="intent-1",
        bucket="core",
        request_metadata={"entry_plan_id": "plan-1"},
        last_source_sequence=10,
        last_source_event_at=snapshot_at,
      )
    )
    db.add(
      StrategyOrderCorrelation(
        id="correlation-1",
        client_order_id="client-1",
        broker_order_id="9001",
        account_id="account-1",
        strategy_run_id="plan-1",
        strategy_order_id="strategy-order-1",
        intent_id="intent-1",
        bucket="core",
        execution_mode="live",
        trace_id="trace-1",
        request_metadata={
          "entry_plan_id": "plan-1",
          "instrument_code": "605499.SH",
        },
      )
    )
    db.add(
      AccountExecutionControl(
        account_id="account-1",
        reconcile_status="READY",
        last_snapshot_id=snapshot.payload["snapshot_id"],
        last_snapshot_hash=snapshot.payload["snapshot_hash"],
        last_snapshot_at=snapshot_at,
      )
    )
    await db.commit()


def _managed_strategy(terminal_status: str) -> AshareManagedEntryPlanStrategy:
  strategy = AshareManagedEntryPlanStrategy(
    StrategyContext(
      run_id="plan-1",
      mode=StrategyRunMode.LIVE,
      instruments=["605499.SH"],
      current_time=datetime(2026, 8, 20, 10, 5),
      parameters={
        MANAGED_ENTRY_STATE_KEY: {
          "template_version": 1,
          "config_version": 1,
          "instrument_code": "605499.SH",
          "bucket": "core",
          "target_policy": {
            "mode": "ADDITIONAL_VOLUME",
            "additional_volume": 100,
            "max_total_amount_cny": 20_000,
            "max_position_pct": 0.5,
            "baseline_snapshot": {
              "position_volume": 0,
              "market_value_cny": 0,
              "total_asset_cny": 100_000,
              "reference_price": 10,
              "account_snapshot_version": "snapshot-1",
            },
          },
          "trigger_rules": [
            {
              "rule_id": "manual-1",
              "rule_type": "MANUAL_TRIGGER",
              "priority": 100,
              "parameters": {},
            }
          ],
          "pacing_policy": {
            "tranche_count": 1,
            "max_single_intent_amount_cny": 20_000,
            "max_daily_filled_amount_cny": 20_000,
            "max_orders_per_day": 1,
            "max_open_orders": 1,
          },
          "execution_policy": {
            "environment": "LIVE",
            "authorization_mode": "MANUAL_CONFIRM",
            "price_reference": "ASK1_PROTECTED_LIMIT",
            "approval_ttl_ms": 60_000,
          },
          "completion_policy": {"max_buy_price": 12},
        }
      },
    )
  )
  requested = EntryPlanStatus(terminal_status)
  state = ManagedEntryPlanState(
    phase=EntryPlanStatus.DRAINING,
    terminal_requested=requested,
    terminal_request_reason=f"USER_{terminal_status}",
    pending_intent_id="intent-1",
    pending_stage_id="stage-1",
    pending_rule_id="manual-1",
    pending_rule_type="MANUAL_TRIGGER",
    pending_requested_volume=100,
    pending_requested_amount_cny=1_000,
    reserved_amount_cny=1_000,
  )
  strategy.state.set(
    MANAGED_ENTRY_STATE_KEY,
    state.to_dict(),
    persist=False,
    notify=False,
  )
  return strategy


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["CANCELLED", "EXPIRED"])
async def test_full_snapshot_proves_zero_fill_and_replays_idempotently(
  monkeypatch: pytest.MonkeyPatch,
  terminal_status: str,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status=terminal_status,
    snapshot_id=f"snapshot-{terminal_status.lower()}",
  )
  await _seed_managed_order(
    sessions,
    terminal_status=terminal_status,
    snapshot=snapshot,
  )

  try:
    # The first terminal broker report is not a zero-fill proof because an
    # execution report may still follow it.
    await report_processor._stage_runtime_events(
      _terminal_report(terminal_status)
    )
    async with sessions() as db:
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status == terminal_status
      assert (
        await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent))
        == 1
      )

    await report_processor._stage_runtime_events(snapshot)
    await report_processor._stage_runtime_events(snapshot)
    async with sessions() as db:
      events = list(
        (
          await db.execute(
            select(StrategyRuntimeEvent).order_by(
              StrategyRuntimeEvent.created_at,
              StrategyRuntimeEvent.event_id,
            )
          )
        )
        .scalars()
        .all()
      )
      assert len(events) == 2
      zero_event = events[-1]
      assert (
        zero_event.payload["report"]["effective_order_status"]
        == "RECONCILED_ZERO_FILL"
      )
      audit = zero_event.payload["metadata"][
        "qmt_zero_fill_reconciliation"
      ]
      assert audit["snapshot_id"] == snapshot.payload["snapshot_id"]
      assert audit["broker_terminal_status"] == terminal_status
      assert audit["received_execution_volume"] == 0
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status == "RECONCILED_ZERO_FILL"
      assert intent.intent_metadata[
        "qmt_zero_fill_reconciliation"
      ]["snapshot_id"] == snapshot.payload["snapshot_id"]
      zero_event.application_status = "PROCESSING"
      await db.commit()

    # A restart returns the exact same durable event to PENDING; replaying the
    # full snapshot cannot create a second terminal callback.
    await report_processor._recover_stuck_runtime_events()
    await report_processor._stage_runtime_events(snapshot)
    async with sessions() as db:
      zero_event = await db.scalar(
        select(StrategyRuntimeEvent).where(
          StrategyRuntimeEvent.business_key.like(
            "%:RECONCILED_ZERO_FILL:0"
          )
        )
      )
      assert zero_event is not None
      assert zero_event.application_status == "PENDING"
      assert (
        await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent))
        == 2
      )

    captured_orders = []

    async def capture_order(_run_id, order):
      captured_orders.append(order)

    monkeypatch.setattr(
      strategy_manager.executor,
      "apply_durable_order_report",
      capture_order,
    )
    await report_processor._apply_runtime_event(zero_event)
    [order] = captured_orders
    assert order.status == "RECONCILED_ZERO_FILL"
    event = OrderStateEvent.from_raw(order)
    assert event.status == "RECONCILED_ZERO_FILL"
    assert event.metadata["qmt_zero_fill_reconciliation"]["snapshot_id"] == (
      snapshot.payload["snapshot_id"]
    )

    strategy = _managed_strategy(terminal_status)
    patch = await strategy.on_order(event)
    assert patch is not None
    settled = patch.set[MANAGED_ENTRY_STATE_KEY]
    assert settled["phase"] == terminal_status
    assert settled["pending_intent_id"] == ""
    assert settled["filled_volume"] == 0
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_late_execution_prevents_full_snapshot_zero_fill_proof(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status="CANCELLED",
    snapshot_id="snapshot-after-late-fill",
    source_sequence=12,
  )
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
  )
  late_fill = AgentReportInbox(
    message_id="late-fill-report",
    device_id="device-1",
    message_type="execution_report",
    protocol_version="1.1",
    client_order_id="client-1",
    raw_payload_hash="c" * 64,
    business_idempotency_key="late-fill:1",
    payload={
      "client_order_id": "client-1",
      "execution": {
        "client_order_id": "client-1",
        "account_id": "account-1",
        "order_id": 9001,
        "execution_id": "execution-late-1",
        "stock_code": "605499.SH",
        "order_type": 23,
        "traded_volume": 100,
        "traded_price": 10,
        "traded_time": datetime(
          2026, 8, 20, 10, 4, tzinfo=timezone.utc
        ).isoformat(),
      },
    },
    received_at=utcnow(),
    processing_status="PROCESSING",
  )

  try:
    await report_processor._stage_runtime_events(_terminal_report("CANCELLED"))
    await report_processor._stage_runtime_events(late_fill)
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
      )
      assert len(events) == 2
      assert {
        event.payload["report"].get("effective_order_status")
        for event in events
        if event.event_type == "ORDER"
      } == {"CANCELLED"}
      assert not any(
        event.payload["report"].get("effective_order_status")
        == "RECONCILED_ZERO_FILL"
        for event in events
      )
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.executed_volume == 100
      assert "qmt_zero_fill_reconciliation" not in intent.intent_metadata
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_declared_complete_snapshot_with_incomplete_trade_section_cannot_prove_zero(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status="CANCELLED",
    snapshot_id="snapshot-incomplete-trades",
  )
  snapshot.payload["section_completeness_by_account"]["account-1"][
    "trades"
  ] = False
  hash_input = {
    key: value
    for key, value in snapshot.payload.items()
    if key != "snapshot_hash"
  }
  snapshot.payload["snapshot_hash"] = sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
  )

  try:
    await report_processor._stage_runtime_events(_terminal_report("CANCELLED"))
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
      )
      assert not any(
        event.payload["report"].get("effective_order_status")
        == "RECONCILED_ZERO_FILL"
        for event in events
      )
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status != "RECONCILED_ZERO_FILL"
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_historical_terminal_fill_projection_cannot_regress_to_zero(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status="CANCELLED",
    snapshot_id="snapshot-after-terminal-fill-projection",
    source_sequence=12,
  )
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
  )
  terminal = _terminal_report("CANCELLED")
  terminal.payload["order"]["traded_volume"] = 100
  terminal.payload["order"]["traded_price"] = 10

  try:
    await report_processor._stage_runtime_events(terminal)
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
      )
      assert len(events) == 2
      assert not any(
        event.payload["report"].get("effective_order_status")
        == "RECONCILED_ZERO_FILL"
        for event in events
      )
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status == "RECONCILE_REQUIRED"
      assert intent.notes == (
        "AWAITING_ORDER_EXECUTION_REPORT: terminal=CANCELLED, "
        "expected=100, received=0"
      )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_conflicting_snapshot_fill_fields_fail_closed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status="CANCELLED",
    snapshot_id="snapshot-conflicting-fill-fields",
  )
  snapshot.payload["orders"][0]["filled_volume"] = 100
  hash_input = {
    key: value
    for key, value in snapshot.payload.items()
    if key != "snapshot_hash"
  }
  snapshot.payload["snapshot_hash"] = sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
  )

  try:
    await report_processor._stage_runtime_events(_terminal_report("CANCELLED"))
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
      )
      assert len(events) == 2
      event = next(
        item
        for item in events
        if item.business_key.endswith(":CANCELLED:100")
      )
      assert (
        event.payload["report"]["effective_order_status"] == "CANCELLED"
      )
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status == "RECONCILE_REQUIRED"
      assert intent.notes == (
        "AWAITING_ORDER_EXECUTION_REPORT: terminal=CANCELLED, "
        "expected=100, received=0"
      )
  finally:
    await engine.dispose()
