"""Retirement uses real frozen intake and preserves pending/claimed obligations."""

from datetime import timedelta

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_engine.t_assistant_live_entry_recovery import recover_live_entry_work
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from tests.engine.unit.test_t_assistant_candidate_controls import (
  allocation_sessions as _allocation_sessions,
)
from tests.engine.unit.test_t_assistant_candidate_controls import (
  base_sessions as _base_sessions,
)
from tests.engine.unit.test_t_assistant_candidate_controls import (
  controls,
  source,
)
from tests.engine.unit.test_t_assistant_candidate_controls import (
  frozen_config as _frozen_config,
)
from tests.engine.unit.test_t_assistant_candidate_controls import (
  ledger_sessions as _ledger_sessions,
)
from tests.engine.unit.test_t_assistant_candidate_controls import (
  sessions as _sessions,
)

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions
ledger_sessions = _ledger_sessions
sessions = _sessions
frozen_config = _frozen_config


@pytest.mark.parametrize(
  "fault",
  [None, "pending", "lease", "committed", "old_lease", "material", "outbox", "persist"],
)
async def test_retirement_fences_real_intake_and_retains_durable_work(
  sessions, frozen_config, fault, monkeypatch
):
  seed, _, states = await source(sessions, "EXECUTION_READY", ExecutionEnvironment.LIVE)
  now = seed.now + timedelta(minutes=2)
  async with sessions() as db, db.begin():
    await db.run_sync(
      lambda session: Base.metadata.create_all(
        session.connection(),
        tables=[
          PendingTradeOrder.__table__,
          OrderCorrelation.__table__,
          TradeCommandOutbox.__table__,
          AccountRiskIncreaseAdmissionBatch.__table__,
        ],
      )
    )
    row = await db.get(TradeIntentRecord, seed.intent_id)
    from quantx_infrastructure.models.t_assistant_execution import (
      TAssistantDecisionCycleRecord,
    )

    cycle = await db.get(TAssistantDecisionCycleRecord, row.allocation_cycle_id)
    assert cycle.status == "PROPOSALS_COMMITTED"
    cycle.created_at = seed.now
    if fault == "pending":
      db.add(
        PendingTradeOrder(
          client_order_id="pending",
          user_id="user",
          account_id="account-1",
          owner_type="T_ASSISTANT_EXECUTION",
          owner_id=seed.execution_id,
          environment="LIVE",
          instrument_code=row.instrument_code,
          side="BUY",
          order_type="FIX_PRICE",
          limit_price="10",
          volume=100,
          status="UNKNOWN",
          intent_id=row.id,
          created_at=seed.now,
          updated_at=seed.now,
        )
      )
    elif fault == "outbox":
      db.add(
        TradeCommandOutbox(
          message_id="orphan",
          client_order_id="missing",
          idempotency_key="orphan",
          device_id="device",
          account_id="account-1",
          owner_type="T_ASSISTANT_EXECUTION",
          owner_id=seed.execution_id,
          environment="LIVE",
          payload={},
          delivery_status="PENDING",
          expires_at=now,
          created_at=seed.now,
          updated_at=seed.now,
        )
      )
    elif fault in {"lease", "old_lease", "committed"}:
      db.add(
        AccountRiskIncreaseAdmissionBatch(
          admission_batch_id="admission",
          account_id="account-1",
          environment="LIVE",
          attempt=1,
          policy_version="v1",
          account_snapshot_id="snapshot",
          account_snapshot_hash="a" * 64,
          obligation_watermark="b" * 64,
          input_fingerprint="c" * 64,
          intent_manifest_hash="d" * 64,
          status="COMMITTED" if fault == "committed" else "PREPARED",
          processing_owner="worker",
          processing_fence_token="fence",
          processing_lease_until=(
            now + timedelta(seconds=30) if fault == "lease" else seed.now
          ).replace(tzinfo=None),
          expires_at=(now + timedelta(seconds=60)).replace(tzinfo=None),
          created_at=seed.now,
          updated_at=seed.now,
        )
      )
      row.admission_batch_id = "admission"
      row.admission_rank = 1
      row.admission_policy_version = "v1"
      row.admission_input_fingerprint = "c" * 64
    elif fault == "material":
      row.target_amount = (row.target_amount or 0) + 1
    row.updated_at = seed.now.replace(tzinfo=None)
    flag_modified(row, "updated_at")
  async with sessions() as db, db.begin():
    if fault == "persist":
      from unittest.mock import AsyncMock

      from quantx_engine import t_assistant_live_entry_recovery as recovery_module

      monkeypatch.setattr(
        recovery_module.TAssistantExecutionRepository,
        "append_event",
        AsyncMock(side_effect=ValueError("TEST_PERSISTENCE_FAILURE")),
      )
    if fault in {"material", "outbox", "persist"}:
      with pytest.raises(
        ValueError,
        match="LIVE_ENTRY_RECOVERY_INTAKE_CHANGED|LIVE_ENTRY_RECOVERY_ORPHAN_OUTBOX|TEST_PERSISTENCE_FAILURE",
      ):
        await recover_live_entry_work(db, execution_id=seed.execution_id, now=now)
    else:
      result = await recover_live_entry_work(
        db, execution_id=seed.execution_id, now=now
      )
      retained = fault in {"pending", "lease", "committed"}
      assert result.retained == ((seed.intent_id,) if retained else ())
      assert result.expired == (() if retained else (seed.intent_id,))
      repeated = await recover_live_entry_work(
        db, execution_id=seed.execution_id, now=now
      )
      assert repeated.expired == ()
      if not retained:
        projected = await controls(
          db, seed, states, environment=ExecutionEnvironment.LIVE, as_of=now
        )
        assert projected["600000.SH"].suppress_candidate_id
      if fault == "old_lease":
        batch = await db.get(AccountRiskIncreaseAdmissionBatch, "admission")
        assert batch.status == "SUPERSEDED" and batch.processing_fence_token is None
  async with sessions() as db:
    row = await db.get(TradeIntentRecord, seed.intent_id)
    events = list(
      await db.scalars(
        select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.event_type == "LIVE_ENTRY_RETIRED"
        )
      )
    )
    assert len(events) == (1 if fault in {None, "old_lease"} else 0)
    assert row.status == (
      "EXPIRED" if fault in {None, "old_lease"} else "EXECUTION_READY"
    )


async def test_expired_staged_quote_cancels_without_extending_original_intent(
  sessions, frozen_config, monkeypatch
):
  from types import SimpleNamespace
  from unittest.mock import AsyncMock

  from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
  from quantx_domain.trading.market_rules import MarketDataSnapshot
  from quantx_domain.trading.risk_checker import OrderRiskDecision
  from quantx_engine.t_assistant_entry_gate_input import build_entry_gate
  from quantx_infrastructure.models.t_assistant_execution import (
    TAssistantDecisionCycleRecord,
    TAssistantExecutionRecord,
  )
  from quantx_infrastructure.services import live_entry_request_staging as staging
  from quantx_infrastructure.services.live_entry_execution_review import (
    LiveEntryReviewResult,
  )

  seed, execution, states = await source(
    sessions, "EXECUTION_READY", ExecutionEnvironment.LIVE
  )
  tick = seed.latest_tick
  sample = tick.sample
  market = MarketDataSnapshot(
    instrument_code=sample.instrument_code, timestamp=seed.now, price=sample.price
  )
  async with sessions() as db, db.begin():
    await db.run_sync(
      lambda session: Base.metadata.create_all(
        session.connection(),
        tables=[
          PendingTradeOrder.__table__,
          OrderCorrelation.__table__,
          TradeCommandOutbox.__table__,
          AccountRiskIncreaseAdmissionBatch.__table__,
        ],
      )
    )
    intent = await db.get(TradeIntentRecord, seed.intent_id)
    cycle = await db.get(TAssistantDecisionCycleRecord, intent.allocation_cycle_id)
    cycle.created_at = seed.now
    record = await db.get(TAssistantExecutionRecord, seed.execution_id)
    state = states[sample.instrument_code]
    witness = SimpleNamespace(
      latest_tick=tick,
      ring_generation=state.cursor.ring_generation,
      last_accepted_sequence=state.cursor.accepted_sequence,
    )
    gate = await build_entry_gate(
      db, record, intent, witness, seed.now, environment=ExecutionEnvironment.LIVE
    )
    request = OrderRequest(
      instrument_code=intent.instrument_code,
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      price=sample.price,
      volume=100,
      execution_ref=execution.execution_ref,
      environment=ExecutionEnvironment.LIVE,
      metadata={"portfolio_input_fingerprint": "a" * 64},
    )
    reviewed = LiveEntryReviewResult(
      "REVIEWED", (), "user", request, None, OrderRiskDecision.allow(request)
    )
    monkeypatch.setattr(
      staging.LiveEntryExecutionReview, "review", AsyncMock(return_value=reviewed)
    )
    await staging.stage_live_entry_request(
      db,
      execution_id=seed.execution_id,
      intent_id=seed.intent_id,
      gate_input=gate,
      market_data=market,
      market_mark_reader=None,
      now=seed.now,
      validate_market=lambda: None,
    )
  now = seed.now + timedelta(seconds=4)
  assert int(now.timestamp() * 1000) < gate.intent_expires_at_ms
  async with sessions() as db, db.begin():
    result = await recover_live_entry_work(db, execution_id=seed.execution_id, now=now)
    assert result.cancelled == (seed.intent_id,) and result.expired == ()
    intent = await db.get(TradeIntentRecord, seed.intent_id)
    assert (
      intent.status == "CANCELLED"
      and "risk_increase_order_request" in intent.intent_metadata
    )
    projected = await controls(
      db, seed, states, environment=ExecutionEnvironment.LIVE, as_of=now
    )
    assert (
      projected[sample.instrument_code].suppress_candidate_id
      == gate.candidate.candidate_id
    )


@pytest.mark.parametrize("status", ["ALLOCATION_PENDING", "AWAITING_APPROVAL"])
async def test_background_scan_retires_waiting_intents_before_account_dispatch(
  sessions, frozen_config, monkeypatch, status
):
  from quantx_engine import risk_increase_admission_runtime as runtime_module
  from quantx_engine.t_assistant_live_entry_recovery import recover_account_live_entries
  from quantx_infrastructure.models.t_assistant_execution import (
    TAssistantDecisionCycleRecord,
  )

  seed, _, _ = await source(sessions, status, ExecutionEnvironment.LIVE)
  now = seed.now + timedelta(minutes=2)
  async with sessions() as db, db.begin():
    await db.run_sync(
      lambda session: Base.metadata.create_all(
        session.connection(),
        tables=[
          PendingTradeOrder.__table__,
          OrderCorrelation.__table__,
          TradeCommandOutbox.__table__,
          AccountRiskIncreaseAdmissionBatch.__table__,
        ],
      )
    )
    intent = await db.get(TradeIntentRecord, seed.intent_id)
    cycle = await db.get(TAssistantDecisionCycleRecord, intent.allocation_cycle_id)
    cycle.created_at = seed.now
  dispatched = []

  class CommandService:
    def __init__(self, db):
      self.db = db

    async def dispatch_ready_risk_increase_orders(self, **kwargs):
      # Separate session observes the retirement commit before the queue runs.
      async with sessions() as reader:
        row = await reader.get(TradeIntentRecord, seed.intent_id)
        assert row.status == "EXPIRED"
      dispatched.append(kwargs["account_id"])
      return {}

  async def recover(db, *, account_id):
    return await recover_account_live_entries(db, account_id=account_id, now=now)

  monkeypatch.setattr(runtime_module, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(runtime_module, "TradeCommandService", CommandService)
  result = await runtime_module.RiskIncreaseAdmissionRuntime(
    live_entry_recovery=recover
  ).recover_once()
  assert result == {"accounts": 1, "dispatched": 0}
  assert dispatched == ["account-1"]
