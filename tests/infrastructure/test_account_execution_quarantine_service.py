from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_domain.clock import utcnow
from quantx_domain.trading.exit_plan import (
  ExitPlan,
  ExitPlanBook,
  ExitPlanCommand,
  ExitPlanCommandType,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)
from quantx_engine import report_processor
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AccountExecutionControlEvent,
  AgentDevice,
  AgentReportInbox,
  OrderCorrelation,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_execution_quarantine_service import (
  BROKER_EXECUTION_AFTER_RELEASE,
  LIVE_PLACE_PHYSICAL_GATE_REJECTED,
  QUARANTINE_CANCEL_REQUIRED_METADATA_KEY,
  QUARANTINE_REASON_METADATA_KEY,
  QUARANTINE_REPAIR_REQUIRED_METADATA_KEY,
  AccountExecutionQuarantineService,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

ACCOUNT_ID = "account-1"
USER_ID = "11111111-1111-4111-8111-111111111111"
DEVICE_ID = "22222222-2222-4222-8222-222222222222"
PLAN_ID = "exit-plan-1"
CURRENT_INTENT_ID = "current-exit-intent"
CLIENT_ORDER_ID = "current-exit-client-order"
PLACE_MESSAGE_ID = "33333333-3333-4333-8333-333333333333"
OTHER_PLAN_ID = "exit-plan-2"
OTHER_INTENT_ID = "other-exit-intent"
OTHER_CLIENT_ORDER_ID = "other-exit-client-order"
OTHER_PLACE_MESSAGE_ID = "44444444-4444-4444-8444-444444444444"


@asynccontextmanager
async def _database():
  engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
  )
  tables = (
    AuthUser.__table__,
    AgentDevice.__table__,
    RuntimeComponentHeartbeat.__table__,
    OrderCorrelation.__table__,
    AccountExecutionControl.__table__,
    AccountExecutionControlEvent.__table__,
    AgentReportInbox.__table__,
    AutoExitPlanRecord.__table__,
    TradeIntentRecord.__table__,
    PendingTradeOrder.__table__,
    TradeCommandOutbox.__table__,
  )
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: AuthUser.metadata.create_all(
        sync_connection,
        tables=tables,
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  try:
    yield sessions
  finally:
    await engine.dispose()


def _plan(
  *,
  owner_kind: str,
  environment: str = ExecutionEnvironment.LIVE.value,
) -> AutoExitPlanRecord:
  source_type, strategy_run_id = {
    "monitor": ("MANUAL_LIQUIDATION", None),
    "runtime": ("T_TRADE_BATCH", "runtime-run-1"),
    "dedicated": ("MANUAL_POSITION", None),
  }[owner_kind]
  source_owner_type, source_owner_id = {
    "monitor": ("MANUAL_COMMAND", "group-monitor"),
    "runtime": ("STRATEGY_RUN", "runtime-run-1"),
    "dedicated": ("MANUAL_COMMAND", "source-dedicated"),
  }[owner_kind]
  domain_plan = ExitPlan(
    template=ExitPlanTemplate(
      plan_id=PLAN_ID,
      source_type=source_type,
      source_id=f"source-{owner_kind}",
      account_id=ACCOUNT_ID,
      instrument_code="600000.SH",
      bucket="swing",
      rules=[
        ExitRuleSpec(
          rule_id=f"{PLAN_ID}:manual",
          strategy=ExitRuleType.MANUAL_TRIGGER,
        )
      ],
      run_id=strategy_run_id or "",
      config_version=2,
      auto_exit_authorized=False,
    ),
    status=ExitPlanStatus.ERROR,
    entry_filled_volume=100,
    entry_avg_price=10.0,
    pending_intent_id=CURRENT_INTENT_ID,
    pending_order_id=CLIENT_ORDER_ID,
    pending_rule_id=f"{PLAN_ID}:manual",
    pending_requested_volume=100,
    error_message="ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:old-intent",
  )
  return AutoExitPlanRecord(
    plan_id=PLAN_ID,
    account_id=ACCOUNT_ID,
    instrument_code="600000.SH",
    bucket="swing",
    source_type=source_type,
    source_id=f"source-{owner_kind}",
    strategy_run_id=strategy_run_id,
    enabled=False,
    status="ERROR",
    source_execution_owner_type=source_owner_type,
    source_execution_owner_id=source_owner_id,
    source_execution_environment=environment,
    environment=environment,
    config_version=2,
    protected_volume=100,
    exited_volume=0,
    remaining_volume=100,
    entry_avg_price=10.0,
    cost_basis_snapshot={},
    plan_state=domain_plan.to_dict(),
    group_id="group-monitor" if owner_kind == "monitor" else None,
    pending_client_order_id=CLIENT_ORDER_ID,
    state_version=7,
    last_error="ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:old-intent",
  )


def _pending(
  *,
  broker_order_id: str = "",
  environment: str = ExecutionEnvironment.LIVE.value,
) -> PendingTradeOrder:
  metadata = {"exit_rule_id": f"{PLAN_ID}:manual"}
  return PendingTradeOrder(
    client_order_id=CLIENT_ORDER_ID,
    user_id=USER_ID,
    account_id=ACCOUNT_ID,
    owner_type="EXIT_PLAN",
    owner_id=PLAN_ID,
    instrument_code="600000.SH",
    side="SELL",
    order_type="FIX_PRICE",
    limit_price="10.00",
    volume=100,
    status="DELIVERED" if broker_order_id else "QUEUED",
    broker_order_id=broker_order_id or None,
    environment=environment,
    strategy_run_id=None,
    strategy_order_id=None,
    intent_id=CURRENT_INTENT_ID,
    bucket="swing",
    trace_id="trace-current",
    request_metadata=metadata,
  )


def _place_outbox(
  *,
  delivered: bool,
  environment: str = ExecutionEnvironment.LIVE.value,
) -> TradeCommandOutbox:
  now = utcnow()
  return TradeCommandOutbox(
    message_id=PLACE_MESSAGE_ID,
    client_order_id=CLIENT_ORDER_ID,
    idempotency_key="place-current-exit",
    device_id=DEVICE_ID,
    account_id=ACCOUNT_ID,
    owner_type="EXIT_PLAN",
    owner_id=PLAN_ID,
    environment=environment,
    payload={
      "command_kind": "PLACE_ORDER",
      "client_order_id": CLIENT_ORDER_ID,
      "account_id": ACCOUNT_ID,
      "execution_mode": environment.lower(),
      "instrument_code": "600000.SH",
      "side": "SELL",
      "price_type": "FIX_PRICE",
      "limit_price": "10.00",
      "volume": 100,
      "expires_at": (now + timedelta(minutes=5)).isoformat() + "Z",
    },
    delivery_status="DELIVERED" if delivered else "QUEUED",
    delivered_at=now if delivered else None,
    expires_at=now + timedelta(minutes=5),
    attempts=1 if delivered else 0,
  )


def _other_plan_command(
  *,
  delivered: bool,
  broker_order_id: str = "",
  owner_kind: str = "monitor",
  auto_authorized: bool = False,
) -> tuple[
  AutoExitPlanRecord,
  TradeIntentRecord,
  PendingTradeOrder,
  TradeCommandOutbox,
]:
  source_type, strategy_run_id, template_metadata = {
    "monitor": ("MANUAL_POSITION", "", {}),
    "runtime": ("T_TRADE_BATCH", "other-runtime-run", {}),
    "dedicated": (
      "MANUAL_POSITION",
      "",
      {"managed_runtime_command_id": "other-managed-command"},
    ),
  }[owner_kind]
  source_owner_type = (
    "STRATEGY_RUN" if owner_kind == "runtime" else "MANUAL_COMMAND"
  )
  source_owner_id = strategy_run_id or "source-other"
  domain_plan = ExitPlan(
    template=ExitPlanTemplate(
      plan_id=OTHER_PLAN_ID,
      source_type=source_type,
      source_id="source-other",
      account_id=ACCOUNT_ID,
      instrument_code="600001.SH",
      bucket="swing",
      rules=[
        ExitRuleSpec(
          rule_id=f"{OTHER_PLAN_ID}:manual",
          strategy=ExitRuleType.MANUAL_TRIGGER,
        )
      ],
      config_version=1,
      auto_exit_authorized=auto_authorized,
      run_id=strategy_run_id,
      metadata=template_metadata,
    ),
    status=ExitPlanStatus.EXIT_PENDING,
    entry_filled_volume=200,
    entry_avg_price=8.0,
    pending_intent_id=OTHER_INTENT_ID,
    pending_order_id=OTHER_CLIENT_ORDER_ID,
    pending_rule_id=f"{OTHER_PLAN_ID}:manual",
    pending_requested_volume=200,
  )
  metadata = {"exit_rule_id": f"{OTHER_PLAN_ID}:manual"}
  now = utcnow()
  return (
    AutoExitPlanRecord(
      plan_id=OTHER_PLAN_ID,
      account_id=ACCOUNT_ID,
      instrument_code="600001.SH",
      bucket="swing",
      source_type=source_type,
      source_id="source-other",
      strategy_run_id=strategy_run_id or None,
      source_execution_owner_type=source_owner_type,
      source_execution_owner_id=source_owner_id,
      source_execution_environment=ExecutionEnvironment.LIVE.value,
      enabled=True,
      status="EXIT_PENDING",
      environment=ExecutionEnvironment.LIVE.value,
      config_version=1,
      protected_volume=200,
      exited_volume=0,
      remaining_volume=200,
      entry_avg_price=8.0,
      cost_basis_snapshot={},
      plan_state=domain_plan.to_dict(),
      pending_client_order_id=OTHER_CLIENT_ORDER_ID,
      state_version=3,
      auto_exit_authorized=auto_authorized,
      auto_exit_authorization_fingerprint=("f" * 64 if auto_authorized else None),
      auto_exit_authorization_config_version=(1 if auto_authorized else None),
      auto_exit_authorized_at=(now if auto_authorized else None),
      auto_exit_authorization_expires_at=(
        now + timedelta(minutes=30) if auto_authorized else None
      ),
      auto_exit_authorization_challenge_id=(
        "55555555-5555-4555-8555-555555555555" if auto_authorized else None
      ),
      auto_exit_authorization_user_id=(USER_ID if auto_authorized else None),
      auto_exit_authorization_device_session_id=(
        "other-device-session" if auto_authorized else None
      ),
    ),
    TradeIntentRecord(
      id=OTHER_INTENT_ID,
      owner_type="EXIT_PLAN",
      owner_id=OTHER_PLAN_ID,
      account_id=ACCOUNT_ID,
      instrument_code="600001.SH",
      direction="SELL",
      bucket="swing",
      reason="other plan exit",
      priority="HIGH",
      target_volume=200,
      status="APPROVED",
      executed_volume=0,
      environment=ExecutionEnvironment.LIVE.value,
      idempotency_key=f"{OTHER_INTENT_ID}:idempotency",
      strategy_run_id=None,
      intent_metadata=metadata,
    ),
    PendingTradeOrder(
      client_order_id=OTHER_CLIENT_ORDER_ID,
      user_id=USER_ID,
      account_id=ACCOUNT_ID,
      owner_type="EXIT_PLAN",
      owner_id=OTHER_PLAN_ID,
      instrument_code="600001.SH",
      side="SELL",
      order_type="FIX_PRICE",
      limit_price="8.00",
      volume=200,
      status="DELIVERED" if delivered else "QUEUED",
      broker_order_id=broker_order_id or None,
      environment=ExecutionEnvironment.LIVE.value,
      strategy_run_id=None,
      strategy_order_id=None,
      intent_id=OTHER_INTENT_ID,
      bucket="swing",
      trace_id=f"trace-{owner_kind}",
      request_metadata=metadata,
    ),
    TradeCommandOutbox(
      message_id=OTHER_PLACE_MESSAGE_ID,
      client_order_id=OTHER_CLIENT_ORDER_ID,
      idempotency_key="place-other-exit",
      device_id=DEVICE_ID,
      account_id=ACCOUNT_ID,
      owner_type="EXIT_PLAN",
      owner_id=OTHER_PLAN_ID,
      environment=ExecutionEnvironment.LIVE.value,
      payload={
        "command_kind": "PLACE_ORDER",
        "client_order_id": OTHER_CLIENT_ORDER_ID,
        "account_id": ACCOUNT_ID,
        "execution_mode": "live",
        "instrument_code": "600001.SH",
        "side": "SELL",
        "price_type": "FIX_PRICE",
        "limit_price": "8.00",
        "volume": 200,
        "expires_at": (now + timedelta(minutes=5)).isoformat() + "Z",
      },
      delivery_status="DELIVERED" if delivered else "QUEUED",
      delivered_at=now if delivered else None,
      expires_at=now + timedelta(minutes=5),
      attempts=1 if delivered else 0,
    ),
  )


def _other_correlation(
  owner_kind: str,
  *,
  broker_order_id: str = "",
) -> OrderCorrelation | None:
  metadata = {"exit_rule_id": f"{OTHER_PLAN_ID}:manual"}
  return OrderCorrelation(
    id=f"correlation-{owner_kind}",
    client_order_id=OTHER_CLIENT_ORDER_ID,
    broker_order_id=broker_order_id or None,
    account_id=ACCOUNT_ID,
    owner_type="EXIT_PLAN",
    owner_id=OTHER_PLAN_ID,
    environment=ExecutionEnvironment.LIVE.value,
    strategy_run_id=None,
    strategy_order_id=None,
    intent_id=OTHER_INTENT_ID,
    bucket="swing",
    trace_id=f"trace-{owner_kind}",
    request_metadata=metadata,
  )


def _full_snapshot_payload(
  *,
  snapshot_id: str,
  source_sequence: int,
  reported_at: datetime,
  orders: list[dict] | None = None,
  trades: list[dict] | None = None,
) -> dict:
  payload = {
    "is_complete": True,
    "snapshot_id": snapshot_id,
    "source_sequence": source_sequence,
    "reported_at": reported_at.isoformat(),
    "accounts": [{"account_id": ACCOUNT_ID}],
    "positions": [],
    "positions_by_account": {ACCOUNT_ID: []},
    "orders": list(orders or []),
    "trades": list(trades or []),
    "unavailable_accounts": [],
    "snapshot_authority_by_account": {
      ACCOUNT_ID: {
        "initial_status": 0,
        "final_status": 0,
        "stable": True,
        "snapshot_eligible": True,
        "status_name": "OK",
        "reason_code": "XTTRADING_ACCOUNT_STATUS_AUTHORITATIVE",
      }
    },
    "section_completeness_by_account": {
      ACCOUNT_ID: {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
  }
  payload["snapshot_hash"] = hashlib.sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  return payload


async def _store_full_snapshot(db, payload: dict, *, received_at: datetime) -> None:
  snapshot_id = str(payload["snapshot_id"])
  snapshot_hash = str(payload["snapshot_hash"])
  db.add(
    AgentReportInbox(
      message_id=f"snapshot-inbox-{snapshot_id}",
      device_id=DEVICE_ID,
      message_type="delta_report",
      protocol_version="1.2",
      raw_payload_hash=snapshot_hash,
      business_idempotency_key=f"snapshot:{snapshot_id}",
      payload=payload,
      received_at=received_at,
      processing_status="PROCESSED",
      processing_attempts=1,
      processed_at=received_at,
    )
  )
  control = await db.get(
    AccountExecutionControl,
    ACCOUNT_ID,
    with_for_update=True,
  )
  assert control is not None
  control.last_snapshot_id = snapshot_id
  control.last_snapshot_hash = snapshot_hash
  control.last_snapshot_at = received_at
  await db.commit()


async def _seed(
  db,
  *,
  owner_kind: str,
  delivered: bool,
  broker_order_id: str = "",
  environment: str = ExecutionEnvironment.LIVE.value,
) -> AutoExitPlanRecord:
  now = utcnow()
  plan = _plan(owner_kind=owner_kind, environment=environment)
  db.add_all(
    [
      AuthUser(
        id=USER_ID,
        username="quarantine-user",
        display_name="Quarantine User",
        password_hash="not-used",
        permissions=[],
      ),
      AgentDevice(
        id=DEVICE_ID,
        user_id=USER_ID,
        name="live-agent",
        secret_hash="0" * 64,
        authorized_account_ids=[ACCOUNT_ID],
        capabilities=["live"],
      ),
      RuntimeComponentHeartbeat(
        component=f"qmt-agent:{DEVICE_ID}",
        instance_id=DEVICE_ID,
        status="RECONCILE_REQUIRED",
        details={
          "apiInstanceId": "api-instance-1",
          "agentSessionId": "agent-session-1",
          "serverReceivedAt": now.isoformat(),
          "agentSentAt": now.isoformat(),
          "sessionActive": True,
        },
        updated_at=now,
      ),
      AccountExecutionControl(
        account_id=ACCOUNT_ID,
        authorization_state="ENABLED",
        state_version=4,
        reconcile_status="READY",
        paused_reason=None,
        last_snapshot_id="snapshot-before-contradiction",
        last_snapshot_hash="a" * 64,
        last_snapshot_at=now,
        controlled_window_active=True,
        controlled_window_snapshot_id="snapshot-before-contradiction",
        controlled_window_snapshot_hash="a" * 64,
        controlled_window_started_at=now,
        controlled_window_started_by_user_id=USER_ID,
        controlled_window_external_order_ids=["baseline-order"],
        controlled_window_external_trade_ids=["baseline-trade"],
      ),
      plan,
      TradeIntentRecord(
        id=CURRENT_INTENT_ID,
        owner_type="EXIT_PLAN",
        owner_id=PLAN_ID,
        environment=environment,
        idempotency_key=f"{CURRENT_INTENT_ID}:idempotency",
        account_id=ACCOUNT_ID,
        instrument_code="600000.SH",
        direction="SELL",
        bucket="swing",
        reason="current plan exit",
        priority="HIGH",
        target_volume=100,
        status="APPROVED",
        executed_volume=0,
        strategy_run_id=None,
        intent_metadata={"exit_rule_id": f"{PLAN_ID}:manual"},
      ),
      _pending(broker_order_id=broker_order_id, environment=environment),
      OrderCorrelation(
        id="correlation-current",
        client_order_id=CLIENT_ORDER_ID,
        broker_order_id=broker_order_id or None,
        account_id=ACCOUNT_ID,
        owner_type="EXIT_PLAN",
        owner_id=PLAN_ID,
        environment=environment,
        strategy_run_id=None,
        strategy_order_id=None,
        intent_id=CURRENT_INTENT_ID,
        bucket="swing",
        trace_id="trace-current",
        request_metadata={"exit_rule_id": f"{PLAN_ID}:manual"},
      ),
      _place_outbox(delivered=delivered, environment=environment),
    ]
  )
  await db.commit()
  return plan


async def _seed_physical_gate_rejection(
  db,
  *,
  tamper: str = "",
) -> AccountExecutionControlEvent:
  await _seed(db, owner_kind="monitor", delivered=False)
  plan, intent, pending, outbox = _other_plan_command(delivered=True)
  rejected_at = utcnow()
  outbox.delivered_at = rejected_at - timedelta(seconds=1)
  outbox.delivery_status = "RECONCILE_REQUIRED"
  outbox.last_error = "physical_delivery_exit_plan_final_gate_rejected"
  intent.status = "QUEUED"
  pending.status = "CANCEL_REQUESTED"
  pending.status_reason = "physical delivery final gate rejected"
  pending.request_metadata = {
    **dict(pending.request_metadata or {}),
    QUARANTINE_CANCEL_REQUIRED_METADATA_KEY: True,
    QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
    QUARANTINE_REASON_METADATA_KEY: "PHYSICAL_DELIVERY_GATE_REJECTED",
  }
  if tamper == "attempts":
    outbox.attempts = 2
  elif tamper == "acknowledged":
    outbox.acknowledged_at = rejected_at
  elif tamper == "source_sequence":
    pending.last_source_sequence = 1
  elif tamper == "outbox_error":
    outbox.last_error = "different failure"
  elif tamper:
    raise AssertionError(f"unsupported physical-gate tamper: {tamper}")

  control = await db.get(
    AccountExecutionControl,
    ACCOUNT_ID,
    with_for_update=True,
  )
  assert control is not None
  control.authorization_state = "PAUSED"
  control.reconcile_status = "RECONCILE_REQUIRED"
  control.state_version = int(control.state_version or 0) + 1
  control.paused_reason = json.dumps(
    [
      {
        "kind": "QUARANTINED_ORDER_REPAIR_REQUIRED",
        "business_id": OTHER_CLIENT_ORDER_ID,
        "reason": "PHYSICAL_DELIVERY_GATE_REJECTED",
      }
    ]
  )
  event = AccountExecutionControlEvent(
    event_id=f"physical-delivery-quarantine:{OTHER_PLACE_MESSAGE_ID}",
    account_id=ACCOUNT_ID,
    event_type=LIVE_PLACE_PHYSICAL_GATE_REJECTED,
    previous_state="ENABLED",
    next_state="PAUSED",
    created_at=rejected_at,
    details={
      "triggerPlanId": OTHER_PLAN_ID,
      "triggerIntentId": OTHER_INTENT_ID,
      "reason": "exit-plan intent status changed",
      "commandDispositions": [
        {
          "clientOrderId": OTHER_CLIENT_ORDER_ID,
          "messageId": OTHER_PLACE_MESSAGE_ID,
          "planId": OTHER_PLAN_ID,
          "intentId": OTHER_INTENT_ID,
          "ownerKind": "UNKNOWN",
          "disposition": "PHYSICAL_DELIVERY_GATE_REJECTED",
          "repairReason": "PHYSICAL_DELIVERY_GATE_REJECTED",
        }
      ],
    },
  )
  db.add_all([plan, intent, pending, outbox, event])
  await db.commit()
  return event


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_state", ["none", "repaired", "active"])
@pytest.mark.parametrize("has_evidence", [False, True])
async def test_snapshot_evidence_is_loaded_once_only_for_active_candidates(
  candidate_state, has_evidence, monkeypatch
):
  async with _database() as sessions:
    async with sessions() as db:
      await _seed(db, owner_kind="monitor", delivered=False)
      other_plan, other_intent, other_pending, other_outbox = _other_plan_command(
        delivered=False
      )
      db.add_all([other_plan, other_intent, other_pending, other_outbox])
      if candidate_state != "none":
        pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
        assert pending is not None
        for item in (pending, other_pending):
          item.request_metadata = {
            **dict(item.request_metadata or {}),
            QUARANTINE_REASON_METADATA_KEY: BROKER_EXECUTION_AFTER_RELEASE,
            QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: candidate_state == "active",
          }
        db.add(
          AccountExecutionControlEvent(
            event_id="lazy-evidence-quarantine",
            account_id=ACCOUNT_ID,
            event_type=BROKER_EXECUTION_AFTER_RELEASE,
            created_at=utcnow() - timedelta(seconds=2),
            details={
              "commandDispositions": [
                {
                  "clientOrderId": item.client_order_id,
                  "repairReason": BROKER_EXECUTION_AFTER_RELEASE,
                }
                for item in (pending, other_pending)
              ]
            },
          )
        )
      await db.commit()
      if has_evidence:
        now = utcnow()
        await _store_full_snapshot(
          db,
          _full_snapshot_payload(
            snapshot_id="lazy-evidence", source_sequence=200, reported_at=now
          ),
          received_at=now,
        )
      service = AccountExecutionQuarantineService(db)
      lookup = AsyncMock(wraps=service._latest_full_snapshot_payload)
      monkeypatch.setattr(service, "_latest_full_snapshot_payload", lookup)
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      result = await service.list_quarantined_orders(
        account_id=ACCOUNT_ID, control=control
      )
      if candidate_state == "active":
        assert len(result) == 2
        lookup.assert_awaited_once()
        assert all(not item["repairable"] for item in result)
        if not has_evidence:
          assert {item["blocked_reason"] for item in result} == {
            "LATEST_FULL_SNAPSHOT_EVIDENCE_UNAVAILABLE"
          }
      else:
        assert result == []
        lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_indexed_snapshot_lookup_keeps_searching_past_invalid_newer_evidence():
  async with _database() as sessions:
    async with sessions() as db:
      await _seed(db, owner_kind="monitor", delivered=False)
      now = utcnow()
      payload = _full_snapshot_payload(
        snapshot_id="indexed-evidence", source_sequence=200, reported_at=now
      )
      await _store_full_snapshot(db, payload, received_at=now)
      db.add(
        AgentReportInbox(
          message_id="invalid-newer-evidence",
          device_id=DEVICE_ID,
          message_type="delta_report",
          protocol_version="1.1",
          raw_payload_hash="b" * 64,
          business_idempotency_key="invalid-newer-evidence",
          payload={**payload, "snapshot_hash": "b" * 64},
          received_at=now + timedelta(seconds=1),
          processing_status="PROCESSED",
          processing_attempts=1,
        )
      )
      await db.commit()
      result = await AccountExecutionQuarantineService(
        db
      )._latest_full_snapshot_payload(
        account_id=ACCOUNT_ID,
        snapshot_id="indexed-evidence",
        snapshot_hash=payload["snapshot_hash"],
      )
      assert result == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_kind", ["monitor", "runtime", "dedicated"])
async def test_queued_replacement_sell_is_cancelled_before_delivery_for_every_owner(
  owner_kind: str,
) -> None:
  async with _database() as sessions:
    async with sessions() as db:
      plan = await _seed(db, owner_kind=owner_kind, delivered=False)
      result = await AccountExecutionQuarantineService(
        db
      ).quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key=f"broker-evidence:{owner_kind}:queued",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        broker_order_id="broker-old",
        source_sequence=12,
      )
      await db.commit()

      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      stored_record = await db.get(AutoExitPlanRecord, PLAN_ID)
      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      place = await db.get(TradeCommandOutbox, PLACE_MESSAGE_ID)
      events = (await db.execute(select(AccountExecutionControlEvent))).scalars().all()
      commands = (await db.execute(select(TradeCommandOutbox))).scalars().all()

      assert result.applied is True
      assert result.released_zero_fill_intent_id == CURRENT_INTENT_ID
      assert result.locally_cancelled_client_order_ids == (CLIENT_ORDER_ID,)
      assert control is not None
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert control.authorization_state == "PAUSED"
      assert control.state_version == 5
      assert control.controlled_window_active is False
      assert control.controlled_window_snapshot_id is None
      assert control.controlled_window_snapshot_hash is None
      assert control.controlled_window_started_at is None
      assert control.controlled_window_started_by_user_id is None
      assert control.controlled_window_external_order_ids == []
      assert control.controlled_window_external_trade_ids == []
      paused_reason = json.loads(str(control.paused_reason))
      assert paused_reason[0]["kind"] == BROKER_EXECUTION_AFTER_RELEASE
      assert paused_reason[0]["currentPendingIntentId"] == CURRENT_INTENT_ID
      assert pending is not None and pending.status == "CANCELLED"
      assert pending.status_reason == (
        "EXIT_PLAN_QUARANTINE_CANCELLED_BEFORE_AGENT_DELIVERY"
      )
      assert pending.request_metadata["execution_terminal_source"] == (
        "LOCAL_OUTBOX_CANCEL"
      )
      assert pending.request_metadata["execution_terminal_reason"] == (
        "EXIT_PLAN_QUARANTINE_CANCELLED_BEFORE_AGENT_DELIVERY"
      )
      assert place is not None and place.delivery_status == "CANCELLED"
      assert place.last_error == "account_quarantined_before_agent_delivery"
      assert len(commands) == 1
      assert len(events) == 1
      assert events[0].event_type == BROKER_EXECUTION_AFTER_RELEASE
      assert stored_record is not None
      stored_plan = ExitPlan.from_dict(dict(stored_record.plan_state or {}))
      assert stored_plan.pending_intent_id == ""
      assert stored_plan.pending_order_id == ""
      assert CURRENT_INTENT_ID in stored_plan.reconciled_zero_fill_intent_ids
      assert stored_plan.status == ExitPlanStatus.ERROR
      assert stored_plan.error_message == (
        "ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:old-intent"
      )
      assert stored_record.pending_client_order_id is None
      assert stored_record.status == "ERROR"
      assert stored_record.enabled is False
      assert stored_record.last_error == (
        "ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:old-intent"
      )
      assert stored_record.state_version == 7
      cancelled = ExitPlanBook([stored_plan]).apply_command(
        ExitPlanCommand(
          command=ExitPlanCommandType.CANCEL,
          plan_id=PLAN_ID,
          reason="USER_CANCELLED_AFTER_QUARANTINE",
        )
      )
      assert cancelled is not None
      assert cancelled.status == ExitPlanStatus.CANCELLED


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_kind", ["monitor", "runtime", "dedicated"])
async def test_delivered_replacement_sell_preserves_evidence_and_queues_one_cancel(
  owner_kind: str,
) -> None:
  async with _database() as sessions:
    async with sessions() as db:
      plan = await _seed(
        db,
        owner_kind=owner_kind,
        delivered=True,
        broker_order_id="broker-current",
      )
      service = AccountExecutionQuarantineService(db)
      first = await service.quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key=f"broker-evidence:{owner_kind}:delivered",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        broker_order_id="broker-old",
        source_sequence=13,
      )
      await db.commit()
      replay = await service.quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key=f"broker-evidence:{owner_kind}:delivered",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        broker_order_id="broker-old",
        source_sequence=13,
      )
      await db.commit()

      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      place = await db.get(TradeCommandOutbox, PLACE_MESSAGE_ID)
      stored_record = await db.get(AutoExitPlanRecord, PLAN_ID)
      commands = list(
        (
          await db.execute(
            select(TradeCommandOutbox).order_by(TradeCommandOutbox.created_at)
          )
        )
        .scalars()
        .all()
      )
      cancel_commands = [
        item
        for item in commands
        if str(dict(item.payload or {}).get("command_kind") or "").upper()
        == "CANCEL_ORDER"
      ]

      assert first.applied is True
      assert replay.applied is False
      assert first.released_zero_fill_intent_id == ""
      assert first.cancel_message_ids == replay.cancel_message_ids
      assert pending is not None and pending.status == "CANCEL_REQUESTED"
      assert pending.broker_order_id == "broker-current"
      assert place is not None and place.delivery_status == "RECONCILE_REQUIRED"
      assert len(cancel_commands) == 1
      cancel = cancel_commands[0]
      assert cancel.delivery_status == "QUEUED"
      assert cancel.device_id == DEVICE_ID
      assert cancel.payload["broker_order_id"] == "broker-current"
      expected_key = hashlib.sha256(
        (
          f"cancel:{USER_ID}:{ACCOUNT_ID}:LIVE:EXIT_PLAN:{PLAN_ID}:"
          f"entry-plan-cancel:{CLIENT_ORDER_ID}:broker-current"
        ).encode("utf-8")
      ).hexdigest()
      assert cancel.idempotency_key == expected_key
      assert stored_record is not None
      stored_plan = ExitPlan.from_dict(dict(stored_record.plan_state or {}))
      assert stored_plan.pending_intent_id == CURRENT_INTENT_ID
      assert stored_plan.pending_order_id == CLIENT_ORDER_ID


@pytest.mark.asyncio
async def test_delivered_without_broker_id_stays_non_redeliverable_and_cancel_requested() -> (
  None
):
  async with _database() as sessions:
    async with sessions() as db:
      plan = await _seed(db, owner_kind="runtime", delivered=True)
      await AccountExecutionQuarantineService(db).quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="broker-evidence:delivered-without-id",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        source_sequence=14,
      )
      await db.commit()

      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      place = await db.get(TradeCommandOutbox, PLACE_MESSAGE_ID)
      stored_record = await db.get(AutoExitPlanRecord, PLAN_ID)
      commands = (await db.execute(select(TradeCommandOutbox))).scalars().all()
      assert pending is not None and pending.status == "CANCEL_REQUESTED"
      assert pending.status_reason == (
        "waiting for broker order id after account quarantine"
      )
      assert pending.broker_order_id is None
      assert place is not None and place.delivery_status == "RECONCILE_REQUIRED"
      assert len(commands) == 1
      assert stored_record is not None
      stored_plan = ExitPlan.from_dict(dict(stored_record.plan_state or {}))
      assert stored_plan.pending_intent_id == CURRENT_INTENT_ID
      assert stored_plan.pending_order_id == CLIENT_ORDER_ID


@pytest.mark.asyncio
async def test_late_broker_id_queues_one_cancel_and_keeps_snapshot_blocked(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  async with _database() as sessions:
    monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      plan = await _seed(db, owner_kind="monitor", delivered=True)
      await AccountExecutionQuarantineService(db).quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="broker-evidence:late-broker-id",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        source_sequence=14,
      )
      await db.commit()
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      quarantined_reason = str(control.paused_reason)

    for source_sequence in (20, 21):
      await report_processor._update_pending(
        CLIENT_ORDER_ID,
        status="WORKING",
        broker_order_id="broker-late",
        source_sequence=source_sequence,
      )

    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      place = await db.get(TradeCommandOutbox, PLACE_MESSAGE_ID)
      commands = (await db.execute(select(TradeCommandOutbox))).scalars().all()
      cancels = [
        item
        for item in commands
        if str(dict(item.payload or {}).get("command_kind") or "").upper()
        == "CANCEL_ORDER"
      ]
      assert pending is not None
      assert pending.status == "CANCEL_REQUESTED"
      assert pending.broker_order_id == "broker-late"
      assert place is not None and place.delivery_status == "RECONCILE_REQUIRED"
      assert len(cancels) == 1
      assert cancels[0].delivery_status == "QUEUED"
      assert cancels[0].payload["broker_order_id"] == "broker-late"

    working_snapshot = {
      "orders": [
        {
          "account_id": ACCOUNT_ID,
          "client_order_id": CLIENT_ORDER_ID,
          "order_id": "broker-late",
          "order_status": "WORKING",
        }
      ],
      "trades": [],
    }
    reconciliation = await report_processor._snapshot_discrepancies(
      ACCOUNT_ID,
      working_snapshot,
      allow_external_activity=True,
    )
    assert reconciliation["blocking_discrepancies"] == [
      {
        "kind": "QUARANTINED_ORDER_REPAIR_REQUIRED",
        "business_id": CLIENT_ORDER_ID,
      }
    ]

    monkeypatch.setattr(
      report_processor,
      "_invalidate_t_trade_entry_authority_for_account",
      AsyncMock(),
    )
    monkeypatch.setattr(
      report_processor,
      "_rederive_t_trade_exit_authorizations_after_position_update",
      AsyncMock(),
    )
    position_service = SimpleNamespace(
      prepare_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "PREPARED"}
      ),
      finalize_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "APPLIED"}
      ),
      mark_snapshot_failure=AsyncMock(),
    )
    (
      _result,
      blocked,
      _summary,
    ) = await report_processor._reconcile_authoritative_full_account_locked(
      ACCOUNT_ID,
      working_snapshot,
      snapshot_id="snapshot-cancel-pending",
      snapshot_hash="b" * 64,
      reported_at=utcnow(),
      sequence=30,
      positions=[],
      position_service=position_service,
    )
    assert blocked is True
    async with sessions() as db:
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert control.authorization_state == "PAUSED"
      assert str(control.paused_reason) == quarantined_reason

    await report_processor._update_pending(
      CLIENT_ORDER_ID,
      status="CANCELLED",
      broker_order_id="broker-late",
      source_sequence=31,
      cumulative_filled_volume=0,
    )
    (
      _result,
      blocked,
      _summary,
    ) = await report_processor._reconcile_authoritative_full_account_locked(
      ACCOUNT_ID,
      working_snapshot,
      snapshot_id="snapshot-terminal-still-working",
      snapshot_hash="c" * 64,
      reported_at=utcnow(),
      sequence=31,
      positions=[],
      position_service=position_service,
    )
    assert blocked is True
    async with sessions() as db:
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert control.authorization_state == "PAUSED"
      assert str(control.paused_reason) == quarantined_reason

    (
      _result,
      blocked,
      _summary,
    ) = await report_processor._reconcile_authoritative_full_account_locked(
      ACCOUNT_ID,
      {"orders": [], "trades": []},
      snapshot_id="snapshot-cancel-terminal",
      snapshot_hash="d" * 64,
      reported_at=utcnow(),
      sequence=32,
      positions=[],
      position_service=position_service,
    )
    assert blocked is True
    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert pending is not None and pending.status == "CANCELLED"
      assert control is not None
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert control.authorization_state == "PAUSED"
      assert str(control.paused_reason) == quarantined_reason


@pytest.mark.asyncio
async def test_cached_order_report_observes_concurrent_quarantine_cancel_request(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  order_cached = asyncio.Event()
  quarantine_committed = asyncio.Event()

  async with _database() as sessions:
    async with sessions() as db:
      await _seed(db, owner_kind="monitor", delivered=True)
      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      assert pending is not None
      pending.status = "PENDING"
      await db.commit()

    async def process_order_after_quarantine() -> None:
      async with sessions() as order_db:
        cached = await order_db.get(PendingTradeOrder, CLIENT_ORDER_ID)
        assert cached is not None and cached.status == "PENDING"
        # SQLite cannot retain a read transaction across the concurrent writer,
        # but expire_on_commit=False preserves the same stale identity-map row
        # that PostgreSQL READ COMMITTED retains while waiting on FOR UPDATE.
        await order_db.commit()
        order_cached.set()
        await quarantine_committed.wait()

        @asynccontextmanager
        async def order_session_local():
          yield order_db

        monkeypatch.setattr(
          report_processor,
          "AsyncSessionLocal",
          order_session_local,
        )
        for source_sequence in (20, 21):
          await report_processor._update_pending(
            CLIENT_ORDER_ID,
            status="WORKING",
            broker_order_id="broker-after-quarantine",
            source_sequence=source_sequence,
          )

    order_task = asyncio.create_task(process_order_after_quarantine())
    await order_cached.wait()
    async with sessions() as quarantine_db:
      plan = await quarantine_db.get(AutoExitPlanRecord, PLAN_ID)
      assert plan is not None
      await AccountExecutionQuarantineService(
        quarantine_db
      ).quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="broker-evidence:cached-before-quarantine",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        source_sequence=19,
      )
      await quarantine_db.commit()
      pending = await quarantine_db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      assert pending is not None and pending.status == "CANCEL_REQUESTED"
    quarantine_committed.set()
    await order_task

    monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      commands = (await db.execute(select(TradeCommandOutbox))).scalars().all()
      cancels = [
        item
        for item in commands
        if str(dict(item.payload or {}).get("command_kind") or "").upper()
        == "CANCEL_ORDER"
      ]
      assert pending is not None
      assert pending.status == "CANCEL_REQUESTED"
      assert pending.broker_order_id == "broker-after-quarantine"
      assert len(cancels) == 1
      assert cancels[0].payload["broker_order_id"] == "broker-after-quarantine"

    reconciliation = await report_processor._snapshot_discrepancies(
      ACCOUNT_ID,
      {
        "orders": [
          {
            "account_id": ACCOUNT_ID,
            "client_order_id": CLIENT_ORDER_ID,
            "order_id": "broker-after-quarantine",
            "order_status": "WORKING",
          }
        ],
        "trades": [],
      },
      allow_external_activity=True,
    )
    assert reconciliation["blocking_discrepancies"] == [
      {
        "kind": "QUARANTINED_ORDER_REPAIR_REQUIRED",
        "business_id": CLIENT_ORDER_ID,
      }
    ]


@pytest.mark.asyncio
async def test_clean_snapshot_cannot_overwrite_concurrent_quarantine(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  discrepancies_computed = asyncio.Event()
  quarantine_committed = asyncio.Event()

  async with _database() as sessions:
    monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      await _seed(db, owner_kind="runtime", delivered=True)

    original_discrepancies = report_processor._snapshot_discrepancies

    async def pause_after_discrepancies(*args, **kwargs):
      result = await original_discrepancies(*args, **kwargs)
      assert result["blocking_discrepancies"] == []
      discrepancies_computed.set()
      await quarantine_committed.wait()
      return result

    monkeypatch.setattr(
      report_processor,
      "_snapshot_discrepancies",
      pause_after_discrepancies,
    )
    monkeypatch.setattr(
      report_processor,
      "_invalidate_t_trade_entry_authority_for_account",
      AsyncMock(),
    )
    rederive = AsyncMock()
    monkeypatch.setattr(
      report_processor,
      "_rederive_t_trade_exit_authorizations_after_position_update",
      rederive,
    )
    position_service = SimpleNamespace(
      prepare_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "PREPARED"}
      ),
      finalize_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "APPLIED"}
      ),
      mark_snapshot_failure=AsyncMock(),
    )
    snapshot_task = asyncio.create_task(
      report_processor._reconcile_authoritative_full_account_locked(
        ACCOUNT_ID,
        {"orders": [], "trades": []},
        snapshot_id="snapshot-raced-with-quarantine",
        snapshot_hash="e" * 64,
        reported_at=utcnow(),
        sequence=40,
        positions=[],
        position_service=position_service,
      )
    )
    await discrepancies_computed.wait()

    async with sessions() as quarantine_db:
      plan = await quarantine_db.get(AutoExitPlanRecord, PLAN_ID)
      assert plan is not None
      await AccountExecutionQuarantineService(
        quarantine_db
      ).quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="broker-evidence:snapshot-race",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        source_sequence=39,
      )
      await quarantine_db.commit()
      quarantined_control = await quarantine_db.get(
        AccountExecutionControl,
        ACCOUNT_ID,
      )
      assert quarantined_control is not None
      quarantined_version = int(quarantined_control.state_version)
      quarantined_reason = str(quarantined_control.paused_reason)

    quarantine_committed.set()
    _result, blocked, summary = await snapshot_task
    assert blocked is True
    assert summary["status"] == "RECONCILE_REQUIRED"
    assert summary["blockingDiscrepancyCount"] == 1
    position_service.prepare_full_snapshot.assert_awaited_once()
    position_service.finalize_full_snapshot.assert_not_awaited()
    rederive.assert_not_awaited()

    async with sessions() as db:
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      assert control.authorization_state == "PAUSED"
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert control.state_version == quarantined_version
      assert str(control.paused_reason) == quarantined_reason
      assert control.last_snapshot_id == "snapshot-before-contradiction"


@pytest.mark.asyncio
async def test_unrepaired_quarantine_cannot_be_cleared_by_any_clean_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  async with _database() as sessions:
    monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      plan = await _seed(db, owner_kind="monitor", delivered=False)
      await AccountExecutionQuarantineService(
        db
      ).quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="broker-evidence:snapshot-freshness",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        source_sequence=45,
      )
      await db.commit()
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      pause_items = json.loads(str(control.paused_reason))
      quarantined_at = datetime.fromisoformat(pause_items[0]["quarantinedAt"])
      quarantined_version = int(control.state_version)
      quarantined_reason = str(control.paused_reason)

    monkeypatch.setattr(
      report_processor,
      "_invalidate_t_trade_entry_authority_for_account",
      AsyncMock(),
    )
    monkeypatch.setattr(
      report_processor,
      "_rederive_t_trade_exit_authorizations_after_position_update",
      AsyncMock(),
    )
    position_service = SimpleNamespace(
      prepare_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "PREPARED"}
      ),
      finalize_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "APPLIED"}
      ),
      mark_snapshot_failure=AsyncMock(),
    )

    _result, blocked, summary = (
      await report_processor._reconcile_authoritative_full_account_locked(
        ACCOUNT_ID,
        {"orders": [], "trades": []},
        snapshot_id="snapshot-not-newer-than-quarantine",
        snapshot_hash="1" * 64,
        reported_at=quarantined_at,
        sequence=45,
        positions=[],
        position_service=position_service,
      )
    )
    assert blocked is True
    assert summary["blockingDiscrepancyCount"] == 1
    position_service.finalize_full_snapshot.assert_not_awaited()
    async with sessions() as db:
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      assert control.authorization_state == "PAUSED"
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert int(control.state_version) == quarantined_version + 1
      assert str(control.paused_reason) == quarantined_reason
      assert control.last_snapshot_id == "snapshot-not-newer-than-quarantine"

    _result, blocked, summary = (
      await report_processor._reconcile_authoritative_full_account_locked(
        ACCOUNT_ID,
        {"orders": [], "trades": []},
        snapshot_id="snapshot-newer-than-quarantine",
        snapshot_hash="2" * 64,
        reported_at=quarantined_at + timedelta(microseconds=1),
        sequence=46,
        positions=[],
        position_service=position_service,
      )
    )
    assert blocked is True
    assert summary["status"] == "RECONCILE_REQUIRED"
    position_service.finalize_full_snapshot.assert_not_awaited()
    async with sessions() as db:
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      assert control.authorization_state == "PAUSED"
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert int(control.state_version) == quarantined_version + 2
      assert str(control.paused_reason) == quarantined_reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("execution_mode", "expected_blocked"),
  [("paper", False), ("live", True)],
)
async def test_full_snapshot_only_blocks_live_cancel_requests(
  monkeypatch: pytest.MonkeyPatch,
  execution_mode: str,
  expected_blocked: bool,
) -> None:
  async with _database() as sessions:
    monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      await _seed(
        db,
        owner_kind="monitor",
        delivered=True,
        environment=execution_mode.upper(),
      )
      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      assert pending is not None
      pending.status = "CANCEL_REQUESTED"
      await db.commit()

    monkeypatch.setattr(
      report_processor,
      "_invalidate_t_trade_entry_authority_for_account",
      AsyncMock(),
    )
    monkeypatch.setattr(
      report_processor,
      "_rederive_t_trade_exit_authorizations_after_position_update",
      AsyncMock(),
    )
    position_service = SimpleNamespace(
      prepare_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "PREPARED"}
      ),
      finalize_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "APPLIED"}
      ),
      mark_snapshot_failure=AsyncMock(),
    )
    _result, blocked, _summary = (
      await report_processor._reconcile_authoritative_full_account_locked(
        ACCOUNT_ID,
        {"orders": [], "trades": []},
        snapshot_id=f"snapshot-{execution_mode}-cancel-request",
        snapshot_hash="f" * 64,
        reported_at=utcnow(),
        sequence=50,
        positions=[],
        position_service=position_service,
      )
    )
    assert blocked is expected_blocked
    async with sessions() as db:
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      if expected_blocked:
        assert control.reconcile_status == "RECONCILE_REQUIRED"
        assert control.authorization_state == "PAUSED"
        assert json.loads(str(control.paused_reason)) == [
          {
            "kind": "CANCEL_REQUEST_PENDING",
            "business_id": CLIENT_ORDER_ID,
          }
        ]
        position_service.finalize_full_snapshot.assert_not_awaited()
      else:
        assert control.reconcile_status == "READY"
        assert control.authorization_state == "ENABLED"
        position_service.finalize_full_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_clean_snapshot_never_revives_an_account_wide_stale_live_sell() -> (
  None
):
  async with _database() as sessions:
    async with sessions() as db:
      await _seed(db, owner_kind="runtime", delivered=False)
      now = utcnow()
      other = TradeCommandOutbox(
        message_id="44444444-4444-4444-8444-444444444444",
        client_order_id="other-live-sell",
        idempotency_key="other-live-sell-key",
        device_id=DEVICE_ID,
        account_id=ACCOUNT_ID,
        owner_type="EXIT_PLAN",
        owner_id=OTHER_PLAN_ID,
        environment=ExecutionEnvironment.LIVE.value,
        payload={
          "command_kind": "PLACE_ORDER",
          "client_order_id": "other-live-sell",
          "account_id": ACCOUNT_ID,
          "execution_mode": "live",
          "side": "SELL",
          "expires_at": (now + timedelta(minutes=5)).isoformat() + "Z",
        },
        delivery_status="QUEUED",
        expires_at=now + timedelta(minutes=5),
        attempts=0,
      )
      db.add(other)
      await db.flush()
      other_message_id = str(other.message_id)
      plan = await db.get(AutoExitPlanRecord, PLAN_ID)
      assert plan is not None
      service = AccountExecutionQuarantineService(db)
      await service.quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="broker-evidence:delivery-gate",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        source_sequence=15,
      )
      await db.commit()

      blocked = await service.lock_command_for_delivery(
        message_id=other_message_id,
        now=now,
        redelivery_before=now - timedelta(seconds=10),
      )
      assert blocked.command is None
      assert blocked.blocked_reason == "ACCOUNT_RECONCILE_REQUIRED"
      await db.rollback()

      control = await db.get(
        AccountExecutionControl,
        ACCOUNT_ID,
        with_for_update=True,
      )
      assert control is not None
      # Clean full-snapshot convergence returns an automatic PAUSE to the
      # read-only DISABLED stage; it must never silently restore ENABLED.
      control.reconcile_status = "READY"
      control.authorization_state = "DISABLED"
      control.paused_reason = None
      await db.commit()

      still_blocked = await service.lock_command_for_delivery(
        message_id=other_message_id,
        now=now,
        redelivery_before=now - timedelta(seconds=10),
      )
      assert still_blocked.command is None
      assert still_blocked.blocked_reason == "COMMAND_NOT_DELIVERABLE"
      sealed = await db.get(TradeCommandOutbox, other_message_id)
      assert sealed is not None
      assert sealed.delivery_status == "RECONCILE_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("mismatch", "terminal_status"),
  [
    ("none", "CANCELLED"),
    ("pending", "RECONCILE_REQUIRED"),
    ("outbox", "RECONCILE_REQUIRED"),
    ("plan", "RECONCILE_REQUIRED"),
  ],
)
async def test_quarantined_replacement_never_revives_after_clean_snapshot_and_reenable(
  mismatch: str,
  terminal_status: str,
) -> None:
  async with _database() as sessions:
    async with sessions() as db:
      plan = await _seed(db, owner_kind="runtime", delivered=False)
      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      place = await db.get(TradeCommandOutbox, PLACE_MESSAGE_ID)
      assert pending is not None and place is not None
      if mismatch == "pending":
        await db.execute(
          update(PendingTradeOrder)
          .where(PendingTradeOrder.client_order_id == CLIENT_ORDER_ID)
          .values(owner_id="different-plan")
        )
        await db.refresh(pending)
      elif mismatch == "outbox":
        await db.execute(
          update(TradeCommandOutbox)
          .where(TradeCommandOutbox.message_id == PLACE_MESSAGE_ID)
          .values(owner_id="different-plan")
        )
        await db.refresh(place)
      elif mismatch == "plan":
        domain_plan = ExitPlan.from_dict(dict(plan.plan_state or {}))
        domain_plan.pending_order_id = "different-client-order"
        plan.plan_state = domain_plan.to_dict()
      await db.flush()

      service = AccountExecutionQuarantineService(db)
      await service.quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key=f"broker-evidence:no-revive:{mismatch}",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        source_sequence=16,
      )
      await db.commit()

      place = await db.get(TradeCommandOutbox, PLACE_MESSAGE_ID)
      stored_record = await db.get(AutoExitPlanRecord, PLAN_ID)
      assert place is not None and place.delivery_status == terminal_status
      assert stored_record is not None
      stored_plan = ExitPlan.from_dict(dict(stored_record.plan_state or {}))
      if mismatch == "none":
        assert stored_plan.pending_intent_id == ""
        assert stored_plan.pending_order_id == ""
      else:
        assert stored_plan.pending_intent_id == CURRENT_INTENT_ID
        assert stored_plan.pending_order_id == (
          "different-client-order" if mismatch == "plan" else CLIENT_ORDER_ID
        )

      control = await db.get(
        AccountExecutionControl,
        ACCOUNT_ID,
        with_for_update=True,
      )
      assert control is not None
      control.reconcile_status = "READY"
      control.authorization_state = "ENABLED"
      control.paused_reason = None
      await db.commit()

      now = utcnow()
      claim = await service.lock_command_for_delivery(
        message_id=PLACE_MESSAGE_ID,
        now=now,
        redelivery_before=now - timedelta(seconds=10),
      )
      assert claim.command is None
      assert claim.blocked_reason == "COMMAND_NOT_DELIVERABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  (
    "mismatch",
    "expected_pending_status",
    "expected_cancel_count",
  ),
  [
    ("pending", "RECONCILE_REQUIRED", 0),
    ("outbox", "CANCEL_REQUESTED", 1),
    ("plan", "RECONCILE_REQUIRED", 0),
  ],
)
async def test_quarantine_mismatch_stays_sticky_across_late_working_and_snapshot(
  monkeypatch: pytest.MonkeyPatch,
  mismatch: str,
  expected_pending_status: str,
  expected_cancel_count: int,
) -> None:
  async with _database() as sessions:
    monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      plan = await _seed(
        db,
        owner_kind="runtime",
        delivered=mismatch != "plan",
      )
      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      place = await db.get(TradeCommandOutbox, PLACE_MESSAGE_ID)
      assert pending is not None and place is not None
      if mismatch == "pending":
        await db.execute(
          update(PendingTradeOrder)
          .where(PendingTradeOrder.client_order_id == CLIENT_ORDER_ID)
          .values(owner_id="different-plan")
        )
        await db.refresh(pending)
      elif mismatch == "outbox":
        await db.execute(
          update(TradeCommandOutbox)
          .where(TradeCommandOutbox.message_id == PLACE_MESSAGE_ID)
          .values(owner_id="different-plan")
        )
        await db.refresh(place)
      else:
        domain_plan = ExitPlan.from_dict(dict(plan.plan_state or {}))
        domain_plan.pending_order_id = "different-client-order"
        plan.plan_state = domain_plan.to_dict()
      await db.flush()
      await AccountExecutionQuarantineService(
        db
      ).quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key=f"broker-evidence:sticky-mismatch:{mismatch}",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        source_sequence=60,
      )
      await db.commit()

    for source_sequence in (61, 62):
      await report_processor._update_pending(
        CLIENT_ORDER_ID,
        status="WORKING",
        broker_order_id=f"broker-sticky-{mismatch}",
        source_sequence=source_sequence,
      )

    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, CLIENT_ORDER_ID)
      place = await db.get(TradeCommandOutbox, PLACE_MESSAGE_ID)
      commands = (await db.execute(select(TradeCommandOutbox))).scalars().all()
      cancels = [
        item
        for item in commands
        if str(dict(item.payload or {}).get("command_kind") or "").upper()
        == "CANCEL_ORDER"
      ]
      assert pending is not None and pending.status == expected_pending_status
      if mismatch == "pending":
        # A conflicting typed owner/correlation chain is not a routable broker
        # fact; the report must remain untouched until explicit repair.
        assert pending.broker_order_id is None
      else:
        assert pending.broker_order_id == f"broker-sticky-{mismatch}"
      assert place is not None and place.delivery_status == "RECONCILE_REQUIRED"
      assert len(cancels) == expected_cancel_count

    monkeypatch.setattr(
      report_processor,
      "_invalidate_t_trade_entry_authority_for_account",
      AsyncMock(),
    )
    monkeypatch.setattr(
      report_processor,
      "_rederive_t_trade_exit_authorizations_after_position_update",
      AsyncMock(),
    )
    position_service = SimpleNamespace(
      prepare_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "PREPARED"}
      ),
      finalize_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "APPLIED"}
      ),
      mark_snapshot_failure=AsyncMock(),
    )
    broker_order_id = f"broker-sticky-{mismatch}"
    _result, blocked, _summary = (
      await report_processor._reconcile_authoritative_full_account_locked(
        ACCOUNT_ID,
        {
          "orders": [
            {
              "account_id": ACCOUNT_ID,
              "client_order_id": CLIENT_ORDER_ID,
              "order_id": broker_order_id,
              "order_status": "WORKING",
            }
          ],
          "trades": [],
        },
        snapshot_id=f"snapshot-sticky-{mismatch}",
        snapshot_hash="9" * 64,
        reported_at=utcnow(),
        sequence=63,
        positions=[],
        position_service=position_service,
      )
    )
    assert blocked is True
    position_service.finalize_full_snapshot.assert_not_awaited()
    async with sessions() as db:
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert control.authorization_state == "PAUSED"
      paused_reason = json.loads(str(control.paused_reason))
      assert paused_reason[0]["kind"] == BROKER_EXECUTION_AFTER_RELEASE
      assert paused_reason[0]["quarantineSourceSequence"] == 60


@pytest.mark.asyncio
async def test_quarantine_preserves_killed_authorization_state() -> None:
  async with _database() as sessions:
    async with sessions() as db:
      plan = await _seed(db, owner_kind="runtime", delivered=False)
      control = await db.get(
        AccountExecutionControl,
        ACCOUNT_ID,
        with_for_update=True,
      )
      assert control is not None
      control.authorization_state = "KILLED"
      await db.commit()

      await AccountExecutionQuarantineService(db).quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="broker-evidence:killed",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        source_sequence=17,
      )
      await db.commit()

      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      assert control.authorization_state == "KILLED"
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert json.loads(str(control.paused_reason))[0]["kind"] == (
        BROKER_EXECUTION_AFTER_RELEASE
      )


@pytest.mark.asyncio
async def test_missing_account_control_is_created_fail_closed() -> None:
  async with _database() as sessions:
    async with sessions() as db:
      plan = await _seed(db, owner_kind="runtime", delivered=False)
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      await db.delete(control)
      await db.commit()

      await AccountExecutionQuarantineService(db).quarantine_released_exit_plan(
        plan=plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="broker-evidence:missing-control",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        source_sequence=18,
      )
      await db.commit()

      recreated = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert recreated is not None
      assert recreated.authorization_state == "PAUSED"
      assert recreated.reconcile_status == "RECONCILE_REQUIRED"
      assert recreated.controlled_window_active is False


@pytest.mark.asyncio
async def test_delivery_claim_locks_account_before_outbox() -> None:
  events: list[str] = []
  now = utcnow()
  payload = {
    "command_kind": "PLACE_ORDER",
    "account_id": ACCOUNT_ID,
    "execution_mode": "live",
    "side": "SELL",
  }
  command = SimpleNamespace(
    message_id=PLACE_MESSAGE_ID,
    account_id=ACCOUNT_ID,
    payload=payload,
    delivery_status="QUEUED",
    delivered_at=None,
    expires_at=now + timedelta(minutes=1),
  )
  control = SimpleNamespace(reconcile_status="READY")

  class CandidateResult:
    @staticmethod
    def one_or_none():
      events.append("candidate")
      return (ACCOUNT_ID, payload)

  async def execute(_query):
    return CandidateResult()

  async def get(model, key, **kwargs):
    assert kwargs == {
      "with_for_update": True,
      "populate_existing": True,
    }
    if model is AccountExecutionControl:
      events.append("account")
      assert key == ACCOUNT_ID
      return control
    assert model is TradeCommandOutbox
    events.append("outbox")
    assert key == PLACE_MESSAGE_ID
    return command

  service = AccountExecutionQuarantineService(SimpleNamespace(execute=execute, get=get))
  claim = await service.lock_command_for_delivery(
    message_id=PLACE_MESSAGE_ID,
    now=now,
    redelivery_before=now - timedelta(seconds=10),
  )

  assert claim.command is command
  assert events == ["candidate", "account", "outbox"]


@pytest.mark.asyncio
async def test_delivery_refreshes_cached_ready_control_after_concurrent_quarantine() -> (
  None
):
  async with _database() as sessions:
    now = utcnow()
    other_message_id = "cached-ready-other-message"
    async with sessions() as seed_db:
      await _seed(seed_db, owner_kind="runtime", delivered=False)
      seed_db.add(
        TradeCommandOutbox(
          message_id=other_message_id,
          client_order_id="cached-ready-other-client",
          idempotency_key="cached-ready-other-place",
          device_id=DEVICE_ID,
          account_id=ACCOUNT_ID,
          owner_type="EXIT_PLAN",
          owner_id=OTHER_PLAN_ID,
          environment=ExecutionEnvironment.LIVE.value,
          payload={
            "command_kind": "PLACE_ORDER",
            "client_order_id": "cached-ready-other-client",
            "account_id": ACCOUNT_ID,
            "execution_mode": "live",
            "side": "SELL",
            "instrument_code": "600001.SH",
            "price_type": "FIX_PRICE",
            "limit_price": "8.00",
            "volume": 200,
            "expires_at": (now + timedelta(minutes=5)).isoformat() + "Z",
          },
          delivery_status="QUEUED",
          expires_at=now + timedelta(minutes=5),
          attempts=0,
        )
      )
      await seed_db.commit()

    async with sessions() as delivery_db:
      cached_control = await delivery_db.get(AccountExecutionControl, ACCOUNT_ID)
      cached_command = await delivery_db.get(TradeCommandOutbox, other_message_id)
      assert cached_control is not None and cached_control.reconcile_status == "READY"
      assert cached_command is not None and cached_command.delivery_status == "QUEUED"
      await delivery_db.commit()

      async with sessions() as quarantine_db:
        plan = await quarantine_db.get(AutoExitPlanRecord, PLAN_ID)
        assert plan is not None
        await AccountExecutionQuarantineService(
          quarantine_db
        ).quarantine_released_exit_plan(
          plan=plan,
          invalidated_intent_id="released-old-intent",
          evidence_key="broker-evidence:cached-ready-delivery",
          evidence_kind="ORDER",
          evidence_status="WORKING",
          source_sequence=70,
        )
        await quarantine_db.commit()

      claim = await AccountExecutionQuarantineService(
        delivery_db
      ).lock_command_for_delivery(
        message_id=other_message_id,
        now=now,
        redelivery_before=now - timedelta(seconds=10),
      )
      assert claim.command is None
      assert claim.blocked_reason == "ACCOUNT_RECONCILE_REQUIRED"
      assert cached_control.reconcile_status == "RECONCILE_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("owner_kind", "expected_owner"),
  [
    ("monitor", "MONITOR"),
    ("runtime", "RUNTIME_BOOK"),
  ],
)
async def test_account_quarantine_locally_terminates_every_other_queued_live_sell(
  owner_kind: str,
  expected_owner: str,
) -> None:
  async with _database() as sessions:
    async with sessions() as db:
      trigger_plan = await _seed(db, owner_kind="monitor", delivered=False)
      other_plan, other_intent, other_pending, other_outbox = _other_plan_command(
        delivered=False,
        owner_kind=owner_kind,
        auto_authorized=True,
      )
      db.add_all([other_plan, other_intent, other_pending, other_outbox])
      correlation = _other_correlation(owner_kind)
      if correlation is not None:
        db.add(correlation)
      await db.commit()

      result = await AccountExecutionQuarantineService(
        db
      ).quarantine_released_exit_plan(
        plan=trigger_plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="account-wide-other-queued",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        broker_order_id="broker-old",
        source_sequence=80,
      )
      await db.commit()

      stored_other_plan = await db.get(AutoExitPlanRecord, OTHER_PLAN_ID)
      stored_other_intent = await db.get(TradeIntentRecord, OTHER_INTENT_ID)
      stored_other_pending = await db.get(
        PendingTradeOrder,
        OTHER_CLIENT_ORDER_ID,
      )
      stored_other_outbox = await db.get(
        TradeCommandOutbox,
        OTHER_PLACE_MESSAGE_ID,
      )
      event = await db.get(AccountExecutionControlEvent, result.event_id)

      assert stored_other_outbox is not None
      assert stored_other_outbox.delivery_status == "CANCELLED"
      assert stored_other_pending is not None
      assert stored_other_pending.status == "CANCELLED"
      assert stored_other_pending.request_metadata[
        "account_execution_quarantine_repair_required"
      ] is True
      assert stored_other_intent is not None
      assert stored_other_intent.status == "RECONCILED_ZERO_FILL"
      assert stored_other_plan is not None
      domain_other = ExitPlan.from_dict(dict(stored_other_plan.plan_state or {}))
      assert domain_other.pending_intent_id == ""
      assert domain_other.pending_order_id == ""
      assert domain_other.status == ExitPlanStatus.ERROR
      assert stored_other_plan.enabled is False
      assert stored_other_plan.last_error == (
        f"ACCOUNT_WIDE_STALE_SELL:{OTHER_INTENT_ID}"
      )
      assert stored_other_plan.state_version == 4
      assert stored_other_plan.auto_exit_authorized is False
      assert stored_other_plan.auto_exit_authorization_fingerprint is None
      assert stored_other_plan.auto_exit_authorization_config_version is None
      assert stored_other_plan.auto_exit_authorized_at is None
      assert stored_other_plan.auto_exit_authorization_expires_at is None
      assert stored_other_plan.auto_exit_authorization_challenge_id is None
      assert stored_other_plan.auto_exit_authorization_user_id is None
      assert stored_other_plan.auto_exit_authorization_device_session_id is None
      assert domain_other.template.auto_exit_authorized is False
      with pytest.raises(ValueError, match="EXIT_PLAN_RECONCILIATION_REQUIRED"):
        ExitPlanBook([domain_other]).apply_command(
          ExitPlanCommand(
            command=ExitPlanCommandType.RESUME,
            plan_id=OTHER_PLAN_ID,
          )
        )
      assert event is not None
      disposition = next(
        item
        for item in event.details["commandDispositions"]
        if item["clientOrderId"] == OTHER_CLIENT_ORDER_ID
      )
      assert disposition["planId"] == OTHER_PLAN_ID
      assert disposition["intentId"] == OTHER_INTENT_ID
      assert disposition["ownerKind"] == expected_owner
      assert disposition["disposition"] == "ACCOUNT_WIDE_STALE_SELL"
      assert disposition["localDisposition"] == "CANCELLED_BEFORE_AGENT_DELIVERY"
      assert "planId" not in event.details
      assert event.details["triggerPlanId"] == PLAN_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_kind", ["monitor", "runtime"])
async def test_account_quarantine_seals_every_other_delivered_live_sell_and_cancels(
  owner_kind: str,
) -> None:
  async with _database() as sessions:
    async with sessions() as db:
      trigger_plan = await _seed(db, owner_kind="runtime", delivered=False)
      other_plan, other_intent, other_pending, other_outbox = _other_plan_command(
        delivered=True,
        broker_order_id="broker-other",
        owner_kind=owner_kind,
      )
      db.add_all([other_plan, other_intent, other_pending, other_outbox])
      correlation = _other_correlation(owner_kind, broker_order_id="broker-other")
      if correlation is not None:
        db.add(correlation)
      await db.commit()

      result = await AccountExecutionQuarantineService(
        db
      ).quarantine_released_exit_plan(
        plan=trigger_plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="account-wide-other-delivered",
        evidence_kind="ORDER",
        evidence_status="WORKING",
        broker_order_id="broker-old",
        source_sequence=81,
      )
      await db.commit()

      stored_pending = await db.get(PendingTradeOrder, OTHER_CLIENT_ORDER_ID)
      stored_place = await db.get(TradeCommandOutbox, OTHER_PLACE_MESSAGE_ID)
      stored_plan = await db.get(AutoExitPlanRecord, OTHER_PLAN_ID)
      cancel_commands = list(
        (
          await db.execute(
            select(TradeCommandOutbox).where(
              TradeCommandOutbox.payload["command_kind"].as_string()
              == "CANCEL_ORDER"
            )
          )
        )
        .scalars()
        .all()
      )
      assert stored_pending is not None
      assert stored_pending.status == "CANCEL_REQUESTED"
      assert stored_pending.request_metadata[
        "account_execution_quarantine_repair_required"
      ] is True
      assert stored_place is not None
      assert stored_place.delivery_status == "RECONCILE_REQUIRED"
      assert stored_plan is not None
      sticky_plan = ExitPlan.from_dict(dict(stored_plan.plan_state or {}))
      assert sticky_plan.status == ExitPlanStatus.ERROR
      assert sticky_plan.pending_intent_id == OTHER_INTENT_ID
      assert sticky_plan.pending_order_id == OTHER_CLIENT_ORDER_ID
      assert stored_plan.enabled is False
      assert stored_plan.last_error == (
        f"ACCOUNT_WIDE_STALE_SELL:{OTHER_INTENT_ID}"
      )
      assert stored_plan.state_version == 4
      matching_cancels = [
        item
        for item in cancel_commands
        if item.payload.get("broker_order_id") == "broker-other"
      ]
      assert len(matching_cancels) == 1
      event = await db.get(AccountExecutionControlEvent, result.event_id)
      assert event is not None
      disposition = next(
        item
        for item in event.details["commandDispositions"]
        if item["clientOrderId"] == OTHER_CLIENT_ORDER_ID
      )
      assert disposition["planId"] == OTHER_PLAN_ID
      assert disposition["intentId"] == OTHER_INTENT_ID
      assert disposition["planSticky"] is True

      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      control.reconcile_status = "READY"
      control.authorization_state = "DISABLED"
      await db.commit()
      blocked = await AccountExecutionQuarantineService(
        db
      ).lock_command_for_physical_send(
        message_id=OTHER_PLACE_MESSAGE_ID,
        expected_payload=dict(stored_place.payload or {}),
      )
      assert blocked.command is None
      assert blocked.blocked_reason == "COMMAND_NOT_DELIVERABLE"


@pytest.mark.asyncio
async def test_account_quarantine_seals_live_sell_outbox_without_pending_projection(
) -> None:
  async with _database() as sessions:
    async with sessions() as db:
      trigger_plan = await _seed(db, owner_kind="dedicated", delivered=False)
      _, _, _, orphan_outbox = _other_plan_command(delivered=False)
      db.add(orphan_outbox)
      await db.commit()

      result = await AccountExecutionQuarantineService(
        db
      ).quarantine_released_exit_plan(
        plan=trigger_plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="account-wide-orphan-place",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        broker_order_id="broker-old",
        source_sequence=82,
      )
      await db.commit()

      stored = await db.get(TradeCommandOutbox, OTHER_PLACE_MESSAGE_ID)
      event = await db.get(AccountExecutionControlEvent, result.event_id)
      assert stored is not None
      assert stored.delivery_status == "RECONCILE_REQUIRED"
      assert event is not None
      disposition = next(
        item
        for item in event.details["commandDispositions"]
        if item["clientOrderId"] == OTHER_CLIENT_ORDER_ID
      )
      assert disposition == {
        "clientOrderId": OTHER_CLIENT_ORDER_ID,
        "messageId": OTHER_PLACE_MESSAGE_ID,
        "planId": OTHER_PLAN_ID,
        # An orphan outbox has no durable intent relation.  The retained
        # business metadata cannot manufacture one for quarantine routing.
        "intentId": "",
        "ownerKind": "INVALID",
        "previousDeliveryStatus": "QUEUED",
        "disposition": "PLACE_ORDER_BINDING_MISSING",
      }


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["FILLED", "CANCELLED", "REJECTED"])
async def test_account_quarantine_does_not_reopen_historical_terminal_live_sells(
  terminal_status: str,
) -> None:
  async with _database() as sessions:
    async with sessions() as db:
      trigger_plan = await _seed(db, owner_kind="monitor", delivered=False)
      other_plan, other_intent, other_pending, other_outbox = _other_plan_command(
        delivered=True,
        broker_order_id="broker-historical",
      )
      other_pending.status = terminal_status
      other_outbox.delivery_status = "ACKNOWLEDGED"
      other_outbox.acknowledged_at = utcnow()
      db.add_all([other_plan, other_intent, other_pending, other_outbox])
      await db.commit()

      result = await AccountExecutionQuarantineService(
        db
      ).quarantine_released_exit_plan(
        plan=trigger_plan,
        invalidated_intent_id="released-old-intent",
        evidence_key=f"historical-terminal:{terminal_status}",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        broker_order_id="broker-old",
        source_sequence=90,
      )
      await db.commit()

      stored_pending = await db.get(PendingTradeOrder, OTHER_CLIENT_ORDER_ID)
      stored_outbox = await db.get(TradeCommandOutbox, OTHER_PLACE_MESSAGE_ID)
      event = await db.get(AccountExecutionControlEvent, result.event_id)
      assert stored_pending is not None and stored_pending.status == terminal_status
      assert stored_pending.request_metadata.get(
        "account_execution_quarantine_repair_required"
      ) is None
      assert stored_outbox is not None
      assert stored_outbox.delivery_status == "ACKNOWLEDGED"
      assert event is not None
      assert all(
        item["clientOrderId"] != OTHER_CLIENT_ORDER_ID
        for item in event.details["commandDispositions"]
      )
      cancel_count = await db.scalar(
        select(func.count())
        .select_from(TradeCommandOutbox)
        .where(
          TradeCommandOutbox.payload["command_kind"].as_string()
          == "CANCEL_ORDER",
          TradeCommandOutbox.payload["broker_order_id"].as_string()
          == "broker-historical",
        )
      )
      assert cancel_count == 0


@pytest.mark.asyncio
async def test_physical_gate_rejection_repairs_as_proven_zero_fill() -> None:
  async with _database() as sessions:
    async with sessions() as db:
      event = await _seed_physical_gate_rejection(db)
      snapshot_at = event.created_at + timedelta(seconds=2)
      snapshot = _full_snapshot_payload(
        snapshot_id="physical-gate-repair-snapshot",
        source_sequence=1,
        reported_at=snapshot_at,
      )
      await _store_full_snapshot(db, snapshot, received_at=snapshot_at)
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      service = AccountExecutionQuarantineService(db)
      candidates = await service.list_quarantined_orders(
        account_id=ACCOUNT_ID,
        control=control,
      )
      candidate = next(
        item
        for item in candidates
        if item["client_order_id"] == OTHER_CLIENT_ORDER_ID
      )
      assert candidate["repairable"] is True
      assert candidate["blocked_reason"] == ""

      repaired = await service.repair_quarantined_order(
        account_id=ACCOUNT_ID,
        client_order_id=OTHER_CLIENT_ORDER_ID,
        quarantine_reason="PHYSICAL_DELIVERY_GATE_REJECTED",
        snapshot_id="physical-gate-repair-snapshot",
        expected_state_version=int(control.state_version or 0),
        actor_id=USER_ID,
        operator_reason="verified physical send was never attempted",
        operation_id="physical-gate-zero-fill-repair",
      )
      assert repaired.applied is True
      assert repaired.broker_terminal_status == "RECONCILED_ZERO_FILL"
      assert repaired.cumulative_filled_volume == 0
      await db.commit()

      pending = await db.get(PendingTradeOrder, OTHER_CLIENT_ORDER_ID)
      intent = await db.get(TradeIntentRecord, OTHER_INTENT_ID)
      plan = await db.get(AutoExitPlanRecord, OTHER_PLAN_ID)
      outbox = await db.get(TradeCommandOutbox, OTHER_PLACE_MESSAGE_ID)
      assert pending is not None and pending.status == "CANCELLED"
      assert intent is not None and intent.status == "RECONCILED_ZERO_FILL"
      assert plan is not None and plan.enabled is False and plan.status == "ERROR"
      assert plan.pending_client_order_id is None
      assert outbox is not None
      assert outbox.delivery_status == "CANCELLED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "tamper",
  ["attempts", "acknowledged", "source_sequence", "outbox_error"],
)
async def test_physical_gate_zero_fill_proof_fails_closed_after_mutation(
  tamper: str,
) -> None:
  async with _database() as sessions:
    async with sessions() as db:
      event = await _seed_physical_gate_rejection(db, tamper=tamper)
      snapshot_at = event.created_at + timedelta(seconds=2)
      snapshot = _full_snapshot_payload(
        snapshot_id=f"physical-gate-tampered-{tamper}",
        source_sequence=1,
        reported_at=snapshot_at,
      )
      await _store_full_snapshot(db, snapshot, received_at=snapshot_at)
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      candidates = await AccountExecutionQuarantineService(
        db
      ).list_quarantined_orders(
        account_id=ACCOUNT_ID,
        control=control,
      )
      candidate = next(
        item
        for item in candidates
        if item["client_order_id"] == OTHER_CLIENT_ORDER_ID
      )
      assert candidate["repairable"] is False
      assert candidate["blocked_reason"] == "BROKER_TERMINAL_EVIDENCE_REQUIRED"


@pytest.mark.asyncio
async def test_explicit_repair_requires_a_second_strictly_newer_clean_snapshot(
  monkeypatch,
) -> None:
  async with _database() as sessions:
    monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(
      report_processor,
      "_invalidate_t_trade_entry_authority_for_account",
      AsyncMock(),
    )
    monkeypatch.setattr(
      report_processor,
      "_rederive_t_trade_exit_authorizations_after_position_update",
      AsyncMock(),
    )
    async with sessions() as db:
      trigger_plan = await _seed(db, owner_kind="monitor", delivered=False)
      other_plan, other_intent, other_pending, other_outbox = _other_plan_command(
        delivered=False
      )
      db.add_all([other_plan, other_intent, other_pending, other_outbox])
      await db.commit()
      await AccountExecutionQuarantineService(db).quarantine_released_exit_plan(
        plan=trigger_plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="explicit-repair-three-stage",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        broker_order_id="broker-old",
        source_sequence=100,
      )
      await db.commit()
      quarantine_event = await db.scalar(
        select(AccountExecutionControlEvent)
        .where(
          AccountExecutionControlEvent.event_type
          == BROKER_EXECUTION_AFTER_RELEASE
        )
        .order_by(AccountExecutionControlEvent.created_at.desc())
      )
      assert quarantine_event is not None
      snapshot_at = quarantine_event.created_at + timedelta(seconds=2)
      snapshot = _full_snapshot_payload(
        snapshot_id="repair-snapshot-1",
        source_sequence=101,
        reported_at=snapshot_at,
      )
      await _store_full_snapshot(db, snapshot, received_at=snapshot_at)

    async with sessions() as db:
      service = AccountExecutionQuarantineService(db)
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      candidates = await service.list_quarantined_orders(
        account_id=ACCOUNT_ID,
        control=control,
      )
      candidate = next(
        item
        for item in candidates
        if item["client_order_id"] == OTHER_CLIENT_ORDER_ID
      )
      assert candidate["plan_id"] == OTHER_PLAN_ID
      assert candidate["intent_id"] == OTHER_INTENT_ID
      assert candidate["repairable"] is True
      expected_version = int(control.state_version or 0)
      repaired = await service.repair_quarantined_order(
        account_id=ACCOUNT_ID,
        client_order_id=OTHER_CLIENT_ORDER_ID,
        quarantine_reason="ACCOUNT_WIDE_STALE_SELL",
        snapshot_id="repair-snapshot-1",
        expected_state_version=expected_version,
        actor_id=USER_ID,
        operator_reason="verified broker terminal state",
        operation_id="repair-operation-1",
      )
      assert repaired.applied is True
      await db.commit()

      replay = await service.repair_quarantined_order(
        account_id=ACCOUNT_ID,
        client_order_id=OTHER_CLIENT_ORDER_ID,
        quarantine_reason="ACCOUNT_WIDE_STALE_SELL",
        snapshot_id="repair-snapshot-1",
        expected_state_version=expected_version,
        actor_id=USER_ID,
        operator_reason="verified broker terminal state",
        operation_id="repair-operation-1",
      )
      assert replay.applied is False
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      pending = await db.get(PendingTradeOrder, OTHER_CLIENT_ORDER_ID)
      plan = await db.get(AutoExitPlanRecord, OTHER_PLAN_ID)
      assert control is not None
      assert control.authorization_state == "PAUSED"
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      pause = json.loads(str(control.paused_reason))
      assert pause[0]["kind"] == "QUARANTINE_REPAIR_AWAITING_FRESH_SNAPSHOT"
      assert pause[0]["repairSnapshotId"] == "repair-snapshot-1"
      assert pending is not None
      assert pending.request_metadata.get(
        "account_execution_quarantine_repair_required"
      ) is None
      assert plan is not None
      assert plan.enabled is False
      assert plan.status == "ERROR"

    same_position_service = SimpleNamespace(
      prepare_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "PREPARED"}
      ),
      finalize_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "APPLIED"}
      ),
      mark_snapshot_failure=AsyncMock(),
    )
    _result, same_blocked, _summary = (
      await report_processor._reconcile_authoritative_full_account_locked(
        ACCOUNT_ID,
        snapshot,
        snapshot_id="repair-snapshot-1",
        snapshot_hash=str(snapshot["snapshot_hash"]),
        reported_at=snapshot_at,
        sequence=101,
        positions=[],
        position_service=same_position_service,
      )
    )
    assert same_blocked is True
    same_position_service.finalize_full_snapshot.assert_not_awaited()

    snapshot_2_at = snapshot_at + timedelta(seconds=2)
    snapshot_2 = _full_snapshot_payload(
      snapshot_id="repair-snapshot-2",
      source_sequence=102,
      reported_at=snapshot_2_at,
    )
    next_position_service = SimpleNamespace(
      prepare_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "PREPARED"}
      ),
      finalize_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "APPLIED"}
      ),
      mark_snapshot_failure=AsyncMock(),
    )
    _result, next_blocked, _summary = (
      await report_processor._reconcile_authoritative_full_account_locked(
        ACCOUNT_ID,
        snapshot_2,
        snapshot_id="repair-snapshot-2",
        snapshot_hash=str(snapshot_2["snapshot_hash"]),
        reported_at=snapshot_2_at,
        sequence=102,
        positions=[],
        position_service=next_position_service,
      )
    )
    assert next_blocked is False
    next_position_service.finalize_full_snapshot.assert_awaited_once()
    async with sessions() as db:
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      assert control.authorization_state == "DISABLED"
      assert control.reconcile_status == "READY"


@pytest.mark.asyncio
async def test_quarantine_candidate_rejects_late_snapshot_with_stale_sequence() -> None:
  async with _database() as sessions:
    async with sessions() as db:
      trigger_plan = await _seed(db, owner_kind="monitor", delivered=False)
      other_plan, other_intent, other_pending, other_outbox = _other_plan_command(
        delivered=False
      )
      db.add_all([other_plan, other_intent, other_pending, other_outbox])
      await db.commit()
      await AccountExecutionQuarantineService(db).quarantine_released_exit_plan(
        plan=trigger_plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="candidate-stale-sequence",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        source_sequence=105,
      )
      await db.commit()
      event = await db.scalar(
        select(AccountExecutionControlEvent)
        .where(
          AccountExecutionControlEvent.event_type
          == BROKER_EXECUTION_AFTER_RELEASE
        )
        .order_by(AccountExecutionControlEvent.created_at.desc())
      )
      assert event is not None
      snapshot_at = event.created_at + timedelta(minutes=1)
      stale_snapshot = _full_snapshot_payload(
        snapshot_id="candidate-stale-sequence-snapshot",
        source_sequence=105,
        reported_at=snapshot_at,
      )
      await _store_full_snapshot(db, stale_snapshot, received_at=snapshot_at)
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      candidates = await AccountExecutionQuarantineService(
        db
      ).list_quarantined_orders(account_id=ACCOUNT_ID, control=control)
      candidate = next(
        item
        for item in candidates
        if item["client_order_id"] == OTHER_CLIENT_ORDER_ID
      )
      assert candidate["repairable"] is False
      assert candidate["blocked_reason"] == (
        "SNAPSHOT_SEQUENCE_NOT_NEWER_THAN_QUARANTINE"
      )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("broker_status", "repair_allowed"),
  [
    ("CANCELLED", True),
    ("REJECTED", True),
    ("FILLED", False),
  ],
)
async def test_explicit_repair_maps_broker_zero_fill_terminal_safely(
  broker_status: str,
  repair_allowed: bool,
) -> None:
  async with _database() as sessions:
    async with sessions() as db:
      trigger_plan = await _seed(db, owner_kind="monitor", delivered=False)
      other_plan, other_intent, other_pending, other_outbox = _other_plan_command(
        delivered=True,
        broker_order_id="broker-repair-zero",
      )
      db.add_all([other_plan, other_intent, other_pending, other_outbox])
      await db.commit()
      await AccountExecutionQuarantineService(db).quarantine_released_exit_plan(
        plan=trigger_plan,
        invalidated_intent_id="released-old-intent",
        evidence_key=f"repair-zero-terminal:{broker_status}",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        broker_order_id="broker-old",
        source_sequence=110,
      )
      await db.commit()
      event = await db.scalar(
        select(AccountExecutionControlEvent)
        .where(
          AccountExecutionControlEvent.event_type
          == BROKER_EXECUTION_AFTER_RELEASE
        )
        .order_by(AccountExecutionControlEvent.created_at.desc())
      )
      assert event is not None
      snapshot_at = event.created_at + timedelta(seconds=2)
      snapshot = _full_snapshot_payload(
        snapshot_id=f"repair-zero-{broker_status.lower()}",
        source_sequence=111,
        reported_at=snapshot_at,
        orders=[
          {
            "account_id": ACCOUNT_ID,
            "client_order_id": OTHER_CLIENT_ORDER_ID,
            "order_id": "broker-repair-zero",
            "effective_order_status": broker_status,
            "traded_volume": 0,
          }
        ],
      )
      await _store_full_snapshot(db, snapshot, received_at=snapshot_at)
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      service = AccountExecutionQuarantineService(db)
      candidates = await service.list_quarantined_orders(
        account_id=ACCOUNT_ID,
        control=control,
      )
      candidate = next(
        item
        for item in candidates
        if item["client_order_id"] == OTHER_CLIENT_ORDER_ID
      )
      assert candidate["repairable"] is repair_allowed
      if not repair_allowed:
        assert candidate["blocked_reason"] == (
          "BROKER_TERMINAL_EVIDENCE_REQUIRED"
        )
      kwargs = {
        "account_id": ACCOUNT_ID,
        "client_order_id": OTHER_CLIENT_ORDER_ID,
        "quarantine_reason": "ACCOUNT_WIDE_STALE_SELL",
        "snapshot_id": str(snapshot["snapshot_id"]),
        "expected_state_version": int(control.state_version or 0),
        "actor_id": USER_ID,
        "operator_reason": "verified zero-fill broker terminal",
        "operation_id": f"repair-zero-operation-{broker_status.lower()}",
      }
      if not repair_allowed:
        with pytest.raises(ValueError, match="FILLED 委托不能作为零成交"):
          await service.repair_quarantined_order(**kwargs)
        await db.rollback()
      else:
        repaired = await service.repair_quarantined_order(**kwargs)
        assert repaired.applied is True
        assert repaired.broker_terminal_status == broker_status
        assert repaired.cumulative_filled_volume == 0
        await db.commit()

    async with sessions() as verify_db:
      pending = await verify_db.get(PendingTradeOrder, OTHER_CLIENT_ORDER_ID)
      intent = await verify_db.get(TradeIntentRecord, OTHER_INTENT_ID)
      record = await verify_db.get(AutoExitPlanRecord, OTHER_PLAN_ID)
      place = await verify_db.get(TradeCommandOutbox, OTHER_PLACE_MESSAGE_ID)
      assert pending is not None and intent is not None and record is not None
      domain_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if repair_allowed:
        assert pending.status == broker_status
        assert intent.status == "RECONCILED_ZERO_FILL"
        assert domain_plan.pending_intent_id == ""
        assert domain_plan.pending_order_id == ""
        assert domain_plan.status == ExitPlanStatus.ERROR
        assert record.pending_client_order_id is None
        assert place is not None and place.delivery_status == "RECONCILED_TERMINAL"
        repair_event = await verify_db.scalar(
          select(AccountExecutionControlEvent).where(
            AccountExecutionControlEvent.event_type == "QUARANTINED_ORDER_REPAIRED"
          )
        )
        assert repair_event is not None
        assert repair_event.details["operatorReason"] == (
          "verified zero-fill broker terminal"
        )
        assert repair_event.details["operationId"] == (
          f"repair-zero-operation-{broker_status.lower()}"
        )
      else:
        assert pending.status == "CANCEL_REQUESTED"
        assert intent.status == "APPROVED"
        assert domain_plan.pending_intent_id == OTHER_INTENT_ID
        assert domain_plan.pending_order_id == OTHER_CLIENT_ORDER_ID
        assert place is not None and place.delivery_status == "RECONCILE_REQUIRED"


@pytest.mark.asyncio
async def test_orphan_delivered_place_repair_seals_transport_until_new_snapshot(
  monkeypatch,
) -> None:
  async with _database() as sessions:
    monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(
      report_processor,
      "_invalidate_t_trade_entry_authority_for_account",
      AsyncMock(),
    )
    monkeypatch.setattr(
      report_processor,
      "_rederive_t_trade_exit_authorizations_after_position_update",
      AsyncMock(),
    )
    async with sessions() as db:
      trigger_plan = await _seed(db, owner_kind="monitor", delivered=False)
      other_plan, other_intent, _other_pending, orphan_outbox = (
        _other_plan_command(delivered=True)
      )
      db.add_all([other_plan, other_intent, orphan_outbox])
      await db.commit()
      await AccountExecutionQuarantineService(db).quarantine_released_exit_plan(
        plan=trigger_plan,
        invalidated_intent_id="released-old-intent",
        evidence_key="repair-delivered-orphan",
        evidence_kind="TRADE",
        evidence_status="FILLED",
        broker_order_id="broker-old",
        source_sequence=120,
      )
      await db.commit()
      event = await db.scalar(
        select(AccountExecutionControlEvent)
        .where(
          AccountExecutionControlEvent.event_type
          == BROKER_EXECUTION_AFTER_RELEASE
        )
        .order_by(AccountExecutionControlEvent.created_at.desc())
      )
      assert event is not None
      snapshot_at = event.created_at + timedelta(seconds=2)
      snapshot = _full_snapshot_payload(
        snapshot_id="orphan-repair-snapshot-1",
        source_sequence=121,
        reported_at=snapshot_at,
        orders=[
          {
            "account_id": ACCOUNT_ID,
            "client_order_id": OTHER_CLIENT_ORDER_ID,
            "order_id": "broker-orphan-terminal",
            "effective_order_status": "CANCELLED",
            "traded_volume": 0,
          }
        ],
      )
      await _store_full_snapshot(db, snapshot, received_at=snapshot_at)
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      assert control is not None
      candidates = await AccountExecutionQuarantineService(
        db
      ).list_quarantined_orders(account_id=ACCOUNT_ID, control=control)
      orphan = next(
        item
        for item in candidates
        if item["client_order_id"] == OTHER_CLIENT_ORDER_ID
      )
      assert orphan["quarantine_reason"] == "PLACE_ORDER_BINDING_MISSING"
      assert orphan["repairable"] is True
      repaired = await AccountExecutionQuarantineService(
        db
      ).repair_quarantined_order(
        account_id=ACCOUNT_ID,
        client_order_id=OTHER_CLIENT_ORDER_ID,
        quarantine_reason="PLACE_ORDER_BINDING_MISSING",
        snapshot_id="orphan-repair-snapshot-1",
        expected_state_version=int(control.state_version or 0),
        actor_id=USER_ID,
        operator_reason="verified orphan broker terminal",
        operation_id="repair-orphan-operation",
      )
      assert repaired.applied is True
      await db.commit()
      place = await db.get(TradeCommandOutbox, OTHER_PLACE_MESSAGE_ID)
      record = await db.get(AutoExitPlanRecord, OTHER_PLAN_ID)
      assert place is not None and place.delivery_status == "RECONCILED_TERMINAL"
      assert record is not None and record.pending_client_order_id is None
      repaired_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert repaired_plan.pending_intent_id == ""
      assert repaired_plan.pending_order_id == ""

    same_position_service = SimpleNamespace(
      prepare_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "PREPARED"}
      ),
      finalize_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "APPLIED"}
      ),
      mark_snapshot_failure=AsyncMock(),
    )
    _result, blocked, _summary = (
      await report_processor._reconcile_authoritative_full_account_locked(
        ACCOUNT_ID,
        snapshot,
        snapshot_id="orphan-repair-snapshot-1",
        snapshot_hash=str(snapshot["snapshot_hash"]),
        reported_at=snapshot_at,
        sequence=121,
        positions=[],
        position_service=same_position_service,
      )
    )
    assert blocked is True
    same_position_service.finalize_full_snapshot.assert_not_awaited()

    next_at = snapshot_at + timedelta(seconds=2)
    next_snapshot = _full_snapshot_payload(
      snapshot_id="orphan-repair-snapshot-2",
      source_sequence=122,
      reported_at=next_at,
    )
    next_position_service = SimpleNamespace(
      prepare_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "PREPARED"}
      ),
      finalize_full_snapshot=AsyncMock(
        return_value={"applied": True, "reason": "APPLIED"}
      ),
      mark_snapshot_failure=AsyncMock(),
    )
    _result, blocked, _summary = (
      await report_processor._reconcile_authoritative_full_account_locked(
        ACCOUNT_ID,
        next_snapshot,
        snapshot_id="orphan-repair-snapshot-2",
        snapshot_hash=str(next_snapshot["snapshot_hash"]),
        reported_at=next_at,
        sequence=122,
        positions=[],
        position_service=next_position_service,
      )
    )
    assert blocked is False
    next_position_service.finalize_full_snapshot.assert_awaited_once()
    async with sessions() as db:
      control = await db.get(AccountExecutionControl, ACCOUNT_ID)
      place = await db.get(TradeCommandOutbox, OTHER_PLACE_MESSAGE_ID)
      assert control is not None
      assert control.authorization_state == "DISABLED"
      assert control.reconcile_status == "READY"
      assert place is not None and place.delivery_status == "RECONCILED_TERMINAL"
      delivery = await AccountExecutionQuarantineService(
        db
      ).lock_command_for_delivery(
        message_id=OTHER_PLACE_MESSAGE_ID,
        now=next_at,
        redelivery_before=next_at - timedelta(seconds=10),
      )
      assert delivery.command is None
      assert delivery.blocked_reason == "COMMAND_NOT_DELIVERABLE"
