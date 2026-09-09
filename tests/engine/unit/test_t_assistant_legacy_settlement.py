"""Real ORM facts with synthetic broker receipts; no device or live dispatch."""

from datetime import UTC, datetime, timedelta

import pytest
from quantx_engine.report_processor import _event_payload
from quantx_engine.t_assistant_legacy_settlement import read_legacy_broker_settlement
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.enums import OrderPriceType, OrderStatus, OrderType
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade import Trade

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
      result = await read_legacy_broker_settlement(
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
      result = await read_legacy_broker_settlement(
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
      result = await read_legacy_broker_settlement(
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
