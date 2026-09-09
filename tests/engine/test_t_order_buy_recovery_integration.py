"""Hermetic BUY attempt recovery through the real admission and outbox chain."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import StrategyContext
from quantx_domain.strategies.base import StrategyRunMode as DomainMode
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.enums import (
  AccountType,
  OrderPriceType,
  OrderStatus,
  OrderType,
  StrategyRunMode,
  StrategyRunStatus,
)
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)
from quantx_infrastructure.models.strategy import Strategy
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import account_risk_increase_admission as admission
from quantx_infrastructure.services import trade_command_service as commands
from quantx_infrastructure.services.exit_plan_authorization_service import (
  T_TRADE_ENTRY_APPROVAL_ACTION,
  T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY,
  build_t_trade_entry_exit_authorization_envelope,
  trade_confirmation_payload_fingerprint,
)
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from sqlalchemy import ARRAY, JSON, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class SimulatedProcessLoss(BaseException):
  """Skip application exception handlers, as an actual process loss would."""


@pytest.fixture
async def buy_chain(monkeypatch, request):
  clock = SimpleNamespace(now=getattr(request, "param", datetime(2026, 9, 7, 2)))
  monkeypatch.setattr(commands, "utcnow", lambda: clock.now)
  monkeypatch.setattr(admission, "utcnow", lambda: clock.now)
  monkeypatch.setattr(
    commands.time_utils, "now", lambda: clock.now + timedelta(hours=8)
  )
  models = [
    Account,
    Position,
    Strategy,
    StrategyRun,
    TradeIntentRecord,
    PendingTradeOrder,
    OrderCorrelation,
    TradeCommandOutbox,
    TTradeBatch,
    TTradeRolloutEvent,
    TTradeGlobalConfig,
    StrategyRuntimeEvent,
    Order,
    Trade,
    TradeConfirmationChallenge,
    AccountExecutionControl,
    AccountRiskIncreaseAdmissionBatch,
    AccountRiskIncreaseAdmissionItem,
  ]
  for model in models:
    for column in model.__table__.columns:
      if isinstance(column.type, ARRAY):
        monkeypatch.setattr(column, "type", JSON())
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda db: Base.metadata.create_all(
        db,
        tables=[model.__table__ for model in models],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  metadata = {
    "execution_mode": "MANUAL_CONFIRM",
    "approval_mode": "MANUAL",
    "t_trade_role": "ENTRY",
    "t_batch_id": "batch-1",
    "config_version": 1,
    "opportunity_schema_version": 3,
    "t_entry_order_policy_version": "TEntryOrderPolicy.v1",
    "t_order_reference_price": "10",
    "t_order_price_tick": "0.01",
    "t_order_limit_up": "11",
    "t_order_limit_down": "9",
    "t_trade_entry_approval_challenge_id": "challenge-1",
    "t_trade_entry_approval_user_id": "user-1",
    "t_trade_entry_approval_device_session_id": "session-1",
    "t_trade_entry_approval_channel": "WEB",
    "max_price_deviation_bps": 30,
    "exit_plan_id": "exit-batch-1",
  }
  metadata["exit_plan_template"] = (
    AshareIntradayTAssistantStrategy(
      StrategyContext(
        run_id="run-1",
        mode=DomainMode.LIVE,
        instruments=["600000.SH"],
        parameters={"account_id": "account-1"},
      )
    )
    .build_exit_plan_template(
      instrument_code="600000.SH",
      batch_id="batch-1",
      plan_id="exit-batch-1",
      policy={"config_version": 1},
    )
    .to_dict()
  )
  metadata["exit_plan_template"]["metadata"].update(
    {
      "strategy_run_id": "run-1",
      "account_id": "account-1",
    }
  )
  intent = TradeIntentRecord(
    id="intent-1",
    strategy_run_id="run-1",
    owner_type="STRATEGY_RUN",
    owner_id="run-1",
    environment="LIVE",
    account_id="account-1",
    idempotency_key="intent-key",
    instrument_code="600000.SH",
    direction="BUY",
    bucket="swing",
    status="APPROVED",
    target_volume=200,
    limit_price_hint=10,
    executed_volume=0,
    intent_metadata=metadata,
    created_at=clock.now,
  )
  payload = {
    "action": T_TRADE_ENTRY_APPROVAL_ACTION,
    "user_id": "user-1",
    "device_session_id": "session-1",
    "account_id": "account-1",
    "owner_type": "STRATEGY_RUN",
    "owner_id": "run-1",
    "environment": "LIVE",
    "run_id": "run-1",
    "intent_id": "intent-1",
    T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY: build_t_trade_entry_exit_authorization_envelope(
      intent
    ).to_dict(),
  }
  async with sessions() as db:
    db.add_all(
      [
        Strategy(
          id=1,
          name="T",
          file_path="t.py",
          class_name="AshareIntradayTAssistantStrategy",
        ),
        StrategyRun(
          id="run-1",
          name="T run",
          strategy_id=1,
          status=StrategyRunStatus.RUNNING,
          mode=StrategyRunMode.LIVE,
          parameters={"account_id": "account-1"},
          instruments=["600000.SH"],
        ),
        Account(
          id="account-row",
          account_id="account-1",
          account_type=list(AccountType)[0],
          cash=100000,
          total_asset=200000,
        ),
        Position(
          id="position-row",
          account_id="account-1",
          stock_code="600000.SH",
          volume=1000,
          can_use_volume=1000,
          market_value=10000,
        ),
        AccountExecutionControl(
          account_id="account-1",
          last_snapshot_id="snapshot-1",
          last_snapshot_hash="a" * 64,
          last_snapshot_at=clock.now,
        ),
        intent,
        TradeConfirmationChallenge(
          id="challenge-1",
          action=T_TRADE_ENTRY_APPROVAL_ACTION,
          user_id="user-1",
          device_session_id="session-1",
          account_id="account-1",
          owner_type="STRATEGY_RUN",
          owner_id="run-1",
          environment="LIVE",
          idempotency_key="challenge-key",
          payload_fingerprint=trade_confirmation_payload_fingerprint(payload),
          token_digest="c" * 64,
          consumed_at=clock.now,
          expires_at=clock.now + timedelta(minutes=5),
          payload=payload,
        ),
      ]
    )
    await db.commit()

  # Only external Agent/market and account-environment reads are replaced.
  # Authorization, sizing, risk, admission, dispatcher and enqueue stay real.
  async def account_control(service, account_id, **_kwargs):
    return await service.db.get(AccountExecutionControl, account_id)

  monkeypatch.setattr(
    commands.TradeCommandService, "_preview_live_authorization", account_control
  )
  monkeypatch.setattr(
    commands.TradeCommandService, "_require_live_authorization", account_control
  )
  device = SimpleNamespace(id="device-1", user_id="user-1")
  monkeypatch.setattr(
    commands.TradeCommandService, "_device_for", AsyncMock(return_value=device)
  )
  monkeypatch.setattr(
    commands.TradeCommandService, "_device_for_account", AsyncMock(return_value=device)
  )
  monkeypatch.setattr(
    commands.TradeCommandService, "_require_live_market_stream_ready", AsyncMock()
  )

  class CapacityReads:
    def __init__(self, _db):
      pass

    async def read(self, *_args, **_kwargs):
      return SimpleNamespace(
        obligation_watermark="e" * 64,
        snapshot_id="snapshot-1",
        available_cash=Decimal("100000"),
        available_volume=1000,
        unclaimed_volume=1000,
        available_by_bucket={"swing": 1000},
        unclaimed_by_bucket={"swing": 1000},
        protected_old_position_floor=0,
        old_inventory_claim_allocation={},
      )

  monkeypatch.setattr(commands, "AccountCapacityService", CapacityReads)
  readiness = {
    "stage": "LIVE",
    "rollout_enabled": True,
    "automation_ready": True,
    "can_approve": True,
    "kill_switch": False,
  }
  monkeypatch.setattr(
    TTradeOperationsService,
    "readiness",
    AsyncMock(side_effect=lambda *_args: dict(readiness)),
  )
  yield SimpleNamespace(
    sessions=sessions, clock=clock, metadata=metadata, readiness=readiness
  )
  await engine.dispose()


async def initial_order(chain):
  async with chain.sessions() as db:
    queued = await commands.TradeCommandService(db).enqueue_order_for_account(
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10.03"),
      volume=200,
      execution_ref=ExecutionOwnerRef.strategy_run("run-1"),
      environment=ExecutionEnvironment.LIVE,
      idempotency_key="first-order",
      trace_id="trace-1",
      strategy_run_id="run-1",
      strategy_order_id="strategy-order-1",
      intent_id="intent-1",
      batch_id="batch-1",
      bucket="swing",
      t_trade_role="ENTRY",
      request_metadata={
        key: value
        for key, value in chain.metadata.items()
        if key in commands._REQUEST_METADATA_ALLOWLIST
        and key not in commands._GENERATED_ORDER_METADATA_KEYS
      },
    )
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    pending.created_at = chain.clock.now
    await db.commit()
    return queued.client_order_id


async def converge_first_attempt(chain, client, filled):
  async with chain.sessions() as db:
    pending = await db.get(PendingTradeOrder, client)
    pending.broker_order_id = "101"
    pending.status = "CANCELLED" if filled else "RECONCILED_ZERO_FILL"
    correlation = await db.scalar(
      select(OrderCorrelation).where(OrderCorrelation.client_order_id == client)
    )
    correlation.broker_order_id = "101"
    intent = await db.get(TradeIntentRecord, "intent-1")
    intent.executed_volume = filled
    intent.executed_price = 10 if filled else 0
    intent.status = "PARTIAL_FILLED" if filled else "QUEUED"
    batch = await db.get(TTradeBatch, "batch-1")
    batch.entry_filled_volume = filled
    db.add(
      Order(
        id=101,
        account_id="account-1",
        stock_code="600000.SH",
        sysid="broker-101",
        time=chain.clock.now,
        type=OrderType.BUY,
        volume=200,
        price_type=OrderPriceType.LIMIT,
        price=10.03,
        traded_volume=filled,
        traded_price=10 if filled else 0,
        status=OrderStatus(54),
      )
    )
    db.add(
      StrategyRuntimeEvent(
        event_id="applied-order-101",
        business_key="order-proof-101",
        owner_type="STRATEGY_RUN",
        owner_id="run-1",
        environment="LIVE",
        strategy_run_id="run-1",
        client_order_id=client,
        broker_order_id="101",
        event_type="ORDER",
        payload={"report": {"status": "CANCELLED", "traded_volume": filled}},
        application_status="APPLIED",
        application_attempts=1,
        created_at=chain.clock.now,
      )
    )
    if filled:
      db.add(
        Trade(
          id="fill-101",
          order_id=101,
          order_sysid="broker-101",
          account_id="account-1",
          stock_code="600000.SH",
          time=chain.clock.now,
          volume=filled,
          price=10,
          amount=filled * 10,
          order_type=23,
        )
      )
      db.add(
        StrategyRuntimeEvent(
          event_id="applied-fill-101",
          business_key="trade-proof-101",
          owner_type="STRATEGY_RUN",
          owner_id="run-1",
          environment="LIVE",
          strategy_run_id="run-1",
          client_order_id=client,
          broker_order_id="101",
          event_type="TRADE",
          payload={
            "report": {
              "execution_id": "fill-101",
              "traded_volume": filled,
              "traded_price": 10,
            }
          },
          application_status="APPLIED",
          application_attempts=1,
          created_at=chain.clock.now,
        )
      )
    await db.commit()


async def replace(chain, client, *, crash_after_stage=False, crash_after_prepare=False):
  quote = chain.clock.now.replace(tzinfo=timezone.utc)
  async with chain.sessions() as db:

    def after_commit(session):
      staged = any(
        isinstance(row, TradeIntentRecord)
        and row.status == "EXECUTION_READY"
        and dict(
          dict(row.intent_metadata or {}).get("risk_increase_order_request") or {}
        ).get("t_order_parent_client_id")
        == client
        for row in session.identity_map.values()
      )
      prepared = any(
        isinstance(row, AccountRiskIncreaseAdmissionBatch) and row.status == "PREPARED"
        for row in session.identity_map.values()
      )
      if (crash_after_stage and staged) or (crash_after_prepare and prepared):
        raise SimulatedProcessLoss("simulated process loss after durable stage")

    if crash_after_stage or crash_after_prepare:
      event.listen(db.sync_session, "after_commit", after_commit)
    try:
      return await commands.TradeCommandService(db).replace_t_order(
        client_order_id=client,
        quote_at=quote,
        reference_price=Decimal("10"),
        price_tick=Decimal("0.01"),
        limit_up=Decimal("11"),
        limit_down=Decimal("9"),
        market_data=MarketDataSnapshot(
          "600000.SH",
          timestamp=quote,
          price=10,
          price_tick=0.01,
          limit_up=11,
          limit_down=9,
          bid_price=[9.99],
          ask_price=[10],
        ),
      )
    finally:
      if crash_after_stage or crash_after_prepare:
        event.remove(db.sync_session, "after_commit", after_commit)


@pytest.mark.asyncio
@pytest.mark.parametrize("filled", [0, 100])
async def test_real_buy_attempt_dispatch_preserves_identity_and_deduplicates(
  buy_chain, filled
):
  client = await initial_order(buy_chain)
  await converge_first_attempt(buy_chain, client, filled)
  buy_chain.clock.now += timedelta(seconds=31)
  queued = await replace(buy_chain, client)
  assert (await replace(buy_chain, client)).client_order_id == queued.client_order_id
  async with buy_chain.sessions() as db:
    attempts = list(
      (
        await db.scalars(
          select(PendingTradeOrder).order_by(PendingTradeOrder.t_order_attempt)
        )
      ).all()
    )
    assert len(attempts) == 2
    assert [row.t_order_attempt for row in attempts] == [0, 1]
    assert attempts[1].volume == 200 - filled
    assert attempts[1].t_order_parent_client_id == client
    assert {row.intent_id for row in attempts} == {"intent-1"}
    assert {row.owner_id for row in attempts} == {"run-1"}
    assert {row.owner_type for row in attempts} == {"STRATEGY_RUN"}
    assert {row.environment for row in attempts} == {"LIVE"}
    assert {row.strategy_order_id for row in attempts} == {"strategy-order-1"}
    assert len({row.t_order_original_created_at for row in attempts}) == 1
    assert {row.trace_id for row in attempts} == {"trace-1"}
    batches = list((await db.scalars(select(AccountRiskIncreaseAdmissionBatch))).all())
    assert len(batches) == 2
    assert all(row.status == "COMMITTED" for row in batches)


@pytest.mark.asyncio
async def test_staged_buy_crash_recovers_with_new_quote_real_dispatcher(buy_chain):
  client = await initial_order(buy_chain)
  await converge_first_attempt(buy_chain, client, 100)
  buy_chain.clock.now += timedelta(seconds=31)
  with pytest.raises(SimulatedProcessLoss, match="simulated process loss"):
    await replace(buy_chain, client, crash_after_stage=True)
  async with buy_chain.sessions() as db:
    intent = await db.get(TradeIntentRecord, "intent-1")
    assert intent.status == "EXECUTION_READY"
    staged = intent.intent_metadata["risk_increase_order_request"]
    old_quote = staged["request_metadata"]["quote_timestamp"]
    assert len(list((await db.scalars(select(PendingTradeOrder))).all())) == 1
  buy_chain.clock.now += timedelta(seconds=5)
  queued = await replace(buy_chain, client)
  async with buy_chain.sessions() as db:
    latest = await db.get(PendingTradeOrder, queued.client_order_id)
    assert latest.request_metadata["quote_timestamp"] != old_quote
    assert latest.volume == 100
    assert latest.t_order_parent_client_id == client
    assert latest.owner_id == "run-1" and latest.trace_id == "trace-1"
    assert len(list((await db.scalars(select(TradeCommandOutbox))).all())) == 2


@pytest.mark.asyncio
async def test_prepared_buy_crash_refresh_supersedes_old_admission(buy_chain):
  client = await initial_order(buy_chain)
  await converge_first_attempt(buy_chain, client, 100)
  buy_chain.clock.now += timedelta(seconds=31)
  with pytest.raises(SimulatedProcessLoss, match="simulated process loss"):
    await replace(buy_chain, client, crash_after_prepare=True)
  async with buy_chain.sessions() as db:
    prepared = await db.scalar(
      select(AccountRiskIncreaseAdmissionBatch).where(
        AccountRiskIncreaseAdmissionBatch.status == "PREPARED",
      )
    )
    assert prepared is not None
    prepared_id = prepared.admission_batch_id
    assert len(list((await db.scalars(select(TradeCommandOutbox))).all())) == 1
  buy_chain.clock.now += timedelta(seconds=11)
  queued = await replace(buy_chain, client)
  async with buy_chain.sessions() as db:
    assert (
      await db.get(AccountRiskIncreaseAdmissionBatch, prepared_id)
    ).status == "SUPERSEDED"
    intent = await db.get(TradeIntentRecord, "intent-1")
    assert (
      await db.get(AccountRiskIncreaseAdmissionBatch, intent.admission_batch_id)
    ).status == "COMMITTED"
    assert (await db.get(PendingTradeOrder, queued.client_order_id)).volume == 100
    assert len(list((await db.scalars(select(TradeCommandOutbox))).all())) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True])
async def test_staged_buy_at_total_deadline_releases_ready_without_new_order(buy_chain, prepared):
  from quantx_engine.t_order_lifecycle import advance_order

  client = await initial_order(buy_chain)
  await converge_first_attempt(buy_chain, client, 100)
  buy_chain.clock.now += timedelta(seconds=31)
  with pytest.raises(SimulatedProcessLoss, match="simulated process loss"):
    await replace(buy_chain, client, crash_after_stage=not prepared, crash_after_prepare=prepared)
  buy_chain.clock.now += timedelta(seconds=30)
  async with buy_chain.sessions() as db:
    await advance_order(db, client, now=buy_chain.clock.now)
    await db.commit()
  async with buy_chain.sessions() as db:
    intent = await db.get(TradeIntentRecord, "intent-1")
    assert intent.status != "EXECUTION_READY"
    assert len(list((await db.scalars(select(PendingTradeOrder))).all())) == 1
    pending = await db.get(PendingTradeOrder, client)
    assert pending.request_metadata.get("t_order_lifecycle_finished") is True
    assert not list((await db.scalars(select(AccountRiskIncreaseAdmissionBatch).where(
      AccountRiskIncreaseAdmissionBatch.status == "PREPARED",
    ))).all())


@pytest.mark.asyncio
async def test_live_auto_buy_rechecks_readiness_on_replacement(buy_chain):
  async with buy_chain.sessions() as db:
    intent = await db.get(TradeIntentRecord, "intent-1")
    intent.intent_metadata = {**intent.intent_metadata, "approval_mode": "LIVE_AUTO"}
    await db.commit()
  client = await initial_order(buy_chain)
  await converge_first_attempt(buy_chain, client, 100)
  buy_chain.clock.now += timedelta(seconds=31)
  buy_chain.readiness["can_approve"] = False
  with pytest.raises(
    commands.AgentUnavailableError, match="LIVE_AUTO_AUTHORITY_NOT_READY"
  ):
    await replace(buy_chain, client)
  buy_chain.readiness["can_approve"] = True
  queued = await replace(buy_chain, client)
  async with buy_chain.sessions() as db:
    assert (
      await db.get(PendingTradeOrder, queued.client_order_id)
    ).t_order_attempt == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "owner_mismatch"])
async def test_manual_buy_rejects_missing_or_wrong_owner_challenge(buy_chain, failure):
  async with buy_chain.sessions() as db:
    challenge = await db.get(TradeConfirmationChallenge, "challenge-1")
    if failure == "missing":
      await db.delete(challenge)
    else:
      challenge.payload = {**challenge.payload, "owner_id": "different-run"}
      challenge.payload_fingerprint = trade_confirmation_payload_fingerprint(
        challenge.payload
      )
    await db.commit()
  with pytest.raises(commands.AgentUnavailableError, match="CHALLENGE"):
    await initial_order(buy_chain)
  async with buy_chain.sessions() as db:
    assert list((await db.scalars(select(TradeCommandOutbox))).all()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("buy_chain", [datetime(2026, 9, 7, 6, 49, 20)], indirect=True)
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("filled", [0, 100])
async def test_cutoff_retires_crashed_buy_replacement_and_never_resumes_next_day(
  buy_chain, prepared, filled
):
  from quantx_engine.t_order_lifecycle import advance_order
  from quantx_infrastructure.services.t_order_lifecycle_state import (
    t_order_lifecycle_active,
  )

  client = await initial_order(buy_chain)
  await converge_first_attempt(buy_chain, client, filled)
  buy_chain.clock.now += timedelta(seconds=31)  # 14:49:51 Shanghai.
  with pytest.raises(SimulatedProcessLoss, match="simulated process loss"):
    await replace(
      buy_chain, client,
      crash_after_stage=not prepared, crash_after_prepare=prepared,
    )
  buy_chain.clock.now += timedelta(seconds=9)  # Cutoff, still before total TTL.
  async with buy_chain.sessions() as db:
    pending = await db.get(PendingTradeOrder, client)
    assert t_order_lifecycle_active(pending, buy_chain.clock.now)
    assert (await db.get(TradeIntentRecord, "intent-1")).status == "EXECUTION_READY"
    await advance_order(db, client, now=buy_chain.clock.now)
    await db.commit()
  for current in (buy_chain.clock.now, datetime(2026, 9, 8, 1, 30)):
    buy_chain.clock.now = current
    async with buy_chain.sessions() as db:
      await advance_order(db, client, now=current)
      await db.commit()
    async with buy_chain.sessions() as db:
      pending = await db.get(PendingTradeOrder, client)
      assert pending.request_metadata["t_order_lifecycle_finished"] is True
      assert pending.owner_id == "run-1" and pending.trace_id == "trace-1"
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status != "EXECUTION_READY"
      assert intent.executed_volume == filled
      assert len(list((await db.scalars(select(PendingTradeOrder))).all())) == 1
      assert len(list((await db.scalars(select(TradeCommandOutbox))).all())) == 1
      assert not list((await db.scalars(select(AccountRiskIncreaseAdmissionBatch).where(
        AccountRiskIncreaseAdmissionBatch.status == "PREPARED",
      ))).all())
      trades = list((await db.scalars(select(Trade))).all())
      assert sum(trade.volume for trade in trades) == filled
