"""Real ORM facts with synthetic broker receipts; no device or live dispatch."""

from datetime import UTC, datetime, timedelta

import pytest
from quantx_engine.report_processor import _event_payload
from quantx_engine.t_assistant_legacy_settlement import read_legacy_order_settlement
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.enums import OrderPriceType, OrderStatus, OrderType
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import select

from tests.engine.test_entry_plan_broker_zero_fill_reconciliation import (
  _database,
  _seed_managed_order,
  _snapshot_report,
)


async def seed_settlement(monkeypatch, volume):
  engine, sessions = await _database(monkeypatch)
  status = "FILLED" if volume == 100 else "CANCELLED"
  await _seed_managed_order(
    sessions,
    terminal_status=status,
    snapshot=_snapshot_report(terminal_status=status, snapshot_id="settlement"),
  )
  now = datetime.now(UTC) + timedelta(seconds=5)
  stamp = now.replace(tzinfo=None) - timedelta(seconds=1)
  async with sessions() as db, db.begin():
    correlation = await db.get(OrderCorrelation, "correlation-1")
    db.add(
      Order(
        id=9001,
        account_id="account-1",
        stock_code="605499.SH",
        sysid="broker-sys",
        time=stamp,
        type=OrderType.BUY,
        volume=100,
        price_type=OrderPriceType.LIMIT,
        price=10,
        traded_volume=volume,
        traded_price=10 if volume else 0,
        status=OrderStatus.SUCCEEDED
        if volume == 100
        else OrderStatus.PART_CANCEL
        if volume
        else OrderStatus.CANCELED,
      )
    )
    common = dict(
      account_id="account-1", order_id=9001, stock_code="605499.SH", order_type=23
    )
    reports = [("ORDER", {**common, "order_status": status, "traded_volume": volume})]
    if volume:
      db.add(
        Trade(
          id="trade-1",
          account_id="account-1",
          stock_code="605499.SH",
          order_id=9001,
          order_sysid="broker-sys",
          order_type=23,
          time=stamp,
          price=10,
          volume=volume,
          amount=volume * 10,
        )
      )
      reports.append(
        ("TRADE", {**common, "execution_id": "trade-1", "traded_volume": volume})
      )
    for kind, report in reports:
      db.add(
        StrategyRuntimeEvent(
          event_id=kind,
          business_key=kind,
          owner_type="STRATEGY_RUN",
          owner_id="plan-1",
          environment="LIVE",
          strategy_run_id="plan-1",
          client_order_id="client-1",
          broker_order_id="9001",
          event_type=kind,
          payload=_event_payload(correlation, report, business_key=kind),
          application_status="APPLIED",
          created_at=stamp,
          applied_at=stamp,
        )
      )
    db.add(
      TradeCommandOutbox(
        message_id="command-1",
        client_order_id="client-1",
        idempotency_key="command-1",
        device_id="device-1",
        account_id="account-1",
        owner_type="STRATEGY_RUN",
        owner_id="plan-1",
        environment="LIVE",
        payload={},
        delivery_status="ACKNOWLEDGED",
        expires_at=stamp,
      )
    )
  return engine, sessions, now


@pytest.mark.asyncio
@pytest.mark.parametrize("volume", [0, 40, 100])
async def test_settlement_proves_zero_partial_and_full_fill(monkeypatch, volume):
  engine, sessions, now = await seed_settlement(monkeypatch, volume)
  try:
    async with sessions() as db, db.begin():
      result = await read_legacy_order_settlement(
        db,
        account_id="account-1",
        run_id="plan-1",
        client_order_id="client-1",
        now=now,
      )
      assert result.blocker is None
      assert result.filled_volume == volume
      assert "ORDER" in result.runtime_event_ids
      assert not db.new and not db.dirty and not db.deleted
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_zero_fill_still_requires_explicit_terminal_quantity(monkeypatch):
  engine, sessions, now = await seed_settlement(monkeypatch, 0)
  try:
    async with sessions() as db, db.begin():
      event = await db.get(StrategyRuntimeEvent, "ORDER")
      report = dict(event.payload["report"])
      report.pop("traded_volume")
      event.payload = {**event.payload, "report": report}
    async with sessions() as db, db.begin():
      result = await read_legacy_order_settlement(
        db,
        account_id="account-1",
        run_id="plan-1",
        client_order_id="client-1",
        now=now,
      )
      assert result.blocker == "LEGACY_T_SETTLEMENT_RECEIPTS_INCOMPLETE"
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "damage,blocker",
  [
    ("pending", "ORDER_NOT_TERMINAL"),
    ("order", "ORDER_NOT_TERMINAL"),
    ("backlog", "RUNTIME_BACKLOG"),
    ("correlation", "OWNER_CONFLICT"),
    ("trade_gap", "FILL_GAP"),
    ("stale_zero", "FILL_GAP"),
    ("trade_scope", "BROKER_SCOPE_CONFLICT"),
    ("trade_identity", "RECEIPTS_INCOMPLETE"),
    ("receipt_scope", "RECEIPT_CONFLICT"),
    ("queued", "COMMAND_UNRESOLVED"),
    ("ack_only", "RECEIPTS_INCOMPLETE"),
    ("missing_cumulative", "RECEIPTS_INCOMPLETE"),
    ("future", "FUTURE_FACT"),
  ],
)
async def test_settlement_rejects_local_status_or_incomplete_receipts(
  monkeypatch, damage, blocker
):
  engine, sessions, now = await seed_settlement(monkeypatch, 40)
  try:
    async with sessions() as db, db.begin():
      if damage == "pending":
        (await db.get(PendingTradeOrder, "client-1")).status = "UNKNOWN"
      elif damage == "stale_zero":
        (await db.get(PendingTradeOrder, "client-1")).status = "RECONCILED_ZERO_FILL"
      elif damage == "order":
        (await db.get(Order, 9001)).status = OrderStatus.PART_SUCC
      elif damage == "backlog":
        (await db.get(StrategyRuntimeEvent, "TRADE")).application_status = "PENDING"
      elif damage == "correlation":
        (await db.get(OrderCorrelation, "correlation-1")).bucket = "swing"
      elif damage == "trade_gap":
        (await db.get(Trade, "trade-1")).volume = 20
      elif damage == "trade_scope":
        (await db.get(Trade, "trade-1")).account_id = "other-account"
      elif damage in {"trade_identity", "receipt_scope", "missing_cumulative"}:
        event = await db.get(
          StrategyRuntimeEvent, "ORDER" if damage == "missing_cumulative" else "TRADE"
        )
        report = dict(event.payload["report"])
        if damage == "trade_identity":
          report["execution_id"] = "different-trade"
        elif damage == "receipt_scope":
          report["account_id"] = "other-account"
        else:
          report.pop("traded_volume")
        event.payload = {**event.payload, "report": report}
      elif damage == "queued":
        (await db.get(TradeCommandOutbox, "command-1")).delivery_status = "QUEUED"
      elif damage == "ack_only":
        await db.delete(await db.get(StrategyRuntimeEvent, "ORDER"))
      elif damage == "future":
        (await db.get(StrategyRuntimeEvent, "ORDER")).applied_at = (
          now + timedelta(seconds=1)
        ).replace(tzinfo=None)
    async with sessions() as db, db.begin():
      result = await read_legacy_order_settlement(
        db,
        account_id="account-1",
        run_id="plan-1",
        client_order_id="client-1",
        now=now,
      )
      assert result.blocker == f"LEGACY_T_SETTLEMENT_{blocker}"
      assert result.filled_volume is None
      assert not db.new and not db.dirty and not db.deleted
  finally:
    await engine.dispose()


async def seed_local_terminal(monkeypatch, kind):
  from quantx_api.agent_api import _transition_place_order_command
  from quantx_infrastructure.services.trade_command_service import TradeCommandService

  engine, sessions = await _database(monkeypatch)
  await _seed_managed_order(
    sessions,
    terminal_status="QUEUED",
    snapshot=_snapshot_report(terminal_status="CANCELLED", snapshot_id="local"),
  )
  now = datetime.now(UTC) + timedelta(seconds=5)
  stamp = now.replace(tzinfo=None) - timedelta(seconds=1)
  async with sessions() as db, db.begin():
    pending = await db.get(PendingTradeOrder, "client-1")
    pending.broker_order_id = None
    pending.last_source_sequence = 0
    pending.last_source_event_at = None
    (await db.get(OrderCorrelation, "correlation-1")).broker_order_id = None
    command = TradeCommandOutbox(
      message_id="local-command",
      client_order_id="client-1",
      idempotency_key="local-command",
      device_id="device-1",
      account_id="account-1",
      owner_type="STRATEGY_RUN",
      owner_id="plan-1",
      environment="LIVE",
      delivery_status="QUEUED",
      attempts=0,
      payload=dict(
        command_kind="PLACE_ORDER",
        client_order_id="client-1",
        account_id="account-1",
        execution_mode="live",
        instrument_code="605499.SH",
        side="BUY",
        volume=100,
      ),
      expires_at=stamp - timedelta(seconds=1),
    )
    db.add(command)
    await db.flush()
    if kind == "expired":
      await _transition_place_order_command(
        db,
        command=command,
        requested_status="EXPIRED",
        reason="command_expired_before_delivery",
        now=stamp,
        pre_execution_proven=True,
      )
  if kind == "cancelled":
    async with sessions() as db:
      result = await TradeCommandService(db).request_strategy_buy_cancellations(
        strategy_run_id="plan-1",
        reason="maintenance",
      )
      assert len(result) == 1 and result[0].local_terminal
  return engine, sessions, now


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expired", "cancelled"])
async def test_actual_local_terminal_flow_requires_receipt_application(
  monkeypatch, kind
):
  engine, sessions, now = await seed_local_terminal(monkeypatch, kind)
  try:
    async with sessions() as db, db.begin():
      if kind == "expired":
        result = await read_legacy_order_settlement(
          db,
          account_id="account-1",
          run_id="plan-1",
          client_order_id="client-1",
          now=now,
        )
        assert result.blocker == "LEGACY_T_SETTLEMENT_RUNTIME_BACKLOG"
        # Application itself is a synthetic boundary; expiry/cancel above use
        # their actual service code, but this is not a running Engine or QMT.
        events = list(await db.scalars(select(StrategyRuntimeEvent)))
        assert len(events) == 1
        events[0].application_status = "APPLIED"
        events[0].applied_at = now.replace(tzinfo=None)
    async with sessions() as db, db.begin():
      result = await read_legacy_order_settlement(
        db,
        account_id="account-1",
        run_id="plan-1",
        client_order_id="client-1",
        now=now,
      )
      assert result.blocker is None
      assert result.filled_volume == 0
      assert result.evidence_kind == "LOCAL_UNDELIVERED_COMMAND"
      assert not db.new and not db.dirty and not db.deleted
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "damage,blocker",
  [
    ("delivered", "LOCAL_PRE_DELIVERY_PROOF_REQUIRED"),
    ("ack", "LOCAL_PRE_DELIVERY_PROOF_REQUIRED"),
    ("attempt", "LOCAL_PRE_DELIVERY_PROOF_REQUIRED"),
    ("source_event", "LOCAL_PRE_DELIVERY_PROOF_REQUIRED"),
    ("fill", "LOCAL_PRE_DELIVERY_PROOF_REQUIRED"),
    ("command_ref", "LOCAL_RECEIPT_CONFLICT"),
    ("no_event", "LOCAL_EXPIRY_PROOF_REQUIRED"),
    ("early_expiry", "LOCAL_EXPIRY_NOT_REACHED"),
    ("payload", "LOCAL_COMMAND_BINDING_CONFLICT"),
  ],
)
async def test_unknown_local_outcome_remains_an_obligation(
  monkeypatch, damage, blocker
):
  engine, sessions, now = await seed_local_terminal(monkeypatch, "expired")
  try:
    async with sessions() as db, db.begin():
      event = await db.scalar(select(StrategyRuntimeEvent))
      event.application_status = "APPLIED"
      event.applied_at = now.replace(tzinfo=None)
      command = await db.get(TradeCommandOutbox, "local-command")
      if damage == "delivered":
        command.delivered_at = now.replace(tzinfo=None)
      elif damage == "ack":
        command.acknowledged_at = now.replace(tzinfo=None)
      elif damage == "attempt":
        command.attempts = 1
      elif damage == "source_event":
        (await db.get(PendingTradeOrder, "client-1")).last_source_sequence = 1
      elif damage == "fill":
        (await db.get(TradeIntentRecord, "intent-1")).executed_volume = 1
      elif damage == "command_ref":
        event.payload = {
          **event.payload,
          "metadata": {**event.payload["metadata"], "command_message_id": "other"},
        }
      elif damage == "no_event":
        await db.delete(event)
      elif damage == "early_expiry":
        command.expires_at = (now + timedelta(seconds=1)).replace(tzinfo=None)
      elif damage == "payload":
        command.payload = {**command.payload, "command_kind": "CANCEL_ORDER"}
    async with sessions() as db, db.begin():
      result = await read_legacy_order_settlement(
        db,
        account_id="account-1",
        run_id="plan-1",
        client_order_id="client-1",
        now=now,
      )
      assert result.blocker == f"LEGACY_T_SETTLEMENT_{blocker}"
      assert result.filled_volume is None
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_parent", [False, True])
async def test_unsent_replacement_does_not_erase_prior_attempt_fills(
  monkeypatch, wrong_parent
):
  engine, sessions, now = await seed_local_terminal(monkeypatch, "expired")
  try:
    async with sessions() as db, db.begin():
      pending = await db.get(PendingTradeOrder, "client-1")
      original = (now - timedelta(seconds=20)).replace(tzinfo=None)
      values = {
        column.name: getattr(pending, column.name)
        for column in PendingTradeOrder.__table__.columns
      }
      values.update(
        client_order_id="prior-client",
        strategy_order_id="prior-order",
        broker_order_id="9001",
        status="CANCELLED",
        t_order_original_created_at=original,
        instrument_code="other" if wrong_parent else pending.instrument_code,
      )
      db.add(PendingTradeOrder(**values))
      await db.flush()
      pending.t_order_attempt = 1
      pending.t_order_parent_client_id = "prior-client"
      pending.t_order_original_created_at = original
      intent = await db.get(TradeIntentRecord, "intent-1")
      intent.executed_volume = 40
      intent.executed_price = 10
      intent.executed_time = original
      event = await db.scalar(select(StrategyRuntimeEvent))
      event.application_status = "APPLIED"
      event.applied_at = now.replace(tzinfo=None)
    async with sessions() as db, db.begin():
      result = await read_legacy_order_settlement(
        db,
        account_id="account-1",
        run_id="plan-1",
        client_order_id="client-1",
        now=now,
      )
      if wrong_parent:
        assert result.blocker == "LEGACY_T_SETTLEMENT_PARENT_CONFLICT"
      else:
        assert result.blocker is None and result.filled_volume == 0
      assert (await db.get(TradeIntentRecord, "intent-1")).executed_volume == 40
      assert (await db.get(PendingTradeOrder, "prior-client")).broker_order_id == "9001"
  finally:
    await engine.dispose()
