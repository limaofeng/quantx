"""Durable SQLite receipt evidence; no external account or broker access."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from quantx_application.t_trade_v3.daily_t_valuation import TValuationMark
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.services.live_t_valuation import LiveTValuationReader
from sqlalchemy import delete

from tests.infrastructure.test_account_capacity_service import (
  capacity_db as _capacity_db,
)

capacity_db = _capacity_db
NOW = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)
CODE = "600000.SH"


@pytest.fixture
async def db(capacity_db):
  await capacity_db.run_sync(
    lambda session: StrategyRuntimeEvent.__table__.create(session.connection())
  )
  capacity_db.add(AccountExecutionControl(account_id="account"))
  capacity_db.add(
    TTradeBatch(
      batch_id="batch",
      account_id="account",
      instrument_code=CODE,
      source_execution_owner_type="T_ASSISTANT_EXECUTION",
      source_execution_owner_id="old-execution",
      source_execution_environment="LIVE",
      environment="LIVE",
      entry_filled_volume=200,
      exit_filled_volume=100,
      commission_rate=0.0003,
      minimum_commission=5,
      stamp_tax_rate=0.0005,
      transfer_fee_rate=0.00001,
      policy_version=1,
      created_at=NOW,
      updated_at=NOW,
    )
  )
  capacity_db.add(
    AutoExitPlanRecord(
      plan_id="exit",
      account_id="account",
      instrument_code=CODE,
      source_type="T_TRADE_BATCH",
      source_id="batch",
      source_execution_owner_type="T_ASSISTANT_EXECUTION",
      source_execution_owner_id="old-execution",
      source_execution_environment="LIVE",
      environment="LIVE",
      protected_volume=200,
      remaining_volume=100,
      entry_avg_price=10,
      created_at=NOW,
      updated_at=NOW,
    )
  )
  for client, broker, side in [("entry", "101", "BUY"), ("exit", "102", "SELL")]:
    owner_type, owner_id = (
      ("T_ASSISTANT_EXECUTION", "old-execution")
      if side == "BUY"
      else ("EXIT_PLAN", "exit")
    )
    role = "ENTRY" if side == "BUY" else "EXIT"
    capacity_db.add(
      PendingTradeOrder(
        client_order_id=client,
        user_id="fixture",
        account_id="account",
        owner_type=owner_type,
        owner_id=owner_id,
        environment="LIVE",
        instrument_code=CODE,
        side=side,
        order_type="LIMIT",
        limit_price="10",
        volume=200,
        status="PARTIAL_FILLED",
        broker_order_id=broker,
        batch_id="batch",
        bucket="swing",
        t_trade_role=role,
        intent_id=client,
        created_at=NOW,
        updated_at=NOW,
      )
    )
    capacity_db.add(
      OrderCorrelation(
        id=client,
        client_order_id=client,
        broker_order_id=broker,
        account_id="account",
        owner_type=owner_type,
        owner_id=owner_id,
        environment="LIVE",
        intent_id=client,
        batch_id="batch",
        bucket="swing",
        t_trade_role=role,
        trace_id=client,
        created_at=NOW,
        updated_at=NOW,
      )
    )
  for index, client, price in [(1, "entry", 10), (2, "entry", 10), (3, "exit", 11)]:
    await add_fill(
      capacity_db, index, client, price, NOW - timedelta(seconds=30 - index)
    )
  await capacity_db.commit()
  return capacity_db


async def add_fill(db, index, client, price, at):
  order = await db.get(PendingTradeOrder, client)
  payload = dict(
    execution_id=str(index),
    order_id=int(order.broker_order_id),
    account_id="account",
    stock_code=CODE,
    traded_volume=100,
    traded_price=price,
    traded_time=int(at.timestamp()),
  )
  db.add(
    Trade(
      id=str(index),
      time=(at + timedelta(hours=8)).replace(tzinfo=None),
      price=price,
      volume=100,
      amount=price * 100,
      account_id="account",
      stock_code=CODE,
      order_id=int(order.broker_order_id),
      order_sysid="fixture",
      order_type=23 if order.side == "BUY" else 24,
      created_at=NOW,
      updated_at=NOW,
    )
  )
  db.add(
    StrategyRuntimeEvent(
      event_id=str(index),
      business_key=str(index),
      owner_type=order.owner_type,
      owner_id=order.owner_id,
      environment="LIVE",
      client_order_id=client,
      broker_order_id=order.broker_order_id,
      event_type="TRADE",
      application_status="APPLIED",
      created_at=NOW,
      applied_at=NOW,
      payload={
        "report": payload,
        "metadata": {"t_batch_id": "batch", "t_trade_role": order.t_trade_role.lower()},
      },
    )
  )


async def read(db, **changes):
  values = dict(
    account_id="account",
    as_of=NOW,
    previous_trading_day=(NOW - timedelta(days=1)).date(),
    opening_marks={},
    current_marks={CODE: TValuationMark(CODE, Decimal(11), NOW, "current")},
    mark_max_age_seconds=60,
  )
  values.update(changes)
  async with db.begin():
    return await LiveTValuationReader(db).read(**values)


async def test_partial_fills_charge_minimum_once_and_reconcile_daily_pnl(db):
  result = await read(db)
  assert result.cost_basis == "RULE_ESTIMATE"
  assert result.valuation.realized == Decimal("91.929")
  assert result.valuation.unrealized == Decimal("97.49")
  assert result.valuation.cash_flow + result.valuation.closing_value == Decimal(
    "189.419"
  )
  assert result.exposure_by_instrument == ((CODE, Decimal("1002.51")),)
  assert result.open_batch_ids == ("batch",)
  assert (await read(db)).evidence_hash == result.evidence_hash


@pytest.mark.parametrize(
  "damage,reason",
  [
    ("missing_event", "LINEAGE_INCOMPLETE"),
    ("unapplied", "UNAPPLIED_FILL"),
    ("volume", "FILL_CONFLICT"),
    ("source", "OWNER_CONFLICT"),
    ("batch_total", "BATCH_QUANTITY_CONFLICT"),
    ("future", "FUTURE_EVIDENCE"),
    ("missing_cost", "AMOUNT_INVALID"),
  ],
)
async def test_missing_conflicting_or_unavailable_facts_block(db, damage, reason):
  async with db.begin():
    if damage == "missing_event":
      await db.execute(
        delete(StrategyRuntimeEvent).where(StrategyRuntimeEvent.event_id == "1")
      )
    elif damage in {"unapplied", "source", "future"}:
      row = await db.get(StrategyRuntimeEvent, "1")
      if damage == "unapplied":
        row.application_status = "PENDING"
      elif damage == "source":
        row.payload = {
          **row.payload,
          "metadata": {**row.payload["metadata"], "t_batch_id": "wrong"},
        }
      else:
        row.applied_at = NOW + timedelta(seconds=1)
    elif damage == "volume":
      row = await db.get(Trade, "1")
      row.volume = 200
      row.updated_at = NOW
    else:
      row = await db.get(TTradeBatch, "batch")
      if damage == "batch_total":
        row.entry_filled_volume = 300
      else:
        row.minimum_commission = None
      row.updated_at = NOW
  with pytest.raises(ValueError, match=reason):
    await read(db)


async def test_overnight_rebases_to_prior_close_and_excludes_yesterday_fees(db):
  async with db.begin():
    for index in ("1", "2"):
      trade = await db.get(Trade, index)
      trade.time -= timedelta(days=1)
      trade.updated_at = NOW
      event = await db.get(StrategyRuntimeEvent, index)
      report = {
        **event.payload["report"],
        "traded_time": event.payload["report"]["traded_time"] - 86400,
      }
      event.payload = {**event.payload, "report": report}
  opening = {
    CODE: TValuationMark(
      CODE, Decimal("10.5"), (NOW - timedelta(days=1)).replace(hour=7), "prior-close"
    )
  }
  result = await read(db, opening_marks=opening)
  assert result.valuation.opening_value == 2100
  assert result.valuation.realized == Decimal("44.439")
  assert result.valuation.unrealized == 50
  with pytest.raises(ValueError, match="PRIOR_CLOSE_WINDOW_REQUIRED"):
    await read(
      db,
      opening_marks={
        CODE: TValuationMark(CODE, Decimal("10.5"), NOW - timedelta(days=1), "morning")
      },
    )
  with pytest.raises(ValueError, match="OPENING_MARK_REQUIRED"):
    await read(db)


async def test_empty_account_has_zero_t_valuation_from_complete_empty_tables(db):
  async with db.begin():
    for model in (
      StrategyRuntimeEvent,
      OrderCorrelation,
      PendingTradeOrder,
      Trade,
      AutoExitPlanRecord,
      TTradeBatch,
    ):
      await db.execute(delete(model))
  result = await read(db)
  assert result.valuation.realized == result.valuation.unrealized == 0
  assert result.open_batch_ids == ()


async def test_replacement_broker_order_has_its_own_minimum_commission(db):
  async with db.begin():
    original = await db.get(PendingTradeOrder, "entry")
    original.volume = 100
    original.status = "CANCELLED"
    original.updated_at = NOW
    values = {
      column.key: getattr(original, column.key)
      for column in original.__mapper__.column_attrs
    }
    values.update(
      client_order_id="replacement",
      broker_order_id="103",
      status="FILLED",
      t_order_attempt=1,
      t_order_parent_client_id="entry",
    )
    db.add(PendingTradeOrder(**values))
    db.add(
      OrderCorrelation(
        id="replacement",
        client_order_id="replacement",
        broker_order_id="103",
        account_id="account",
        owner_type="T_ASSISTANT_EXECUTION",
        owner_id="old-execution",
        environment="LIVE",
        intent_id="entry",
        batch_id="batch",
        bucket="swing",
        t_trade_role="ENTRY",
        trace_id="replacement",
        created_at=NOW,
        updated_at=NOW,
      )
    )
    await db.execute(
      delete(StrategyRuntimeEvent).where(StrategyRuntimeEvent.event_id == "2")
    )
    await db.execute(delete(Trade).where(Trade.id == "2"))
    await add_fill(db, 2, "replacement", 10, NOW - timedelta(seconds=28))
  result = await read(db)
  assert result.valuation.realized + result.valuation.unrealized == Decimal("184.419")
  assert result.exposure_by_instrument == ((CODE, Decimal("1005.01")),)
