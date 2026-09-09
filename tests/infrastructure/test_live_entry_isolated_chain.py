"""Synthetic broker facts, real durable confirmation/Gate/capacity/order review."""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from quantx_application.t_trade_v3.daily_t_valuation import TValuationMark
from quantx_engine.t_assistant_live_entry_review import (
  LiveEntryMarketWitness,
  LiveEntryReviewAdapter,
)
from quantx_engine.t_assistant_live_entry_runtime import TAssistantLiveEntryRuntime
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentDevice,
  AgentReportInbox,
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auth import AuthDeviceSession, AuthUser
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from quantx_infrastructure.services.live_entry_dispatch_review import (
  revalidate_live_entry_dispatch,
)
from quantx_infrastructure.services.live_portfolio_snapshot import (
  LivePortfolioSnapshotReader,
)
from quantx_infrastructure.services.live_position_attribution import (
  LivePositionAttributionService,
)
from sqlalchemy import func, select

from tests.infrastructure import test_t_allocation_repository as allocation
from tests.infrastructure import test_t_entry_confirmation as confirmation
from tests.infrastructure.test_live_position_attribution import buckets
from tests.infrastructure.test_paper_entry_execution_review import (
  allocation_sessions as _allocation_sessions,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  base_sessions as _base_sessions,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  frozen_config as _frozen_config,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  input_for,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  ledger_sessions as _ledger_sessions,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  review_evidence as _review_evidence,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  sessions as _sessions,
)
from tests.infrastructure.test_t_entry_confirmation import signing_key as _signing_key

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions
ledger_sessions = _ledger_sessions
sessions = _sessions
frozen_config = _frozen_config
review_evidence = _review_evidence
signing_key = _signing_key


@pytest.mark.parametrize(
  "review_evidence", [allocation.NOW.replace(hour=1)], indirect=True
)
@pytest.mark.parametrize("fault", [None, "disabled", "approval"])
async def test_confirm_reallocate_stage_and_fresh_dispatch_review(
  sessions, review_evidence, monkeypatch, fault
):
  at = allocation.NOW
  confirmed = at + timedelta(seconds=1)
  monkeypatch.setattr(confirmation, "NOW", at)
  monkeypatch.setattr(confirmation, "CONFIRMED", confirmed)
  async with sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda db: Base.metadata.create_all(
        db,
        tables=[
          model.__table__
          for model in (
            AccountExecutionControl,
            AgentDevice,
            AgentReportInbox,
            OrderCorrelation,
            PendingTradeOrder,
            StrategyRuntimeEvent,
            TradeCommandOutbox,
            AuthUser,
            AuthDeviceSession,
            TradeConfirmationChallenge,
            Order,
            Position,
            Trade,
          )
        ],
      )
    )

  class Marks:
    async def read(self, **kwargs):
      return SimpleNamespace(
        as_of=kwargs["as_of"],
        current={
          "600000.SH": TValuationMark(
            "600000.SH", Decimal("9.9"), confirmed, "synthetic"
          )
        },
        opening={},
      )

  _, candidates = await confirmation.seed_confirmable(sessions)
  async with sessions() as db, db.begin():
    # Broker boundary: a deterministic, complete synthetic account snapshot.
    payload = dict(
      snapshot_id="confirmed-account",
      source_event_at=confirmed.isoformat(),
      is_complete=True,
      accounts=[dict(account_id="account-1", cash=10000, total_asset=20000)],
      positions_by_account={
        "account-1": [dict(stock_code="600000.SH", volume=1000, can_use_volume=1000)]
      },
      orders=[],
      trades=[],
      section_completeness_by_account={
        "account-1": dict.fromkeys(("account", "positions", "orders", "trades"), True)
      },
      snapshot_authority_by_account={
        "account-1": dict(
          initial_status=0,
          final_status=0,
          stable=True,
          snapshot_eligible=True,
          status_name="OK",
          reason_code="AUTHORITATIVE",
        )
      },
    )
    digest = hashlib.sha256(
      json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload["snapshot_hash"] = digest
    db.add(
      AgentReportInbox(
        message_id="snapshot",
        device_id="synthetic",
        message_type="delta_report",
        raw_payload_hash=digest,
        business_idempotency_key="snapshot",
        payload=payload,
        processing_status="PROCESSED",
        received_at=confirmed.replace(tzinfo=None),
        processed_at=confirmed.replace(tzinfo=None),
      )
    )
    db.add(
      AccountExecutionControl(
        account_id="account-1",
        last_snapshot_id=payload["snapshot_id"],
        last_snapshot_hash=digest,
        last_snapshot_at=confirmed.replace(tzinfo=None),
        authorization_state="ENABLED",
        reconcile_status="READY",
        created_at=at,
        updated_at=confirmed,
      )
    )
    await db.flush()
    await LivePositionAttributionService(db).approve_seed(
      execution_id="live-fixture",
      actor_id="test-operator",
      instruments=buckets(),
      expected_snapshot_id=payload["snapshot_id"],
      expected_snapshot_hash=digest,
      as_of=confirmed,
      max_age_seconds=90,
    )
    cycle = await db.get(TAssistantDecisionCycleRecord, "intake-cycle")
    cycle.created_at = at
    source = await db.get(TAssistantExecutionRecord, "live-fixture")
    source.updated_at = confirmed
    await confirmation.confirm(db)
  async with sessions() as db, db.begin():
    repo = TAllocationRepository(db)
    newer = await LivePortfolioSnapshotReader(db).read(
      execution_id="live-fixture",
      cycle_id="intake-cycle",
      instrument_codes=["600000.SH"],
      as_of=confirmed,
      market_mark_reader=Marks(),
      account_max_age_seconds=90,
    )
    assert newer.entry_blockers == ()
    candidates = tuple(replace(c, intent_version=1) for c in candidates)
    batch = await allocation._prepared(repo, newer, candidates, now=confirmed)
    claim = await allocation._claim(repo, batch, newer, candidates, now=confirmed)
    await repo.commit(claim=claim, snapshot=newer, candidates=candidates, now=confirmed)
    row = await db.get(TradeIntentRecord, "intent-0")
    assert row.status == "EXECUTION_READY"
    gate, market = await input_for(db, "live-fixture", row.id)

  async def witness(execution_id, code):
    assert (execution_id, code) == ("live-fixture", "600000.SH")
    return LiveEntryMarketWitness(
      gate.latest_tick,
      gate.latest_ring_generation,
      gate.latest_accepted_sequence,
      market,
      lambda: None,
    )

  def adapter(db):
    return LiveEntryReviewAdapter(
      db, witness_provider=witness, market_mark_reader=Marks(), clock=lambda: confirmed
    )

  runtime = TAssistantLiveEntryRuntime(
    session_factory=sessions, clock=lambda: confirmed, review_adapter_factory=adapter
  )
  result = await runtime.dispatch(
    execution_id="live-fixture", validate_market=lambda: None
  )
  assert result.staged == ("intent-0",), result
  assert (
    await runtime.dispatch(execution_id="live-fixture", validate_market=lambda: None)
  ).status == "IDLE"
  async with sessions() as db, db.begin():
    row = await db.get(TradeIntentRecord, "intent-0")
    request = row.intent_metadata["risk_increase_order_request"]
    if fault == "disabled":
      control = await db.get(AccountExecutionControl, "account-1")
      control.authorization_state = "DISABLED"
      control.updated_at = confirmed
      from sqlalchemy.orm.attributes import flag_modified

      flag_modified(control, "updated_at")
    elif fault == "approval":
      challenge = await db.get(TradeConfirmationChallenge, "challenge-1")
      challenge.consumed_at = None
    await db.flush()
    call = revalidate_live_entry_dispatch(
      db,
      intent=row,
      volume=request["volume"],
      limit_price=Decimal(request["limit_price"]),
      now=confirmed,
      fresh_review=adapter(db),
    )
    if fault:
      with pytest.raises(
        ValueError,
        match=(
          "^LIVE_ENTRY_FRESH_REVIEW_REJECTED$"
          if fault == "disabled"
          else "^T_ENTRY_CONSUMED_CHALLENGE_REQUIRED$"
        ),
      ):
        await call
    else:
      result = await call
      assert result.user_id == "user-1" and result.request.volume == 100

    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 0
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 0
