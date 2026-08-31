import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentReportInbox,
  PendingTradeOrder,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.services.account_capacity_service import (
  AccountCapacityService,
  buy_cash_required,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def capacity_db():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda db: Base.metadata.create_all(
        db,
        tables=[
          model.__table__
          for model in (
            AccountExecutionControl,
            AgentReportInbox,
            PendingTradeOrder,
            TradeCommandOutbox,
            TTradeBatch,
            AutoExitPlanRecord,
            Order,
            Trade,
            Position,
          )
        ],
      )
    )
  async with async_sessionmaker(engine, expire_on_commit=False)() as db:
    yield db
  await engine.dispose()


async def snapshot(db, *, cash=10000, volume=1000, orders=None):
  stamp = utcnow()
  payload = {
    "snapshot_id": "snapshot",
    "source_event_at": stamp.isoformat() + "Z",
    "is_complete": True,
    "accounts": [{"account_id": "account", "cash": cash}],
    "positions_by_account": {
      "account": [{"stock_code": "600000.SH", "can_use_volume": volume}]
    },
    "orders": list(orders or []),
    "section_completeness_by_account": {
      "account": dict.fromkeys(("account", "positions", "orders", "trades"), True)
    },
    "snapshot_authority_by_account": {
      "account": {
        "initial_status": 0,
        "final_status": 0,
        "stable": True,
        "snapshot_eligible": True,
        "status_name": "OK",
        "reason_code": "AUTHORITATIVE",
      }
    },
  }
  digest = hashlib.sha256(
    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
  ).hexdigest()
  payload["snapshot_hash"] = digest
  control = AccountExecutionControl(
    account_id="account",
    last_snapshot_id="snapshot",
    last_snapshot_hash=digest,
    last_snapshot_at=stamp,
    authorization_state="ENABLED",
    reconcile_status="READY",
  )
  db.add_all(
    [
      control,
      AgentReportInbox(
        message_id="snapshot",
        device_id="device",
        message_type="delta_report",
        protocol_version="1.1",
        raw_payload_hash=digest,
        business_idempotency_key="snapshot",
        payload=payload,
        received_at=stamp,
        processing_status="PROCESSED",
      ),
    ]
  )
  await db.commit()
  return control


def pending(key="first", **kwargs):
  values = dict(
    client_order_id=key,
    user_id="user",
    account_id="account",
    instrument_code="600000.SH",
    side="BUY",
    order_type="FIX_PRICE",
    limit_price="10",
    volume=600,
    status="QUEUED",
    execution_mode="live",
  )
  values.update(kwargs)
  return PendingTradeOrder(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["QUEUED", "DELIVERED", "FILLED", "PARTIAL_FILLED"])
@pytest.mark.parametrize("instrument_code", ["600000.SH", "600001.SH"])
async def test_cash_claim_survives_old_snapshot_including_later_fills(
  capacity_db, status, instrument_code
):
  control = await snapshot(capacity_db)
  capacity_db.add(
    pending(
      status=status,
      instrument_code=instrument_code,
      broker_order_id="11" if status == "FILLED" else None,
    )
  )
  # PAPER commands never spend any part of the LIVE account.
  capacity_db.add(pending("paper", volume=100000, execution_mode="paper"))
  await capacity_db.commit()
  capacity = await AccountCapacityService(capacity_db).read(
    control, instrument_code="600000.SH"
  )
  assert capacity.available_cash == Decimal(10000) - buy_cash_required(10, 600)
  with pytest.raises(AgentUnavailableError, match="ACCOUNT_CASH_CAPACITY_EXCEEDED"):
    await TradeCommandService(capacity_db)._require_account_capacity(
      control,
      instrument_code="600000.SH",
      side="BUY",
      limit_price=Decimal(10),
      volume=600,
      intent=None,
      batch_id="",
      t_trade_role="",
    )
  assert (
    await capacity_db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 0
  )


@pytest.mark.asyncio
async def test_snapshot_observed_broker_freeze_is_not_reserved_twice(capacity_db):
  control = await snapshot(
    capacity_db, cash=4000, orders=[{"account_id": "account", "order_id": "11"}]
  )
  capacity_db.add(pending(broker_order_id="11", status="SUBMITTED"))
  await capacity_db.commit()
  capacity = await AccountCapacityService(capacity_db).read(
    control, instrument_code="600000.SH"
  )
  assert capacity.available_cash == 4000


@pytest.mark.asyncio
async def test_empty_broker_identity_cannot_cover_another_queued_order(capacity_db):
  control = await snapshot(
    capacity_db,
    orders=[{"account_id": "account", "client_order_id": "observed"}],
  )
  capacity_db.add(pending())
  await capacity_db.commit()
  capacity = await AccountCapacityService(capacity_db).read(
    control, instrument_code="600000.SH"
  )
  assert capacity.available_cash == Decimal(10000) - buy_cash_required(10, 600)


@pytest.mark.asyncio
@pytest.mark.parametrize("trade_volume", [0, 200, 600])
async def test_t_order_terminal_before_fill_projection_keeps_old_inventory_claim(
  capacity_db,
  trade_volume,
):
  control = await snapshot(capacity_db)
  capacity_db.add_all(
    [
      pending(
        batch_id="batch", t_trade_role="ENTRY", broker_order_id="11", status="FILLED"
      ),
      TTradeBatch(
        batch_id="batch",
        account_id="account",
        instrument_code="600000.SH",
        strategy_run_id="run",
        execution_mode="live",
        entry_filled_volume=0,
        exit_filled_volume=0,
      ),
    ]
  )
  if trade_volume:
    capacity_db.add(
      Trade(
        id="fill",
        account_id="account",
        stock_code="600000.SH",
        order_id=11,
        order_sysid="11",
        order_type=23,
        time=utcnow(),
        volume=trade_volume,
        price=10,
        amount=10 * trade_volume,
      )
    )
  await capacity_db.commit()
  capacity = await AccountCapacityService(capacity_db).read(
    control, instrument_code="600000.SH"
  )
  assert capacity.unclaimed_volume == 400


@pytest.mark.asyncio
async def test_positive_t_cannot_buy_more_than_unclaimed_old_inventory(capacity_db):
  control = await snapshot(capacity_db, cash=100000, volume=100)
  service = TradeCommandService(capacity_db)
  with pytest.raises(AgentUnavailableError, match="T_TRADE_EXIT_CAPACITY_EXCEEDED"):
    await service._require_account_capacity(
      control,
      instrument_code="600000.SH",
      side="BUY",
      limit_price=Decimal(10),
      volume=1000,
      intent=None,
      batch_id="t-new",
      t_trade_role="ENTRY",
    )
  await service._require_account_capacity(
    control,
    instrument_code="600000.SH",
    side="BUY",
    limit_price=Decimal(10),
    volume=100,
    intent=None,
    batch_id="t-new",
    t_trade_role="ENTRY",
  )


@pytest.mark.asyncio
async def test_plan_t_batch_and_queued_sell_share_inventory(capacity_db):
  control = await snapshot(capacity_db, cash=100000, volume=1000)
  capacity_db.add_all(
    [
      pending("sell", side="SELL", volume=100),
      pending("t-entry", batch_id="batch", t_trade_role="ENTRY", volume=200),
      TTradeBatch(
        batch_id="batch",
        account_id="account",
        instrument_code="600000.SH",
        strategy_run_id="run",
        execution_mode="live",
        entry_filled_volume=100,
        exit_filled_volume=0,
      ),
      AutoExitPlanRecord(
        plan_id="exit",
        source_type="MANUAL_POSITION",
        source_id="exit",
        account_id="account",
        instrument_code="600000.SH",
        execution_mode="live",
        status="ERROR",
        protected_volume=400,
        remaining_volume=400,
        entry_avg_price=10,
      ),
    ]
  )
  await capacity_db.commit()
  capacity = await AccountCapacityService(capacity_db).read(
    control, instrument_code="600000.SH"
  )
  assert capacity.available_volume == 900
  assert capacity.unclaimed_volume == 200


@pytest.mark.asyncio
async def test_missing_durable_intent_cannot_create_pending_or_outbox():
  db = SimpleNamespace(get=AsyncMock(return_value=None), add=AsyncMock())
  service = TradeCommandService(db)
  with pytest.raises(AgentUnavailableError, match="TRADE_INTENT_NOT_ACCEPTED"):
    await service.enqueue_order(
      user_id="user",
      account_id="account",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal(10),
      volume=100,
      strategy_run_id="run",
      strategy_order_id="order",
      intent_id="missing",
    )
  db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "changes",
  [
    {"strategy_run_id": "other"},
    {"account_id": "other"},
    {"instrument_code": "600001.SH"},
    {"direction": "SELL"},
    {"bucket": "core"},
  ],
)
async def test_durable_intent_cannot_route_for_a_different_owner_scope(changes):
  accepted = SimpleNamespace(
    **{
      "strategy_run_id": "run",
      "account_id": "account",
      "instrument_code": "600000.SH",
      "direction": "BUY",
      "bucket": "swing",
      **changes,
    }
  )
  db = SimpleNamespace(get=AsyncMock(return_value=accepted), add=AsyncMock())
  with pytest.raises(AgentUnavailableError, match="TRADE_INTENT_SCOPE_MISMATCH"):
    await TradeCommandService(db).enqueue_order(
      user_id="user",
      account_id="account",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal(10),
      volume=100,
      strategy_run_id="run",
      strategy_order_id="order",
      intent_id="intent",
      bucket="swing",
    )
  db.add.assert_not_called()


@pytest.mark.asyncio
async def test_t_entry_cannot_remove_its_role_to_bypass_old_share_capacity():
  db = SimpleNamespace(add=AsyncMock())
  service = TradeCommandService(db)
  service._require_durable_order_intent = AsyncMock(
    return_value=SimpleNamespace(
      intent_metadata={"t_trade_role": "ENTRY", "t_batch_id": "batch"},
    )
  )
  with pytest.raises(AgentUnavailableError, match="TRADE_INTENT_SCOPE_MISMATCH"):
    await service.enqueue_order(
      user_id="user",
      account_id="account",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal(10),
      volume=100,
      strategy_run_id="run",
      strategy_order_id="order",
      intent_id="intent",
      bucket="swing",
    )
  db.add.assert_not_called()


@pytest.mark.asyncio
async def test_live_market_buy_cannot_claim_cash_without_an_executable_price_cap():
  db = SimpleNamespace(add=AsyncMock())
  service = TradeCommandService(db)
  service._require_live_authorization = AsyncMock(
    return_value=SimpleNamespace(account_id="account")
  )
  with pytest.raises(
    AgentUnavailableError, match="ACCOUNT_CAPACITY_LIMIT_PRICE_REQUIRED"
  ):
    await service.enqueue_order(
      user_id="user",
      account_id="account",
      instrument_code="600000.SH",
      side="BUY",
      order_type="LATEST_PRICE",
      limit_price=Decimal(10),
      volume=100,
      execution_mode="live",
    )
  db.add.assert_not_called()


@pytest.mark.asyncio
async def test_accepted_entry_retry_recovers_without_reserving_cash_again(capacity_db):
  control = await snapshot(capacity_db, cash=0)
  service = TradeCommandService(capacity_db)
  capacity_db.add_all(
    [
      pending(intent_id="intent", strategy_run_id="run", strategy_order_id="order"),
      TradeCommandOutbox(
        message_id="message",
        client_order_id="first",
        account_id="account",
        device_id="device",
        delivery_status="DELIVERED",
        expires_at=utcnow(),
        payload={},
        idempotency_key=service.order_idempotency_digest(
          user_id="user",
          account_id="account",
          idempotency_key="original",
        ),
      ),
    ]
  )
  await capacity_db.commit()
  service._require_live_authorization = AsyncMock(return_value=control)
  service._require_account_capacity = AsyncMock(
    side_effect=AssertionError("retry reserved cash")
  )
  arguments = dict(
    account_id="account",
    instrument_code="600000.SH",
    side="BUY",
    order_type="FIX_PRICE",
    limit_price=Decimal(10),
    volume=600,
    execution_mode="live",
    intent_id="intent",
    strategy_run_id="run",
    strategy_order_id="order",
    idempotency_key="original",
  )
  result = await service.enqueue_order_for_account(**arguments)
  assert result.client_order_id == "first"
  assert result.message_id == "message"
  service._require_account_capacity.assert_not_awaited()
  with pytest.raises(AgentUnavailableError, match="TRADE_COMMAND_RETRY_MISMATCH"):
    await service.enqueue_order_for_account(**{**arguments, "volume": 700})
  with pytest.raises(AgentUnavailableError, match="TRADE_INTENT_ALREADY_ROUTED"):
    await service.enqueue_order_for_account(**{**arguments, "idempotency_key": "new"})
  assert (
    await capacity_db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 1
  )
