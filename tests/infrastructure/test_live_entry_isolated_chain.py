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
from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
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
@pytest.mark.parametrize(
  "fault",
  [
    None,
    "disabled",
    "approval",
    "device_lost",
    "replacement",
    "replacement_device_lost",
  ],
)
async def test_confirm_reallocate_stage_and_fresh_dispatch_review(
  sessions, review_evidence, monkeypatch, fault
):
  replacing = fault in {"replacement", "replacement_device_lost"}
  at = allocation.NOW
  confirmed = at + timedelta(seconds=1)
  # Give SQL defaults and bulk UPDATEs the same synthetic wall clock as Python.
  async with sessions.kw["bind"].connect() as connection:
    await connection.run_sync(
      lambda sync: sync.connection.dbapi_connection.create_function(
        "current_timestamp",
        0,
        lambda: confirmed.strftime("%Y-%m-%d %H:%M:%S.%f"),
      )
    )
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
            AuthUserAccountAccess,
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

  initial_volume = 200 if replacing else 100
  if replacing:
    original_seed = confirmation._seed

    async def larger_seed(*args, **kwargs):
      enrich = kwargs["enrich_intent"]

      def larger_intent(intent):
        enrich(intent)
        intent.target_amount = 2000

      kwargs["enrich_intent"] = larger_intent
      snapshot, candidates = await original_seed(*args, **kwargs)
      return snapshot, tuple(
        replace(c, requested_amount_ceiling=Decimal(2000)) for c in candidates
      )

    monkeypatch.setattr(confirmation, "_seed", larger_seed)
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
    if fault in {"disabled", "approval"}:
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
      assert result.user_id == "user-1" and result.request.volume == initial_volume

    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 0
    assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 0

  if fault in {"disabled", "approval"}:
    return

  # Continue through the real account sequencer and final command service.
  # Only the platform/device boundary and wall clock are supplied by the test.
  from datetime import datetime
  from unittest.mock import AsyncMock

  from quantx_contracts import runtime_environment
  from quantx_infrastructure.models.agent_runtime import TTradeBatch
  from quantx_infrastructure.models.risk_increase_admission import (
    AccountRiskIncreaseAdmissionBatch,
  )
  from quantx_infrastructure.services import (
    account_risk_increase_admission as admission_module,
  )
  from quantx_infrastructure.services import (
    trade_command_service as command_module,
  )

  class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
      return confirmed.astimezone(tz) if tz else confirmed.replace(tzinfo=None)

  monkeypatch.setattr(command_module, "datetime", FixedDateTime)
  monkeypatch.setattr(
    command_module.time_utils, "now", lambda: confirmation.to_shanghai(confirmed)
  )
  for module in (command_module, admission_module):
    monkeypatch.setattr(module, "utcnow", lambda: confirmed.replace(tzinfo=None))
  monkeypatch.setattr(
    runtime_environment, "live_runtime_allowed", lambda environment: True
  )
  monkeypatch.setattr(command_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    command_module.settings, "real_trading_account_allowlist", ["account-1"]
  )
  async with sessions() as db:
    service = command_module.TradeCommandService(db, live_entry_review=adapter(db))
    service._device_for = AsyncMock(
      return_value=SimpleNamespace(id="synthetic", user_id="user-1")
    )
    service._require_live_market_stream_ready = AsyncMock()
    if fault == "device_lost":
      service._device_for.side_effect = [
        SimpleNamespace(id="synthetic", user_id="user-1"),
        command_module.AgentUnavailableError("SYNTHETIC_DEVICE_LOST"),
      ]
    call = service.dispatch_ready_risk_increase_orders(
      account_id="account-1", processing_owner="isolated-test"
    )
    if fault == "device_lost":
      with pytest.raises(
        command_module.AgentUnavailableError, match="SYNTHETIC_DEVICE_LOST"
      ):
        await call
      for model in (
        PendingTradeOrder,
        OrderCorrelation,
        TradeCommandOutbox,
        TTradeBatch,
      ):
        assert await db.scalar(select(func.count()).select_from(model)) == 0
      intent = await db.get(TradeIntentRecord, "intent-0", populate_existing=True)
      assert intent.status == "EXECUTION_READY"
      assert "risk_increase_order_request" in intent.intent_metadata
      return
    queued = await call
    assert set(queued) == {"intent-0"}
    pending = await db.get(PendingTradeOrder, queued["intent-0"].client_order_id)
    outbox = await db.get(TradeCommandOutbox, queued["intent-0"].message_id)
    batch = await db.scalar(select(TTradeBatch))
    admission = await db.scalar(select(AccountRiskIncreaseAdmissionBatch))
    intent = await db.get(TradeIntentRecord, "intent-0", populate_existing=True)
    assert intent.status == "EXECUTION_PENDING"
    assert admission.status == "COMMITTED"
    assert (
      batch.source_execution_owner_type == pending.owner_type == "T_ASSISTANT_EXECUTION"
    )
    assert pending.owner_id == "live-fixture" and pending.volume == initial_volume
    assert pending.strategy_run_id is batch.strategy_run_id is None
    assert outbox.delivery_status == "QUEUED"
    assert (
      await service.dispatch_ready_risk_increase_orders(
        account_id="account-1", processing_owner="replay"
      )
    ) == {}
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 1

  if replacing:
    confirmed += timedelta(seconds=1)  # Fill is strictly after the attribution seed.

  from quantx_contracts.agent import PROTOCOL_VERSION
  from quantx_engine import report_processor
  from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
  from quantx_infrastructure.services import (
    exit_plan_authorization_service as exit_authorization,
  )

  monkeypatch.setattr(
    exit_authorization, "utcnow", lambda: confirmed.replace(tzinfo=None)
  )
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(
    report_processor, "utcnow", lambda: confirmed.replace(tzinfo=None)
  )
  from quantx_infrastructure.services import (
    auto_exit_plan_service,
    order_service,
    trade_service,
  )

  open_service_sessions = 0

  async def isolated_db():
    nonlocal open_service_sessions
    open_service_sessions += 1
    try:
      async with sessions() as db:
        yield db
    finally:
      open_service_sessions -= 1

  monkeypatch.setattr(order_service, "get_async_db", isolated_db)
  monkeypatch.setattr(trade_service, "get_async_db", isolated_db)
  monkeypatch.setattr(auto_exit_plan_service, "AsyncSessionLocal", sessions)
  order_payload = {
    "client_order_id": pending.client_order_id,
    "account_id": "account-1",
    "stock_code": "600000.SH",
    "order_id": 101,
    "order_sysid": "system-101",
    "order_type": 23,
    "order_status": 50,
    "order_volume": initial_volume,
    "price": 9.9,
    "order_time": int(confirmed.timestamp()),
    "source_sequence": 1,
    "source_event_at": confirmed.isoformat(),
  }
  await report_processor._process_order_report(dict(order_payload))
  await report_processor._process_order_report(dict(order_payload))
  report = AgentReportInbox(
    message_id="synthetic-fill",
    client_order_id=pending.client_order_id,
    received_at=confirmed.replace(tzinfo=None),
    device_id="synthetic",
    message_type="execution_report",
    protocol_version=PROTOCOL_VERSION,
    raw_payload_hash="f" * 64,
    business_idempotency_key="synthetic-fill",
    payload={
      "client_order_id": pending.client_order_id,
      "account_id": "account-1",
      "stock_code": "600000.SH",
      "order_id": "101",
      "traded_id": "fill-1",
      "traded_volume": 100,
      "traded_price": 9.9,
      "traded_time": int(confirmed.timestamp()),
      "source_sequence": 2,
      "source_event_at": confirmed.isoformat(),
    },
  )
  report.raw_payload_hash = hashlib.sha256(
    json.dumps(report.payload, sort_keys=True, separators=(",", ":")).encode()
  ).hexdigest()
  async with sessions() as db, db.begin():
    db.add(report)
  await report_processor._process(report)
  await report_processor._process(report)
  assert open_service_sessions == 0
  async with sessions() as db:
    assert await db.scalar(select(func.count()).select_from(Order)) == 1
    assert await db.scalar(select(func.count()).select_from(Trade)) == 1
    recorded_trade = await db.get(Trade, "fill-1")
    assert recorded_trade.volume == 100 and recorded_trade.order_id == 101
  await report_processor._stage_runtime_events(report)
  async with sessions() as db:
    event = await db.scalar(select(StrategyRuntimeEvent))
    assert event is not None
    intent = await db.get(TradeIntentRecord, "intent-0")
    assert intent.executed_volume == 100
    assert intent.status in {"PARTIAL_FILLED", "FILLED"}, (intent.status, intent.notes)
    source = await db.get(TAssistantExecutionRecord, "live-fixture")
    user = await db.get(AuthUser, "user-1")
    user.permissions = ["liquidation:control", "trade:approve"]
    device_session = await db.get(AuthDeviceSession, "session-1")
    device_session.granted_permissions = list(user.permissions)
    db.add(
      AuthUserAccountAccess(user_id="user-1", account_id="account-1", is_default=True)
    )
    # Explicit synthetic post-fill account projection: today's 100 new shares
    # remain unsellable, while the 1000-share old inventory is available.
    db.add(
      Position(
        id="post-fill-position",
        account_id="account-1",
        account_type="STOCK",
        stock_code="600000.SH",
        instrument_name="fixture",
        volume=1100,
        can_use_volume=1000,
        frozen_volume=0,
        yesterday_volume=1000,
        avg_price=9.9,
        market_value=10890,
        created_at=confirmed,
        updated_at=confirmed,
      )
    )
    if not replacing:
      source.status = "STOPPED"
      source.completed_at = confirmed
      source.entry_readiness = "BLOCKED"
    await db.commit()
  await report_processor._drain_runtime_events()
  await report_processor._stage_runtime_events(report)
  await report_processor._drain_runtime_events()
  async with sessions() as db:
    applied = await db.get(StrategyRuntimeEvent, event.event_id)
    assert applied.application_status == "APPLIED"
    assert await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent)) == 1
    plans = list(await db.scalars(select(AutoExitPlanRecord)))
    assert len(plans) == 1
    assert plans[0].plan_state["entry_filled_volume"] == 100
    assert plans[0].source_execution_owner_id == "live-fixture"
    from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanEvent

    audit = list(await db.scalars(select(AutoExitPlanEvent)))
    assert plans[0].auto_exit_authorized, [
      (e.event_type, e.payload.get("reason_code")) for e in audit
    ]
    assert plans[0].auto_exit_authorization_challenge_id == "challenge-1"

  terminal_payload = {
    **order_payload,
    "order_status": 54 if replacing else 56,
    "traded_volume": 100,
    "traded_price": 9.9,
    "source_sequence": 3,
  }
  terminal_report = AgentReportInbox(
    message_id="synthetic-order-terminal",
    device_id="synthetic",
    message_type="order_report",
    protocol_version=PROTOCOL_VERSION,
    raw_payload_hash="e" * 64,
    business_idempotency_key="synthetic-order-terminal",
    payload=terminal_payload,
  )
  await report_processor._process(terminal_report)
  await report_processor._stage_runtime_events(terminal_report)
  await report_processor._drain_runtime_events()
  if replacing:
    from copy import deepcopy

    from quantx_engine import t_order_lifecycle, t_trade_runtime
    from sqlalchemy.orm.attributes import flag_modified

    confirmed += timedelta(seconds=31)
    current_ms = int(confirmed.timestamp() * 1000)
    market = replace(
      market, timestamp=confirmed, ask_price=[9.90, 9.91, 9.92, 9.93, 9.94]
    )
    latest = gate.latest_tick
    latest = replace(
      latest,
      received_at_ms=current_ms,
      sample=replace(
        latest.sample,
        source_time_ms=current_ms,
        ask_price=9.90,
      ),
    )
    gate = replace(gate, latest_tick=latest)
    fresh_payload = deepcopy(payload)
    fresh_payload.pop("snapshot_hash", None)
    fresh_payload.update(
      snapshot_id="replacement-account",
      source_event_at=confirmed.isoformat(),
      accounts=[dict(account_id="account-1", cash=9005, total_asset=19995)],
      positions_by_account={
        "account-1": [dict(stock_code="600000.SH", volume=1100, can_use_volume=1000)]
      },
      orders=[terminal_payload],
      trades=[report.payload],
    )
    fresh_hash = hashlib.sha256(
      json.dumps(fresh_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    fresh_payload["snapshot_hash"] = fresh_hash
    async with sessions() as db, db.begin():
      db.add(
        AgentReportInbox(
          message_id="replacement-account",
          device_id="synthetic",
          message_type="delta_report",
          raw_payload_hash=fresh_hash,
          business_idempotency_key="replacement-account",
          payload=fresh_payload,
          processing_status="PROCESSED",
          received_at=confirmed.replace(tzinfo=None),
          processed_at=confirmed.replace(tzinfo=None),
        )
      )
      control = await db.get(AccountExecutionControl, "account-1")
      control.last_snapshot_id = "replacement-account"
      control.last_snapshot_hash = fresh_hash
      control.last_snapshot_at = confirmed.replace(tzinfo=None)
      control.updated_at = confirmed.replace(tzinfo=None)
      flag_modified(control, "updated_at")
    monkeypatch.setattr(
      t_trade_runtime.t_assistant_live_supervisor, "entry_review_adapter", adapter
    )
    async with sessions() as db, db.begin():
      for model in (PendingTradeOrder, OrderCorrelation):
        for lineage in await db.scalars(select(model)):
          assert max(lineage.created_at, lineage.updated_at) <= confirmed.replace(
            tzinfo=None
          ), (model.__name__, lineage.created_at, lineage.updated_at, confirmed)
      validate = await t_order_lifecycle.advance_order(
        db, pending.client_order_id, now=confirmed.replace(tzinfo=None)
      )
      assert callable(validate)
      await db.flush()
      validate()
    async with sessions() as db:
      service = command_module.TradeCommandService(db, live_entry_review=adapter(db))
      service._device_for = AsyncMock(
        return_value=SimpleNamespace(id="synthetic", user_id="user-1")
      )
      service._require_live_market_stream_ready = AsyncMock()
      if fault == "replacement_device_lost":
        service._device_for.side_effect = [
          SimpleNamespace(id="synthetic", user_id="user-1"),
          command_module.AgentUnavailableError("SYNTHETIC_REPLACEMENT_DEVICE_LOST"),
        ]
        with pytest.raises(
          command_module.AgentUnavailableError,
          match="SYNTHETIC_REPLACEMENT_DEVICE_LOST",
        ):
          await service.dispatch_ready_risk_increase_orders(
            account_id="account-1", processing_owner="replacement-test"
          )
        for model in (PendingTradeOrder, OrderCorrelation, TradeCommandOutbox):
          assert await db.scalar(select(func.count()).select_from(model)) == 1
        retained = await db.get(TradeIntentRecord, "intent-0", populate_existing=True)
        assert retained.status == "EXECUTION_READY" and retained.executed_volume == 100
        return
      replacement = await service.dispatch_ready_risk_increase_orders(
        account_id="account-1", processing_owner="replacement-test"
      )
      assert set(replacement) == {"intent-0"}
      second = await db.get(PendingTradeOrder, replacement["intent-0"].client_order_id)
      wire = await db.get(TradeCommandOutbox, replacement["intent-0"].message_id)
      assert second.volume == 100 and second.t_order_attempt == 1
      assert second.t_order_parent_client_id == pending.client_order_id
      assert second.t_order_original_created_at == pending.t_order_original_created_at
      assert (
        second.trace_id == pending.trace_id and second.intent_id == pending.intent_id
      )
      assert second.strategy_run_id is None and second.owner_id == "live-fixture"
      assert wire.expires_at <= pending.t_order_original_created_at + timedelta(
        seconds=60
      )
      assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 2
      assert await db.scalar(select(func.count()).select_from(OrderCorrelation)) == 2
      assert await db.scalar(select(func.count()).select_from(TradeCommandOutbox)) == 2
      assert (
        await service.dispatch_ready_risk_increase_orders(
          account_id="account-1", processing_owner="replacement-replay"
        )
        == {}
      )
    confirmed += timedelta(seconds=1)
    second_order = {
      **order_payload,
      "client_order_id": second.client_order_id,
      "order_id": 102,
      "order_sysid": "system-102",
      "order_volume": 100,
      "price": float(second.limit_price),
      "order_time": int(confirmed.timestamp()),
      "source_sequence": 4,
      "source_event_at": confirmed.isoformat(),
    }
    await report_processor._process_order_report(second_order)
    second_fill = AgentReportInbox(
      message_id="synthetic-fill-2",
      client_order_id=second.client_order_id,
      device_id="synthetic",
      message_type="execution_report",
      protocol_version=PROTOCOL_VERSION,
      business_idempotency_key="synthetic-fill-2",
      received_at=confirmed.replace(tzinfo=None),
      payload={
        **report.payload,
        "client_order_id": second.client_order_id,
        "order_id": "102",
        "traded_id": "fill-2",
        "traded_price": float(second.limit_price),
        "traded_time": int(confirmed.timestamp()),
        "source_sequence": 5,
        "source_event_at": confirmed.isoformat(),
      },
    )
    second_fill.raw_payload_hash = hashlib.sha256(
      json.dumps(second_fill.payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    async with sessions() as db, db.begin():
      db.add(second_fill)
      holding = await db.get(Position, "post-fill-position")
      holding.volume = 1200
      holding.market_value = 1200 * float(second.limit_price)
    for _ in range(2):
      await report_processor._process(second_fill)
      await report_processor._stage_runtime_events(second_fill)
      await report_processor._drain_runtime_events()
    async with sessions() as db:
      intent = await db.get(TradeIntentRecord, "intent-0")
      plan = await db.scalar(select(AutoExitPlanRecord))
      assert intent.executed_volume == 200
      assert plan.plan_state["entry_filled_volume"] == 200
      assert plan.auto_exit_authorized
      assert await db.scalar(select(func.count()).select_from(Trade)) == 2
      assert await db.scalar(select(func.count()).select_from(AutoExitPlanRecord)) == 1
      # A derived authorization flag may change; producer configuration may not.
      from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
      from quantx_infrastructure.repositories.auto_exit_plan_repository import (
        AutoExitPlanConcurrencyError,
      )

      changed_template = deepcopy(intent.intent_metadata["exit_plan_template"])
      changed_template["config_version"] += 1
      with pytest.raises(AutoExitPlanConcurrencyError, match="模板已变化"):
        await (
          auto_exit_plan_service.AutoExitPlanService().register_execution_entry_fill(
            execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "live-fixture"),
            environment=ExecutionEnvironment.LIVE,
            exit_plan_template=changed_template,
            volume=1,
            price=float(second.limit_price),
            trade_time=confirmed,
            event_business_key="changed-template-must-not-append",
            db=db,
            commit=False,
          )
        )
      assert plan.plan_state["entry_filled_volume"] == 200
    second_terminal = AgentReportInbox(
      message_id="synthetic-order-terminal-2",
      device_id="synthetic",
      message_type="order_report",
      protocol_version=PROTOCOL_VERSION,
      raw_payload_hash="d" * 64,
      business_idempotency_key="synthetic-order-terminal-2",
      payload={
        **second_order,
        "order_status": 56,
        "traded_volume": 100,
        "traded_price": float(second.limit_price),
        "source_sequence": 6,
      },
    )
    await report_processor._process(second_terminal)
    await report_processor._stage_runtime_events(second_terminal)
    await report_processor._drain_runtime_events()
    async with sessions() as db, db.begin():
      assert (
        await t_order_lifecycle.advance_order(
          db, second.client_order_id, now=confirmed.replace(tzinfo=None)
        )
        is None
      )
    pending = second
  async with sessions() as db, db.begin():
    last = await db.get(PendingTradeOrder, pending.client_order_id)
    if not replacing:
      assert await report_processor.finalize_t_order_lifecycle(db, last)
    assert not await report_processor.finalize_t_order_lifecycle(db, last)
  await report_processor._drain_runtime_events()
  async with sessions() as db:
    last = await db.get(PendingTradeOrder, pending.client_order_id)
    intent = await db.get(TradeIntentRecord, "intent-0")
    assert last.request_metadata["t_order_lifecycle_finished"] is True
    assert intent.status == "FILLED" and intent.executed_volume == initial_volume
    finals = list(
      await db.scalars(
        select(StrategyRuntimeEvent).where(
          StrategyRuntimeEvent.business_key == "t-order-lifecycle:intent-0",
        )
      )
    )
    assert len(finals) == 1 and finals[0].application_status == "APPLIED"
    assert (
      finals[0].owner_type == "T_ASSISTANT_EXECUTION"
      and finals[0].strategy_run_id is None
    )
  event.payload = {
    **event.payload,
    "report": {**event.payload["report"], "stock_code": "000001.SZ"},
  }
  with pytest.raises(
    report_processor.OwnerRuntimeRoutingError, match="OWNER_TARGET_CONFLICT"
  ):
    await report_processor._apply_runtime_event(event)
