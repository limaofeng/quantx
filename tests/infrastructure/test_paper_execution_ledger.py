"""PAPER ledger transactions; the fixture sink is not an ExitPlan integration."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, Position, PriceType
from quantx_domain.strategies.base import (
  ExitPlanIntentOrigin,
  TAssistantExecutionIntentOrigin,
  TradeIntent,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_domain.trading.risk_checker import TradingRiskChecker
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionFillRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from quantx_infrastructure.repositories.trade_intent_repository import (
  TradeIntentRepository,
)
from quantx_infrastructure.services.account_risk_increase_admission import (
  AccountRiskIncreaseAdmissionSequencer,
)
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.trade_intent_intake import trade_intent_record_data
from sqlalchemy import event, func, select

from tests.infrastructure.test_t_allocation_repository import _claim, _prepared, _seed
from tests.infrastructure.test_t_allocation_repository import (
  sessions as _allocation_sessions,
)
from tests.infrastructure.test_t_assistant_runtime_repository import NOW
from tests.infrastructure.test_t_assistant_runtime_repository import (
  sessions as _base_sessions,
)

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions


@pytest.fixture
async def sessions(allocation_sessions):
  # Explicit BEGIN prevents SQLite legacy SAVEPOINT release from committing
  # independently of the caller's outer transaction.
  event.listen(
    allocation_sessions.kw["bind"].sync_engine,
    "begin",
    lambda connection: connection.exec_driver_sql("BEGIN"),
  )
  async with allocation_sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          PaperExecutionAccountRecord.__table__,
          PaperExecutionEventRecord.__table__,
          PaperExecutionOrderRecord.__table__,
          PaperExecutionFillRecord.__table__,
          AccountRiskIncreaseAdmissionBatch.__table__,
          AccountRiskIncreaseAdmissionItem.__table__,
          AutoExitPlanRecord.__table__,
        ],
      )
    )
  return allocation_sessions


def seed_values():
  return dict(
    cash=100000.0,
    non_trading_asset_value=50.0,
    positions={
      "600000.SH": Position(
        "600000.SH",
        long_volume=1000,
        available_volume=1000,
        long_avg_price=10.0,
        last_price=10.0,
        market_value=10000.0,
      )
    },
    bucket_checkpoint={
      "instruments": {
        "600000.SH": {
          "swing": {
            "bucket": "swing",
            "total_volume": 1000,
            "available_volume": 1000,
            "today_buy_volume": 0,
            "frozen_volume": 0,
            "avg_price": 10.0,
            "last_price": 10.0,
            "market_value": 10000.0,
          }
        }
      }
    },
    seed_as_of=NOW,
    seed_snapshot_id="explicit-seed",
    seed_snapshot_hash="a" * 64,
  )


def quote(seconds, depth=200):
  return MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=NOW + timedelta(seconds=seconds),
    price=9.9,
    source="accepted-tick",
    limit_up=11.0,
    limit_down=9.0,
    bid_price=[9.89, 9.88, 9.87, 9.86, 9.85],
    ask_price=[9.9, 9.91, 9.92, 9.93, 9.94],
    bid_vol=[depth] * 5,
    ask_vol=[depth] * 5,
  )


async def setup(
  sessions,
  sink,
  *,
  initial_seed=None,
  allocation_cash=None,
  order_price=9.9,
  candidate_ttl_seconds=60,
  allocation_at=NOW,
  admission_at=NOW,
):
  snapshot, candidates = await _seed(sessions, authorization="AUTO")
  if allocation_cash is not None:
    snapshot = replace(snapshot, available_cash=Decimal(allocation_cash))
  candidates = tuple(
    replace(candidate, expires_at=NOW + timedelta(seconds=candidate_ttl_seconds))
    for candidate in candidates
  )
  execution_id = snapshot.cut.execution_ref.owner_id
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      await ledger.initialize(
        execution_id=execution_id,
        account_id="account-1",
        **(initial_seed or seed_values()),
      )
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates, now=allocation_at)
      claim = await _claim(repository, batch, snapshot, candidates, now=allocation_at)
      await repository.commit(
        claim=claim, snapshot=snapshot, candidates=candidates, now=allocation_at
      )
    async with db.begin():
      state = await ledger.get_snapshot(execution_id=execution_id)
      sequencer = AccountRiskIncreaseAdmissionSequencer(
        db, environment=ExecutionEnvironment.PAPER, paper_execution_id=execution_id
      )
      batch = await sequencer.prepare_batch(
        account_id="account-1",
        account_snapshot_id=state["snapshot_id"],
        account_snapshot_hash=state["snapshot_hash"],
        obligation_watermark="b" * 64,
        now=admission_at,
        commit=False,
      )
      claim = await sequencer.claim_batch(
        admission_batch_id=batch.admission_batch_id,
        processing_owner="paper",
        now=admission_at,
        commit=False,
      )
      await sequencer.commit_batch(
        admission_batch_id=batch.admission_batch_id,
        fence_token=claim.fence_token,
        account_snapshot_id=state["snapshot_id"],
        account_snapshot_hash=state["snapshot_hash"],
        obligation_watermark="b" * 64,
        now=admission_at,
        commit=False,
      )
    raw = await db.get(TradeIntentRecord, candidates[0].intent_id)
    intent = TradeIntent(
      strategy_id=raw.strategy_id,
      instrument_code=raw.instrument_code,
      direction="BUY",
      bucket=raw.bucket,
      reason=raw.reason,
      target_amount=raw.target_amount,
      metadata=dict(raw.intent_metadata),
      intent_id=raw.id,
      execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id),
      origin=TAssistantExecutionIntentOrigin(
        execution_id,
        "paper-test",
        candidate_id=candidates[0].candidate_id,
        opportunity_id=candidates[0].candidate_id,
        cycle_id=snapshot.cycle_id,
      ),
    )
    draft = OrderSizer().draft_intent(
      intent,
      OrderType.BUY,
      order_price,
      {"cash": 100000.0, "total_asset": 110050.0},
      {"available_volume": 1000},
      allocated_amount_cap=Decimal(allocation_cash)
      if allocation_cash is not None
      else 1000,
    )
    request = OrderRequest(
      instrument_code=raw.instrument_code,
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=draft.sized_volume,
      price=order_price,
      execution_ref=intent.execution_ref,
      environment=ExecutionEnvironment.PAPER,
      metadata={
        "bucket": "swing",
        "intent_id": intent.intent_id,
        "order_expire_at_ms": int((NOW + timedelta(seconds=60)).timestamp() * 1000),
      },
    )
    risk = await TradingRiskChecker(
      strict_market_data=True, strict_limit_data=True
    ).evaluate_order(
      request,
      account={"cash": 100000.0, "total_asset": 110050.0},
      position={"available_volume": 1000},
      market_data=replace(
        quote(0),
        price=order_price,
        limit_up=order_price * 1.1,
        limit_down=order_price * 0.9,
        bid_price=[order_price - 0.01 * index for index in range(1, 6)],
        ask_price=[order_price + 0.01 * index for index in range(5)],
      ),
      current_time=NOW,
    )
  assert draft.sized_volume == 100 and risk.allowed
  return execution_id, dict(
    event_key="order-command",
    order_id="paper-order",
    intent_id=intent.intent_id,
    order_attempt=0,
    request=request,
    sizing_evidence=draft,
    risk_evidence=risk,
    expected_revision=state["revision"],
    expected_snapshot_hash=state["snapshot_hash"],
    now=NOW,
  )


@pytest.fixture
def sink():
  seen = []

  async def receipt(db, execution_id, result):
    seen.append((execution_id, len(result.orders), len(result.trades)))

  receipt.seen = seen
  return receipt


def test_receipt_sink_must_be_explicit_and_async():
  with pytest.raises(TypeError, match="ASYNC_RECEIPT_SINK"):
    PaperExecutionLedger(None, receipt_sink=None)
  with pytest.raises(TypeError, match="ASYNC_RECEIPT_SINK"):
    PaperExecutionLedger(None, receipt_sink=lambda db, scope, result: None)


async def test_initialize_requires_matching_paper_owner_and_exact_seed(sessions, sink):
  snapshot, _ = await _seed(sessions)
  execution_id = snapshot.cut.execution_ref.owner_id
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      with pytest.raises(ValueError, match="SCOPE"):
        await ledger.initialize(
          execution_id=execution_id, account_id="other", **seed_values()
        )
      first = await ledger.initialize(
        execution_id=execution_id, account_id="account-1", **seed_values()
      )
      same = await ledger.initialize(
        execution_id=execution_id, account_id="account-1", **seed_values()
      )
      assert same is first and first.revision == 0
      assert first.initial_snapshot_hash == first.snapshot_hash
      assert (
        "run_id" not in first.bucket_checkpoint
        and "generated_at" not in first.bucket_checkpoint
      )
      with pytest.raises(ValueError, match="SEED_IDEMPOTENCY"):
        await ledger.initialize(
          execution_id=execution_id,
          account_id="account-1",
          **{**seed_values(), "cash": 99999.0},
        )


async def test_partial_fill_restart_history_idempotency_and_bucket_conservation(
  sessions, sink
):
  execution_id, order_args = await setup(sessions, sink)
  receipts = []
  for action in ("place", "q1", "q2"):
    async with sessions() as db:
      async with db.begin():
        ledger = PaperExecutionLedger(db, receipt_sink=sink)
        receipt = (
          await ledger.place_order(execution_id=execution_id, **order_args)
          if action == "place"
          else await ledger.process_quote(
            execution_id=execution_id,
            event_key=action,
            quote=quote(1 if action == "q1" else 2),
          )
        )
        receipts.append(receipt)
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      before = len(sink.seen)
      assert (
        await ledger.process_quote(
          execution_id=execution_id, event_key="q1", quote=quote(1)
        )
      ).duplicate
      assert (
        await ledger.place_order(execution_id=execution_id, **order_args)
      ).duplicate
      assert len(sink.seen) == before
      with pytest.raises(ValueError, match="IDEMPOTENCY"):
        await ledger.process_quote(
          execution_id=execution_id, event_key="q1", quote=quote(1, depth=400)
        )
      account = await ledger.get_snapshot(execution_id=execution_id)
      assert account["revision"] == 3
      assert account["broker_checkpoint"]["material"]["orders"] == {}
      bucket = account["bucket_checkpoint"]["instruments"]["600000.SH"]["swing"]
      assert bucket["total_volume"] == 1100 and bucket["today_buy_volume"] == 100
      assert bucket["available_volume"] == 1000
      row = await db.get(PaperExecutionOrderRecord, "paper-order")
      assert row.filled_volume == 100 and row.status == "FILLED"
      fills = list((await db.scalars(select(PaperExecutionFillRecord))).all())
      assert sum(fill.volume for fill in fills) == 100 and len(fills) == 2
      assert row.response_payload["commission"] == pytest.approx(
        sum(float(fill.fee) for fill in fills)
      )
      events = list(
        (
          await db.scalars(
            select(PaperExecutionEventRecord).order_by(
              PaperExecutionEventRecord.revision
            )
          )
        ).all()
      )
      assert events[0].result_payload["order_ids"] == ["paper-order"]
      assert events[1].previous_snapshot_hash == events[0].resulting_snapshot_hash


async def test_sink_failure_rolls_back_orders_event_account_and_public_state(
  sessions, sink
):
  execution_id, args = await setup(sessions, sink)

  async def failed(db, scope, result):
    intent = await db.get(TradeIntentRecord, args["intent_id"])
    intent.status = "ROUTED"
    await db.flush()
    raise RuntimeError("sink failed")

  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=failed)
      with pytest.raises(RuntimeError, match="sink failed"):
        await ledger.place_order(execution_id=execution_id, **args)
  async with sessions() as db:
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionOrderRecord)) == 0
    )
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord)) == 0
    )
    assert (await db.get(PaperExecutionAccountRecord, execution_id)).revision == 0
    assert (
      await db.get(TradeIntentRecord, args["intent_id"])
    ).status == "EXECUTION_READY"


async def test_required_revision_and_real_typed_review(sessions, sink):
  execution_id, args = await setup(sessions, sink)
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      with pytest.raises(TypeError, match="TYPED"):
        await ledger.place_order(
          execution_id=execution_id, **{**args, "sizing_evidence": {"allowed": True}}
        )
      with pytest.raises(ValueError, match="REVISION"):
        await ledger.place_order(
          execution_id=execution_id, **{**args, "expected_revision": 1}
        )
      with pytest.raises(ValueError, match="SIZING_RISK"):
        await ledger.place_order(
          execution_id=execution_id,
          **{**args, "request": replace(args["request"], volume=200)},
        )
      with pytest.raises(ValueError, match="REQUEST"):
        await ledger.place_order(
          execution_id=execution_id,
          **{
            **args,
            "request": replace(args["request"], environment=ExecutionEnvironment.LIVE),
          },
        )


async def test_successful_frame_is_rolled_back_with_caller_transaction(sessions, sink):
  execution_id, args = await setup(sessions, sink)
  async with sessions() as db:
    with pytest.raises(RuntimeError, match="outer abort"):
      async with db.begin():
        receipt = await PaperExecutionLedger(db, receipt_sink=sink).place_order(
          execution_id=execution_id, **args
        )
        assert receipt.result_payload["revision"] == 1
        raise RuntimeError("outer abort")
  async with sessions() as db:
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionOrderRecord)) == 0
    )
    assert (await db.get(PaperExecutionAccountRecord, execution_id)).revision == 0


@pytest.mark.parametrize(
  "seconds,ttl,reason",
  [
    (60, 60, "INTENT_EXPIRED"),
    (5, 5, "ALLOCATION_EXPIRED"),
    (30, 60, "ADMISSION_EXPIRED"),
  ],
)
async def test_expired_ready_credentials_cannot_create_new_order_with_fresh_account_and_ttl(
  sessions, sink, seconds, ttl, reason
):
  execution_id, args = await setup(sessions, sink, candidate_ttl_seconds=ttl)
  now = NOW + timedelta(seconds=seconds)
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      await ledger.process_quote(
        execution_id=execution_id, event_key="refresh", quote=quote(seconds)
      )
      snapshot = await ledger.get_snapshot(execution_id=execution_id)
      fresh = replace(
        args["request"],
        metadata={
          **args["request"].metadata,
          "order_expire_at_ms": int((now + timedelta(seconds=30)).timestamp() * 1000),
        },
      )
      with pytest.raises(ValueError, match=reason):
        await ledger.place_order(
          execution_id=execution_id,
          **{
            **args,
            "now": now,
            "request": fresh,
            "expected_revision": snapshot["revision"],
            "expected_snapshot_hash": snapshot["snapshot_hash"],
          },
        )
      assert (
        await db.scalar(select(func.count()).select_from(PaperExecutionOrderRecord))
        == 0
      )


@pytest.mark.parametrize(
  "allocation_at,admission_at,reason",
  [
    (NOW + timedelta(seconds=1), NOW + timedelta(seconds=1), "ALLOCATION_NOT_CAUSAL"),
    (NOW, NOW + timedelta(seconds=1), "ADMISSION_NOT_CAUSAL"),
  ],
)
async def test_order_cannot_precede_decision_creation_or_admission_commit(
  sessions, sink, allocation_at, admission_at, reason
):
  execution_id, args = await setup(
    sessions, sink, allocation_at=allocation_at, admission_at=admission_at
  )
  async with sessions() as db:
    async with db.begin():
      with pytest.raises(ValueError, match=reason):
        await PaperExecutionLedger(db, receipt_sink=sink).place_order(
          execution_id=execution_id, **args
        )


async def test_submission_before_authority_expiry_can_fill_later_within_order_ttl(
  sessions, sink
):
  execution_id, args = await setup(sessions, sink, candidate_ttl_seconds=30)
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      before = NOW + timedelta(seconds=30) - timedelta(microseconds=1)
      await ledger.place_order(execution_id=execution_id, **{**args, "now": before})
      receipt = await ledger.process_quote(
        execution_id=execution_id,
        event_key="fill-after-auth-expiry",
        quote=quote(31, depth=400),
      )
      assert receipt.result_payload["orders"][0]["status"] == "FILLED"


async def test_decimal_exact_cash_cap_matches_real_sizer_boundary(sessions, sink):
  execution_id, args = await setup(
    sessions, sink, allocation_cash="115.0011", order_price=1.10
  )
  assert args["sizing_evidence"].sized_volume == 100
  assert Decimal(
    args["sizing_evidence"].metadata["allocation_cash_required"]
  ) == Decimal("115.0011")
  async with sessions() as db:
    async with db.begin():
      receipt = await PaperExecutionLedger(db, receipt_sink=sink).place_order(
        execution_id=execution_id, **args
      )
      assert receipt.result_payload["orders"][0]["status"] == "SUBMITTED"


async def test_ttl_quote_releases_bucket_reservation_without_fill(sessions, sink):
  execution_id, args = await setup(sessions, sink)
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      await ledger.place_order(execution_id=execution_id, **args)
      receipt = await ledger.process_quote(
        execution_id=execution_id, event_key="expired", quote=quote(60)
      )
      assert receipt.result_payload["fill_ids"] == []
      assert receipt.result_payload["orders"][0]["status"] == "EXPIRED"
      snapshot = await ledger.get_snapshot(execution_id=execution_id)
      assert snapshot["bucket_checkpoint"]["pending_orders"] == {}
      assert snapshot["broker_checkpoint"]["material"]["state"]["cash"] == 100000


async def test_cancel_and_empty_fill_quote_are_atomic_events(sessions, sink):
  execution_id, args = await setup(sessions, sink)
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      await ledger.place_order(execution_id=execution_id, **args)
      empty = await ledger.process_quote(
        execution_id=execution_id, event_key="no-depth", quote=quote(1, depth=0)
      )
      assert empty.result_payload["fill_ids"] == []
      cancelled = await ledger.cancel(
        execution_id=execution_id,
        event_key="cancel",
        order_id="paper-order",
        now=NOW + timedelta(seconds=2),
      )
      assert cancelled.result_payload["orders"][0]["status"] == "CANCELLED"
      assert (
        await ledger.cancel(
          execution_id=execution_id,
          event_key="cancel",
          order_id="paper-order",
          now=NOW + timedelta(seconds=2),
        )
      ).duplicate
      state = await ledger.get_snapshot(execution_id=execution_id)
      assert state["bucket_checkpoint"]["pending_orders"] == {}


@pytest.mark.parametrize("wrong_source", [False, True])
async def test_exit_sell_uses_real_risk_substitution_and_exact_source_owner(
  sessions, sink, wrong_source
):
  initial = seed_values()
  core = initial["bucket_checkpoint"]["instruments"]["600000.SH"].pop("swing")
  core["bucket"] = "core"
  initial["bucket_checkpoint"]["instruments"]["600000.SH"]["core"] = core
  execution_id, buy_args = await setup(sessions, sink, initial_seed=initial)
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      await ledger.place_order(execution_id=execution_id, **buy_args)
      await ledger.process_quote(
        execution_id=execution_id, event_key="buy-filled", quote=quote(1, depth=400)
      )
      plan_id = "paper-exit-plan"
      db.add(
        AutoExitPlanRecord(
          plan_id=plan_id,
          account_id="account-1",
          instrument_code="600000.SH",
          bucket="swing",
          source_type="T_TRADE",
          source_id="paper-test-source",
          source_execution_owner_type="T_ASSISTANT_EXECUTION",
          source_execution_owner_id="other-execution" if wrong_source else execution_id,
          source_execution_environment="PAPER",
          environment="PAPER",
          protected_volume=100,
          remaining_volume=100,
          entry_avg_price=9.9,
        )
      )
      intent = TradeIntent(
        strategy_id="",
        instrument_code="600000.SH",
        direction="SELL",
        bucket="swing",
        reason="exit",
        target_volume=100,
        intent_id="paper-sell-intent",
        execution_ref=ExecutionOwnerRef("EXIT_PLAN", plan_id),
        origin=ExitPlanIntentOrigin(
          plan_id, ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id)
        ),
        metadata={"bucket": "swing"},
      )
      record = trade_intent_record_data(
        intent, status="EXECUTION_READY", environment=ExecutionEnvironment.PAPER
      )
      record["account_id"] = "account-1"
      await TradeIntentRepository(db).accept_intents_idempotent([record])
    async with db.begin():
      position = {
        "long_volume": 1100,
        "available_volume": 1000,
        "core_available_volume": 1000,
        "swing_available_volume": 0,
      }
      draft = OrderSizer().draft_intent(
        intent,
        OrderType.SELL,
        9.85,
        {"cash": 99000.0, "total_asset": 110000.0},
        position,
      )
      request = OrderRequest(
        instrument_code="600000.SH",
        order_type=OrderType.SELL,
        price_type=PriceType.LIMIT,
        volume=draft.sized_volume,
        price=9.85,
        execution_ref=intent.execution_ref,
        environment=ExecutionEnvironment.PAPER,
        metadata={
          "bucket": "swing",
          "intent_id": intent.intent_id,
          "order_expire_at_ms": int((NOW + timedelta(seconds=60)).timestamp() * 1000),
        },
      )
      risk = await TradingRiskChecker(
        strict_market_data=True, strict_limit_data=True
      ).evaluate_order(
        request,
        account={"cash": 99000.0, "total_asset": 110000.0},
        position=position,
        market_data=quote(2),
        current_time=NOW + timedelta(seconds=2),
      )
      assert risk.allowed and risk.substitution_plan is not None
      snapshot = await ledger.get_snapshot(execution_id=execution_id)
      kwargs = dict(
        execution_id=execution_id,
        event_key="sell",
        order_id="paper-sell",
        intent_id=intent.intent_id,
        order_attempt=0,
        request=request,
        sizing_evidence=draft,
        risk_evidence=risk,
        expected_revision=snapshot["revision"],
        expected_snapshot_hash=snapshot["snapshot_hash"],
        now=NOW + timedelta(seconds=2),
      )
      if wrong_source:
        with pytest.raises(ValueError, match="EXIT_OWNER"):
          await ledger.place_order(**kwargs)
        return
      await ledger.place_order(**kwargs)
  # New repository/matcher/ledger objects must retain reservation and substitution.
  async with sessions() as db:
    async with db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=sink)
      result = await ledger.process_quote(
        execution_id=execution_id, event_key="sell-filled", quote=quote(3, depth=400)
      )
      assert result.result_payload["orders"][0]["status"] == "FILLED"
      snapshot = await ledger.get_snapshot(execution_id=execution_id)
      buckets = snapshot["bucket_checkpoint"]["instruments"]["600000.SH"]
      assert buckets["swing"]["total_volume"] == 0
      assert buckets["core"]["total_volume"] == 1000
      assert buckets["core"]["today_buy_volume"] == 100
      assert buckets["core"]["available_volume"] == 900
      await ledger.process_quote(
        execution_id=execution_id, event_key="next-day", quote=quote(86400)
      )
      snapshot = await ledger.get_snapshot(execution_id=execution_id)
      assert (
        snapshot["bucket_checkpoint"]["instruments"]["600000.SH"]["core"][
          "available_volume"
        ]
        == 1000
      )
