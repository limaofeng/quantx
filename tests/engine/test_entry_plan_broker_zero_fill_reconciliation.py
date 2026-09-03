from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from unittest.mock import AsyncMock

import pytest
from quantx_api import agent_api
from quantx_contracts import PROTOCOL_VERSION
from quantx_domain.clock import to_naive_utc, utcnow
from quantx_domain.enums import StrategyRunMode
from quantx_domain.strategies.ashare_managed_entry_plan import (
  MANAGED_ENTRY_STATE_KEY,
  AshareManagedEntryPlanStrategy,
)
from quantx_domain.strategies.base import OrderStateEvent, StrategyContext
from quantx_domain.trading import (
  EntryPlanStatus,
  ExitEvaluationContext,
  ExitPlan,
  ExitPlanBook,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
  ManagedEntryPlanState,
)
from quantx_engine import report_processor
from quantx_engine.strategy_manager import strategy_manager
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AccountExecutionControlEvent,
  AgentDevice,
  AgentReportInbox,
  OrderCorrelation,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.liquidation import ConditionalLiquidationOrder
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import (
  auto_exit_plan_service,
  exit_plan_zero_fill_safety,
)
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.trade_intent_processor import (
  LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _snapshot_report(
  *,
  terminal_status: str,
  snapshot_id: str,
  source_sequence: int = 10,
) -> AgentReportInbox:
  snapshot_at = datetime(2026, 8, 20, 10, 5, tzinfo=timezone.utc)
  payload = {
    "snapshot_id": snapshot_id,
    "is_complete": True,
    "source_sequence": source_sequence,
    "source_event_at": snapshot_at.isoformat(),
    "accounts": [{"account_id": "account-1"}],
    "positions_by_account": {"account-1": []},
    "snapshot_authority_by_account": {
      "account-1": {
        "initial_status": 0,
        "final_status": 0,
        "stable": True,
        "snapshot_eligible": True,
        "status_name": "OK",
        "reason_code": "XTTRADING_ACCOUNT_STATUS_AUTHORITATIVE",
      }
    },
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "orders": [
      {
        "client_order_id": "client-1",
        "account_id": "account-1",
        "order_id": 9001,
        "stock_code": "605499.SH",
        "order_type": 23,
        "order_status": terminal_status,
        "order_volume": 100,
        "traded_volume": 0,
        "traded_price": 0,
        "price": 10,
        "source_sequence": source_sequence,
        "source_event_at": snapshot_at.isoformat(),
      }
    ],
    "trades": [],
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  return AgentReportInbox(
    message_id=f"snapshot-report-{snapshot_id}",
    device_id="device-1",
    message_type="delta_report",
    protocol_version=PROTOCOL_VERSION,
    client_order_id=None,
    raw_payload_hash="a" * 64,
    business_idempotency_key=f"snapshot:{snapshot_id}",
    payload=payload,
    received_at=utcnow(),
    processing_status="PROCESSING",
  )


def _terminal_report(terminal_status: str) -> AgentReportInbox:
  return AgentReportInbox(
    message_id=f"terminal-report-{terminal_status}",
    device_id="device-1",
    message_type="order_report",
    protocol_version=PROTOCOL_VERSION,
    client_order_id="client-1",
    raw_payload_hash="b" * 64,
    business_idempotency_key=f"terminal:{terminal_status}",
    payload={
      "client_order_id": "client-1",
      "source_sequence": 10,
      "order": {
        "client_order_id": "client-1",
        "account_id": "account-1",
        "order_id": 9001,
        "stock_code": "605499.SH",
        "order_type": 23,
        "order_status": terminal_status,
        "order_volume": 100,
        "traded_volume": 0,
        "price": 10,
      },
    },
    received_at=utcnow(),
    processing_status="PROCESSING",
  )


def _late_exit_trade_report(*, execution_id: str, volume: int = 20) -> AgentReportInbox:
  return AgentReportInbox(
    message_id=f"late-exit-trade-{execution_id}",
    device_id="device-1",
    message_type="execution_report",
    protocol_version=PROTOCOL_VERSION,
    client_order_id="client-1",
    raw_payload_hash="d" * 64,
    business_idempotency_key=f"late-exit-trade:{execution_id}",
    payload={
      "client_order_id": "client-1",
      "source_sequence": 11,
      "execution": {
        "client_order_id": "client-1",
        "account_id": "account-1",
        "order_id": 9001,
        "execution_id": execution_id,
        "stock_code": "605499.SH",
        "order_type": 24,
        "traded_volume": volume,
        "traded_price": 11.1,
        "traded_time": datetime(
          2026, 8, 20, 10, 6, tzinfo=timezone.utc
        ).isoformat(),
      },
    },
    received_at=utcnow(),
    processing_status="PROCESSING",
  )


def _exit_snapshot_report(
  *,
  terminal_status: str = "CANCELLED",
  snapshot_id: str = "exit-snapshot",
) -> AgentReportInbox:
  snapshot = _snapshot_report(
    terminal_status=terminal_status,
    snapshot_id=snapshot_id,
  )
  snapshot.payload["orders"][0]["order_type"] = 24
  hash_input = {
    key: value for key, value in snapshot.payload.items() if key != "snapshot_hash"
  }
  snapshot.payload["snapshot_hash"] = sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  return snapshot


async def _database(monkeypatch: pytest.MonkeyPatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    AgentDevice.__table__,
    RuntimeComponentHeartbeat.__table__,
    PendingTradeOrder.__table__,
    OrderCorrelation.__table__,
    StrategyRuntimeEvent.__table__,
    AccountExecutionControl.__table__,
    AccountExecutionControlEvent.__table__,
    TradeCommandOutbox.__table__,
    TradeIntentRecord.__table__,
    AutoExitPlanRecord.__table__,
    AutoExitPlanEvent.__table__,
    ConditionalLiquidationOrder.__table__,
    Order.__table__,
    Trade.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=tables,
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(auto_exit_plan_service, "AsyncSessionLocal", sessions)
  return engine, sessions


async def _seed_managed_order(
  sessions,
  *,
  terminal_status: str,
  snapshot: AgentReportInbox,
) -> None:
  snapshot_at = to_naive_utc(
    datetime.fromisoformat(snapshot.payload["source_event_at"])
  )
  entry_metadata = {
    "owner_type": "STRATEGY_RUN",
    "owner_id": "plan-1",
    "strategy_run_id": "plan-1",
    "entry_plan_id": "plan-1",
    "intent_id": "intent-1",
    "entry_stage_id": "stage-1",
    "side": "BUY",
  }
  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="entry-zero-fill",
        display_name="Entry Zero Fill",
        password_hash="unused",
        permissions=[],
      )
    )
    db.add(
      TradeIntentRecord(
        id="intent-1",
        idempotency_key="intent-1-key",
        strategy_run_id="plan-1",
        owner_type="STRATEGY_RUN",
        owner_id="plan-1",
        environment="LIVE",
        account_id="account-1",
        instrument_code="605499.SH",
        direction="BUY",
        bucket="core",
        reason="MANAGED_ENTRY",
        target_volume=100,
        status=terminal_status,
        executed_volume=0,
        executed_price=None,
        executed_time=None,
        intent_metadata=dict(entry_metadata),
      )
    )
    db.add(
      PendingTradeOrder(
        client_order_id="client-1",
        user_id="user-1",
        account_id="account-1",
        instrument_code="605499.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price="10",
        volume=100,
        status=terminal_status,
        broker_order_id="9001",
        owner_type="STRATEGY_RUN",
        owner_id="plan-1",
        environment="LIVE",
        strategy_run_id="plan-1",
        strategy_order_id="strategy-order-1",
        intent_id="intent-1",
        bucket="core",
        request_metadata=dict(entry_metadata),
        last_source_sequence=10,
        last_source_event_at=snapshot_at,
      )
    )
    db.add(
    OrderCorrelation(
        id="correlation-1",
        client_order_id="client-1",
        broker_order_id="9001",
        account_id="account-1",
        strategy_run_id="plan-1",
        strategy_order_id="strategy-order-1",
        intent_id="intent-1",
        bucket="core",
        owner_type="STRATEGY_RUN",
        owner_id="plan-1",
        environment="LIVE",
        trace_id="trace-1",
        request_metadata={
          **entry_metadata,
          "instrument_code": "605499.SH",
        },
      )
    )
    db.add(
      AccountExecutionControl(
        account_id="account-1",
        reconcile_status="READY",
        last_snapshot_id=snapshot.payload["snapshot_id"],
        last_snapshot_hash=snapshot.payload["snapshot_hash"],
        last_snapshot_at=snapshot_at,
      )
    )
    await db.commit()


def _pending_exit_plan(*, strategy_run_id: str, dedicated: bool = False) -> ExitPlan:
  plan = ExitPlanBook().register_entry_fill(
    ExitPlanTemplate(
      plan_id="exit-plan-1",
      source_type=(
        "MANUAL_POSITION"
        if dedicated or not strategy_run_id
        else "T_TRADE_BATCH"
      ),
      source_id=(
        "exit-plan-1" if dedicated or not strategy_run_id else "batch-1"
      ),
      account_id="account-1",
      instrument_code="605499.SH",
      bucket="swing" if strategy_run_id else "manual",
      run_id="" if dedicated else strategy_run_id,
      metadata=(
        {"managed_runtime_command_id": "managed-command-1"}
        if dedicated
        else {}
      ),
      rules=[
        ExitRuleSpec(
          rule_id="exit-plan-1:target",
          strategy=ExitRuleType.TARGET_PRICE,
          parameters={"target_price": 11},
        )
      ],
    ),
    volume=100,
    price=10,
  )
  plan.pending_intent_id = "exit-intent-1"
  plan.pending_order_id = "client-1"
  plan.pending_rule_id = "exit-plan-1:target"
  plan.pending_requested_volume = 100
  plan.status = ExitPlanStatus.EXIT_PENDING
  return plan


async def _seed_exit_plan_order(
  sessions,
  *,
  snapshot: AgentReportInbox,
  strategy_run_id: str,
  environment: str = "LIVE",
  wrong_correlation_binding: bool = False,
  executed_volume: int = 0,
  dedicated: bool = False,
  source_owner_type: str | None = None,
) -> None:
  snapshot_at = to_naive_utc(
    datetime.fromisoformat(snapshot.payload["source_event_at"])
  )
  plan = _pending_exit_plan(
    strategy_run_id=strategy_run_id,
    dedicated=dedicated,
  )
  plan_id = plan.plan_id
  intent_id = plan.pending_intent_id
  resolved_source_owner_type = source_owner_type or (
    "MANUAL_COMMAND" if dedicated or not strategy_run_id else "STRATEGY_RUN"
  )
  source_run_id = (
    strategy_run_id
    if resolved_source_owner_type == "STRATEGY_RUN" and not dedicated
    else ""
  )
  owner_metadata = {
    "owner_type": "EXIT_PLAN",
    "owner_id": plan_id,
    "exit_plan_id": plan_id,
    "account_id": "account-1",
    "instrument_code": "605499.SH",
    "intent_id": intent_id,
    "strategy_run_id": source_run_id,
  }
  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="exit-zero-fill",
        display_name="Exit Zero Fill",
        password_hash="unused",
        permissions=[],
      )
    )
    db.add(
      AutoExitPlanRecord(
        plan_id=plan_id,
        account_id="account-1",
        instrument_code="605499.SH",
        bucket=plan.template.bucket,
        source_type=plan.template.source_type,
        source_id=plan.template.source_id,
        strategy_run_id=source_run_id or None,
        source_execution_owner_type=resolved_source_owner_type,
        source_execution_owner_id=(
          plan_id
          if resolved_source_owner_type != "STRATEGY_RUN"
          else strategy_run_id
        ),
        source_execution_environment=environment,
        enabled=True,
        status=plan.status.value,
        environment=environment,
        auto_exit_authorized=False,
        config_version=1,
        state_version=1,
        protected_volume=100,
        exited_volume=0,
        remaining_volume=100,
        entry_avg_price=10,
        plan_state=plan.to_dict(),
        pending_client_order_id="client-1",
      )
    )
    db.add(
      TradeIntentRecord(
        id=intent_id,
        idempotency_key=f"{intent_id}-key",
        strategy_run_id=None,
        owner_type="EXIT_PLAN",
        owner_id=plan_id,
        environment=environment,
        account_id="account-1",
        strategy_id=source_run_id or None,
        instrument_code="605499.SH",
        direction="SELL",
        bucket=plan.template.bucket,
        reason="AUTO_EXIT_TARGET_PRICE",
        target_volume=100,
        status="CANCELLED",
        executed_volume=executed_volume,
        executed_price=10 if executed_volume else None,
        executed_time=snapshot_at if executed_volume else None,
        intent_metadata=dict(owner_metadata),
      )
    )
    db.add(
      PendingTradeOrder(
        client_order_id="client-1",
        user_id="user-1",
        account_id="account-1",
        instrument_code="605499.SH",
        side="SELL",
        order_type="FIX_PRICE",
        limit_price="10",
        volume=100,
        status="CANCELLED",
        broker_order_id="9001",
        owner_type="EXIT_PLAN",
        owner_id=plan_id,
        environment=environment,
        strategy_run_id=None,
        intent_id=intent_id,
        bucket=plan.template.bucket,
        trace_id="exit-trace-1",
        request_metadata=dict(owner_metadata),
        last_source_sequence=10,
        last_source_event_at=snapshot_at,
      )
    )
    correlation_metadata = dict(owner_metadata)
    if wrong_correlation_binding:
      correlation_metadata["owner_id"] = "other-exit-plan"
    db.add(
      OrderCorrelation(
        id="exit-correlation-1",
        client_order_id="client-1",
        broker_order_id="9001",
        account_id="account-1",
        strategy_run_id=None,
        intent_id=intent_id,
        bucket=plan.template.bucket,
        owner_type="EXIT_PLAN",
        owner_id=plan_id,
        environment=environment,
        trace_id="exit-trace-1",
        request_metadata=correlation_metadata,
      )
    )
    db.add(
      AccountExecutionControl(
        account_id="account-1",
        reconcile_status="READY",
        last_snapshot_id=snapshot.payload["snapshot_id"],
        last_snapshot_hash=snapshot.payload["snapshot_hash"],
        last_snapshot_at=snapshot_at,
      )
    )
    await db.commit()


async def _release_exit_plan_zero_fill(
  sessions,
  *,
  add_next_intent: bool = False,
  cancelled: bool = False,
) -> tuple[int, dict[str, object]]:
  async with sessions() as db:
    record = await db.get(AutoExitPlanRecord, "exit-plan-1", with_for_update=True)
    assert record is not None
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
    ExitPlanBook([plan]).apply_order_event(
      plan_id=plan.plan_id,
      intent_id="exit-intent-1",
      status="RECONCILED_ZERO_FILL",
    )
    plan.reconciled_zero_fill_intent_ids = ["exit-intent-1"] + [
      f"historical-zero-fill-{index}" for index in range(20)
    ]
    if add_next_intent:
      plan.pending_intent_id = "exit-intent-2"
      plan.pending_order_id = "client-2"
      plan.pending_rule_id = "exit-plan-1:target"
      plan.pending_requested_volume = 80
      plan.pending_filled_volume = 0
      plan.status = ExitPlanStatus.EXIT_PENDING
    if cancelled:
      plan.status = ExitPlanStatus.CANCELLED
    AutoExitPlanService._sync_record(record, plan)
    await db.commit()
    return int(record.state_version or 0), dict(record.plan_state or {})


async def _seed_delivered_replacement_and_live_agent(
  sessions,
  *,
  strategy_run_id: str,
) -> None:
  now = utcnow()
  owner_metadata = {
    "owner_type": "EXIT_PLAN",
    "owner_id": "exit-plan-1",
    "exit_plan_id": "exit-plan-1",
    "account_id": "account-1",
    "instrument_code": "605499.SH",
    "intent_id": "exit-intent-2",
    "strategy_run_id": strategy_run_id,
  }
  async with sessions() as db:
    db.add_all(
      [
        AgentDevice(
          id="device-1",
          user_id="user-1",
          name="live-agent",
          secret_hash="0" * 64,
          authorized_account_ids=["account-1"],
          capabilities=["live"],
        ),
        RuntimeComponentHeartbeat(
          component="qmt-agent:device-1",
          instance_id="device-1",
          status="RECONCILE_REQUIRED",
          details={
            "apiInstanceId": "api-instance-1",
            "agentSessionId": "agent-session-1",
            "serverReceivedAt": now.isoformat(),
            "sessionActive": True,
          },
          updated_at=now,
        ),
        TradeIntentRecord(
          id="exit-intent-2",
          idempotency_key="exit-intent-2-key",
          strategy_run_id=None,
          owner_type="EXIT_PLAN",
          owner_id="exit-plan-1",
          environment="LIVE",
          account_id="account-1",
          strategy_id=strategy_run_id or None,
          instrument_code="605499.SH",
          direction="SELL",
          bucket="swing" if strategy_run_id else "manual",
          reason="AUTO_EXIT_TARGET_PRICE",
          target_volume=80,
          status="PENDING",
          executed_volume=0,
          intent_metadata=dict(owner_metadata),
        ),
        PendingTradeOrder(
          client_order_id="client-2",
          user_id="user-1",
          account_id="account-1",
          instrument_code="605499.SH",
          side="SELL",
          order_type="FIX_PRICE",
          limit_price="10",
          volume=80,
          status="DELIVERED",
          broker_order_id="9002",
          owner_type="EXIT_PLAN",
          owner_id="exit-plan-1",
          environment="LIVE",
          strategy_run_id=None,
          intent_id="exit-intent-2",
          bucket="swing" if strategy_run_id else "manual",
          trace_id="exit-trace-2",
          request_metadata=dict(owner_metadata),
        ),
        OrderCorrelation(
          id="replacement-correlation-1",
          client_order_id="client-2",
          broker_order_id="9002",
          account_id="account-1",
          owner_type="EXIT_PLAN",
          owner_id="exit-plan-1",
          environment="LIVE",
          strategy_run_id=None,
          intent_id="exit-intent-2",
          bucket="swing" if strategy_run_id else "manual",
          trace_id="exit-trace-2",
          request_metadata=dict(owner_metadata),
        ),
        TradeCommandOutbox(
          message_id="replacement-place-message",
          client_order_id="client-2",
          idempotency_key="replacement-place",
          device_id="device-1",
          account_id="account-1",
          owner_type="EXIT_PLAN",
          owner_id="exit-plan-1",
          environment="LIVE",
          payload={
            "command_kind": "PLACE_ORDER",
            "client_order_id": "client-2",
            "account_id": "account-1",
            "execution_mode": "live",
            "instrument_code": "605499.SH",
            "side": "SELL",
            "intent_id": "exit-intent-2",
            "request_metadata": dict(owner_metadata),
            "expires_at": (now + timedelta(minutes=5)).isoformat() + "Z",
          },
          delivery_status="DELIVERED",
          delivered_at=now,
          expires_at=now + timedelta(minutes=5),
          attempts=1,
        ),
      ]
    )
    await db.commit()


async def _replace_snapshot_proof_with_local_proof(sessions) -> None:
  async with sessions() as db:
    intent = await db.get(
      TradeIntentRecord,
      "exit-intent-1",
      with_for_update=True,
    )
    assert intent is not None
    metadata = dict(intent.intent_metadata or {})
    metadata.pop("qmt_zero_fill_reconciliation", None)
    metadata["execution_terminal_source"] = LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE
    metadata["execution_terminal_reason"] = "command_expired_before_delivery"
    intent.intent_metadata = metadata
    intent.status = "RECONCILED_ZERO_FILL"
    await db.commit()


async def _finalize_exit_intent(sessions, *, cancelled: bool = True) -> None:
  async with sessions() as db:
    record = await db.get(AutoExitPlanRecord, "exit-plan-1", with_for_update=True)
    pending = await db.get(PendingTradeOrder, "client-1", with_for_update=True)
    intent = await db.get(TradeIntentRecord, "exit-intent-1", with_for_update=True)
    assert record is not None and pending is not None and intent is not None
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
    plan.exited_volume = 50
    plan.exit_avg_price = 11.0
    plan.rule_filled_volumes["exit-plan-1:target"] = 50
    plan.pending_intent_id = ""
    plan.pending_order_id = ""
    plan.pending_rule_id = ""
    plan.pending_requested_volume = 0
    plan.pending_filled_volume = 0
    plan.pending_order_terminal = False
    plan.pending_terminal_cumulative_fill = None
    plan.status = (
      ExitPlanStatus.CANCELLED if cancelled else ExitPlanStatus.PARTIALLY_EXITED
    )
    AutoExitPlanService._sync_record(record, plan)
    pending.status = "FILLED"
    pending.broker_order_id = "9001"
    pending.last_source_sequence = 10
    intent.status = "FILLED"
    intent.executed_volume = 50
    intent.executed_price = 11.0
    intent.intent_metadata = {
      key: value
      for key, value in dict(intent.intent_metadata or {}).items()
      if key not in {"qmt_zero_fill_reconciliation", "execution_terminal_source"}
    }
    await db.commit()


def _managed_strategy(terminal_status: str) -> AshareManagedEntryPlanStrategy:
  strategy = AshareManagedEntryPlanStrategy(
    StrategyContext(
      run_id="plan-1",
      mode=StrategyRunMode.LIVE,
      instruments=["605499.SH"],
      current_time=datetime(2026, 8, 20, 10, 5),
      parameters={
        MANAGED_ENTRY_STATE_KEY: {
          "template_version": 1,
          "config_version": 1,
          "instrument_code": "605499.SH",
          "bucket": "core",
          "target_policy": {
            "mode": "ADDITIONAL_VOLUME",
            "additional_volume": 100,
            "max_total_amount_cny": 20_000,
            "max_position_pct": 0.5,
            "baseline_snapshot": {
              "position_volume": 0,
              "market_value_cny": 0,
              "total_asset_cny": 100_000,
              "reference_price": 10,
              "account_snapshot_version": "snapshot-1",
            },
          },
          "trigger_rules": [
            {
              "rule_id": "manual-1",
              "rule_type": "MANUAL_TRIGGER",
              "priority": 100,
              "parameters": {},
            }
          ],
          "pacing_policy": {
            "tranche_count": 1,
            "max_single_intent_amount_cny": 20_000,
            "max_daily_filled_amount_cny": 20_000,
            "max_orders_per_day": 1,
            "max_open_orders": 1,
          },
          "execution_policy": {
            "environment": "LIVE",
            "authorization_mode": "MANUAL_CONFIRM",
            "price_reference": "ASK1_PROTECTED_LIMIT",
            "approval_ttl_ms": 60_000,
          },
          "completion_policy": {"max_buy_price": 12},
        }
      },
    )
  )
  requested = EntryPlanStatus(terminal_status)
  state = ManagedEntryPlanState(
    phase=EntryPlanStatus.DRAINING,
    terminal_requested=requested,
    terminal_request_reason=f"USER_{terminal_status}",
    pending_intent_id="intent-1",
    pending_stage_id="stage-1",
    pending_rule_id="manual-1",
    pending_rule_type="MANUAL_TRIGGER",
    pending_requested_volume=100,
    pending_requested_amount_cny=1_000,
    reserved_amount_cny=1_000,
  )
  strategy.state.set(
    MANAGED_ENTRY_STATE_KEY,
    state.to_dict(),
    persist=False,
    notify=False,
  )
  return strategy


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["CANCELLED", "EXPIRED"])
async def test_full_snapshot_proves_zero_fill_and_replays_idempotently(
  monkeypatch: pytest.MonkeyPatch,
  terminal_status: str,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status=terminal_status,
    snapshot_id=f"snapshot-{terminal_status.lower()}",
  )
  await _seed_managed_order(
    sessions,
    terminal_status=terminal_status,
    snapshot=snapshot,
  )
  try:
    # The first terminal broker report is not a zero-fill proof because an
    # execution report may still follow it.
    await report_processor._stage_runtime_events(
      _terminal_report(terminal_status)
    )
    async with sessions() as db:
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status == terminal_status
      assert (
        await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent))
        == 1
      )

    await report_processor._stage_runtime_events(snapshot)
    await report_processor._stage_runtime_events(snapshot)
    async with sessions() as db:
      events = list(
        (
          await db.execute(
            select(StrategyRuntimeEvent).order_by(
              StrategyRuntimeEvent.created_at,
              StrategyRuntimeEvent.event_id,
            )
          )
        )
        .scalars()
        .all()
      )
      assert len(events) == 2
      zero_event = events[-1]
      assert (
        zero_event.payload["report"]["effective_order_status"]
        == "RECONCILED_ZERO_FILL"
      )
      audit = zero_event.payload["metadata"][
        "qmt_zero_fill_reconciliation"
      ]
      assert audit["snapshot_id"] == snapshot.payload["snapshot_id"]
      assert audit["broker_terminal_status"] == terminal_status
      assert audit["received_execution_volume"] == 0
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status == "RECONCILED_ZERO_FILL"
      assert intent.intent_metadata[
        "qmt_zero_fill_reconciliation"
      ]["snapshot_id"] == snapshot.payload["snapshot_id"]
      zero_event.application_status = "PROCESSING"
      await db.commit()

    # A restart returns the exact same durable event to PENDING; replaying the
    # full snapshot cannot create a second terminal callback.
    await report_processor._recover_stuck_runtime_events()
    await report_processor._stage_runtime_events(snapshot)
    async with sessions() as db:
      zero_event = next(
        (
          event
          for event in (
            await db.execute(
              select(StrategyRuntimeEvent).order_by(
                StrategyRuntimeEvent.created_at,
                StrategyRuntimeEvent.event_id,
              )
            )
          ).scalars()
          if (
            dict(dict(event.payload or {}).get("report") or {}).get(
              "effective_order_status"
            )
            == "RECONCILED_ZERO_FILL"
          )
        ),
        None,
      )
      assert zero_event is not None
      assert zero_event.application_status == "PENDING"
      assert (
        await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent))
        == 2
      )

    captured_orders = []

    async def capture_order(_run_id, order):
      captured_orders.append(order)

    runtime = type(
      "RuntimeStub",
      (),
      {"context": type("ContextStub", (), {"mode": "LIVE"})()},
    )()
    monkeypatch.setitem(strategy_manager.executor.runs, "plan-1", runtime)
    monkeypatch.setattr(
      strategy_manager.executor,
      "require_durable_event_consumer",
      lambda _run_id: runtime,
    )
    monkeypatch.setattr(
      strategy_manager.executor,
      "apply_durable_order_report",
      capture_order,
    )
    await report_processor._apply_runtime_event(zero_event)
    [order] = captured_orders
    assert order.status == "RECONCILED_ZERO_FILL"
    event = OrderStateEvent.from_raw(order)
    assert event.status == "RECONCILED_ZERO_FILL"
    assert event.metadata["qmt_zero_fill_reconciliation"]["snapshot_id"] == (
      snapshot.payload["snapshot_id"]
    )

    strategy = _managed_strategy(terminal_status)
    patch = await strategy.on_order(event)
    assert patch is not None
    settled = patch.set[MANAGED_ENTRY_STATE_KEY]
    assert settled["phase"] == terminal_status
    assert settled["pending_intent_id"] == ""
    assert settled["filled_volume"] == 0
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_snapshot_zero_fill_lock_refreshes_concurrent_quarantine(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status="CANCELLED",
    snapshot_id="snapshot-raced-with-account-quarantine",
  )
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
  )
  checkpoint_cached = asyncio.Event()
  quarantine_committed = asyncio.Event()

  try:
    async def prove_after_quarantine():
      async with sessions() as proof_db:
        cached = await proof_db.get(AccountExecutionControl, "account-1")
        assert cached is not None
        assert cached.reconcile_status == "READY"
        # End SQLite's read transaction while retaining the READY identity-map
        # object, matching a PostgreSQL READ COMMITTED transaction that waits
        # between its first read and later SELECT FOR UPDATE.
        await proof_db.commit()
        checkpoint_cached.set()
        await quarantine_committed.wait()
        return await report_processor._full_snapshot_zero_fill_items(
          proof_db,
          snapshot,
        )

    proof_task = asyncio.create_task(prove_after_quarantine())
    await checkpoint_cached.wait()
    async with sessions() as quarantine_db:
      control = await quarantine_db.get(
        AccountExecutionControl,
        "account-1",
        with_for_update=True,
      )
      assert control is not None
      control.authorization_state = "PAUSED"
      control.reconcile_status = "RECONCILE_REQUIRED"
      control.paused_reason = '[{"kind":"BROKER_EXECUTION_AFTER_RELEASE"}]'
      control.state_version = int(control.state_version or 0) + 1
      await quarantine_db.commit()
    quarantine_committed.set()

    assert await proof_task == []
    async with sessions() as db:
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent is not None
      assert intent.status == "CANCELLED"
      assert "qmt_zero_fill_reconciliation" not in dict(
        intent.intent_metadata or {}
      )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_snapshot_proves_strategy_owned_exit_zero_fill_end_to_end(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(snapshot_id="strategy-exit-zero-fill")
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="exit-run-1",
  )

  try:
    await report_processor._stage_runtime_events(snapshot)
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (
          await db.execute(
            select(StrategyRuntimeEvent).order_by(
              StrategyRuntimeEvent.created_at,
              StrategyRuntimeEvent.event_id,
            )
          )
        )
        .scalars()
        .all()
      )
      assert len(events) == 2
      zero_events = [
        event
        for event in events
        if event.payload["report"].get("effective_order_status")
        == "RECONCILED_ZERO_FILL"
      ]
      assert len(zero_events) == 1
      audit = zero_events[0].payload["metadata"][
        "qmt_zero_fill_reconciliation"
      ]
      assert "execution_owner" not in audit
      assert audit["exit_plan_id"] == "exit-plan-1"
      assert audit["intent_id"] == "exit-intent-1"
      assert "strategy_run_id" not in audit
      assert audit["client_order_id"] == "client-1"
      assert audit["broker_order_id"] == "9001"
      intent = await db.get(TradeIntentRecord, "exit-intent-1")
      assert intent.status == "RECONCILED_ZERO_FILL"
      assert intent.executed_volume == 0
      assert intent.intent_metadata[
        "qmt_zero_fill_reconciliation"
      ]["snapshot_id"] == snapshot.payload["snapshot_id"]
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_snapshot_proves_monitor_exit_and_recovery_releases_plan(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(snapshot_id="monitor-exit-zero-fill")
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="",
  )

  try:
    await report_processor._stage_runtime_events(snapshot)
    async with sessions() as db:
      intent = await db.get(TradeIntentRecord, "exit-intent-1")
      assert intent.status == "RECONCILED_ZERO_FILL"
      audit = intent.intent_metadata["qmt_zero_fill_reconciliation"]
      assert "execution_owner" not in audit
      assert audit["exit_plan_id"] == "exit-plan-1"
      assert "strategy_run_id" not in audit
      reconciled_at = audit["reconciled_at"]
      assert (
        await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent))
        == 2
      )

    # The typed EXIT_PLAN correlation owns one durable runtime event. Replaying
    # the same snapshot remains idempotent, and pending-order recovery consumes
    # the authoritative intent proof before any market gate.
    await report_processor._stage_runtime_events(snapshot)
    async with sessions() as db:
      intent = await db.get(TradeIntentRecord, "exit-intent-1")
      assert intent.intent_metadata[
        "qmt_zero_fill_reconciliation"
      ]["reconciled_at"] == reconciled_at
      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert await AutoExitPlanService._recover_pending_submission(
        db,
        record,
        plan,
      )
      AutoExitPlanService._sync_record(record, plan)
      await db.commit()

    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      recovered = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert recovered.status == ExitPlanStatus.ACTIVE
      assert recovered.pending_intent_id == ""
      assert recovered.pending_order_id == ""
      assert recovered.reconciled_zero_fill_intent_ids == ["exit-intent-1"]
      assert record.pending_client_order_id is None
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("wrong_correlation_binding", "executed_volume"),
  [(True, 0), (False, 1)],
  ids=["wrong-correlation-owner", "persisted-execution"],
)
async def test_full_snapshot_exit_zero_fill_proof_rejects_unsafe_binding_or_fill(
  monkeypatch: pytest.MonkeyPatch,
  wrong_correlation_binding: bool,
  executed_volume: int,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(
    snapshot_id=(
      "exit-wrong-binding"
      if wrong_correlation_binding
      else "exit-existing-execution"
    )
  )
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="exit-run-1",
    wrong_correlation_binding=wrong_correlation_binding,
    executed_volume=executed_volume,
  )

  try:
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
      )
      assert not any(
        event.payload["report"].get("effective_order_status")
        == "RECONCILED_ZERO_FILL"
        for event in events
      )
      intent = await db.get(TradeIntentRecord, "exit-intent-1")
      assert intent.status != "RECONCILED_ZERO_FILL"
      assert int(intent.executed_volume or 0) == executed_volume
      assert "qmt_zero_fill_reconciliation" not in intent.intent_metadata
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "source_owner_type",
  ["T_ASSISTANT_EXECUTION", "ENTRY_PLAN", "BOARD_ASSISTANT_EXECUTION"],
)
async def test_exit_zero_fill_binding_rejects_non_exit_source_owner(
  monkeypatch: pytest.MonkeyPatch,
  source_owner_type: str,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(
    snapshot_id=f"unsupported-exit-source-{source_owner_type}"
  )
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="",
    source_owner_type=source_owner_type,
  )

  try:
    async with sessions() as db:
      result = await exit_plan_zero_fill_safety.invalidate_exit_plan_zero_fill_proof(
        db,
        client_order_id="client-1",
        evidence_kind="ORDER",
        evidence_status="ACCEPTED",
        evidence_key="unsupported-source-evidence",
        broker_order_id="9001",
        source_sequence=11,
      )
    assert result.exact_binding is False
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("status", "execution_evidence", "evidence_kind"),
  [
    ("ACCEPTED", False, "ORDER"),
    ("PARTIAL_FILLED", True, "TRADE"),
  ],
)
async def test_later_monitor_broker_evidence_revokes_snapshot_zero_fill_proof(
  monkeypatch: pytest.MonkeyPatch,
  status: str,
  execution_evidence: bool,
  evidence_kind: str,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(snapshot_id="monitor-proof-before-late-evidence")
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="",
  )

  try:
    await report_processor._stage_runtime_events(snapshot)
    result = await report_processor._update_pending(
      "client-1",
      status=status,
      broker_order_id="9001",
      source_sequence=11,
      source_event_at=datetime(2026, 8, 20, 10, 6, tzinfo=timezone.utc),
      execution_evidence=execution_evidence,
      evidence_key=(
        "qmt-trade:account-1:later-monitor-proof"
        if execution_evidence
        else ""
      ),
    )
    # The terminal PendingTradeOrder remains monotonic, while the conflicting
    # evidence independently revokes permission to release/resell.
    assert not result.accepted

    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, "client-1")
      assert pending.status == "CANCELLED"
      intent = await db.get(TradeIntentRecord, "exit-intent-1")
      assert intent.status == "RECONCILE_REQUIRED"
      assert "qmt_zero_fill_reconciliation" not in intent.intent_metadata
      invalidation = intent.intent_metadata[
        "qmt_zero_fill_reconciliation_invalidated"
      ]
      assert invalidation["snapshot_id"] == snapshot.payload["snapshot_id"]
      assert invalidation["evidence_kind"] == evidence_kind
      assert invalidation["evidence_status"] == status
      assert invalidation["evidence_source_sequence"] == 11

      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert await AutoExitPlanService._recover_pending_submission(
        db,
        record,
        plan,
      )
      assert plan.status == ExitPlanStatus.EXIT_PENDING
      assert plan.pending_intent_id == "exit-intent-1"
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_monitor_released_snapshot_proof_late_order_burns_plan_and_keeps_intent2(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(snapshot_id="monitor-release-before-late-order")
  await _seed_exit_plan_order(sessions, snapshot=snapshot, strategy_run_id="")

  try:
    await report_processor._stage_runtime_events(snapshot)
    previous_version, previous_state = await _release_exit_plan_zero_fill(
      sessions,
      add_next_intent=True,
    )
    result = await report_processor._update_pending(
      "client-1",
      status="ACCEPTED",
      broker_order_id="9001",
      source_sequence=11,
      source_event_at=datetime(2026, 8, 20, 10, 6, tzinfo=timezone.utc),
    )
    assert not result.accepted

    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      assert record is not None
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert not record.enabled
      assert record.status == "ERROR"
      assert record.state_version == previous_version + 1
      assert plan.status == ExitPlanStatus.ERROR
      assert plan.error_message == (
        "ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:exit-intent-1"
      )
      assert plan.pending_intent_id == previous_state["pending_intent_id"]
      assert plan.pending_order_id == previous_state["pending_order_id"]
      assert plan.pending_rule_id == previous_state["pending_rule_id"]
      assert plan.rule_state == previous_state["rule_state"]
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True], ids=["active", "cancelled"])
async def test_monitor_released_snapshot_proof_late_trade_disables_before_fill(
  monkeypatch: pytest.MonkeyPatch,
  cancelled: bool,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(snapshot_id=f"monitor-late-trade-{cancelled}")
  await _seed_exit_plan_order(sessions, snapshot=snapshot, strategy_run_id="")

  try:
    await report_processor._stage_runtime_events(snapshot)
    await _release_exit_plan_zero_fill(
      sessions,
      add_next_intent=True,
      cancelled=cancelled,
    )
    result = await report_processor._update_pending(
      "client-1",
      status="PARTIAL_FILLED",
      broker_order_id="9001",
      source_sequence=11,
      execution_evidence=True,
      evidence_key=f"qmt-trade:account-1:late-execution-{cancelled}",
    )
    assert not result.accepted
    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      assert record is not None and not record.enabled and record.status == "ERROR"
      before_fill = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert before_fill.exited_volume == 0
      assert before_fill.pending_intent_id == "exit-intent-2"

    await AutoExitPlanService().apply_execution_for_report(
      execution_id=f"late-execution-{cancelled}",
      client_order_id="client-1",
      broker_order_id="9001",
      volume=20,
      price=10.8,
    )
    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      assert record is not None and not record.enabled
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert plan.exited_volume == 20
      assert plan.remaining_volume == 80
      assert plan.pending_intent_id == "exit-intent-2"
      assert plan.pending_order_id == "client-2"
      assert plan.pending_rule_id == "exit-plan-1:target"
      assert plan.status == ExitPlanStatus.ERROR
      assert plan.error_message == (
        "ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:exit-intent-1"
      )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("owner_kind", "strategy_run_id", "dedicated"),
  [
    ("monitor", "", False),
    ("runtime", "exit-run-1", False),
    ("dedicated", "exit-run-1", True),
  ],
)
@pytest.mark.parametrize("evidence_kind", ["ORDER", "TRADE"])
async def test_released_working_order_cancels_evidence_and_replacement_once(
  monkeypatch: pytest.MonkeyPatch,
  owner_kind: str,
  strategy_run_id: str,
  dedicated: bool,
  evidence_kind: str,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(
    snapshot_id=f"evidence-cancel-{owner_kind}-{evidence_kind.lower()}"
  )
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id=strategy_run_id,
    dedicated=dedicated,
  )

  try:
    await report_processor._stage_runtime_events(snapshot)
    if evidence_kind == "TRADE":
      await _replace_snapshot_proof_with_local_proof(sessions)
    await _release_exit_plan_zero_fill(sessions, add_next_intent=True)
    await _seed_delivered_replacement_and_live_agent(
      sessions,
      strategy_run_id=strategy_run_id,
    )

    for source_sequence in (11, 12):
      update = await report_processor._update_pending(
        "client-1",
        status="ACCEPTED" if evidence_kind == "ORDER" else "PARTIAL_FILLED",
        broker_order_id="9001",
        source_sequence=source_sequence,
        execution_evidence=evidence_kind == "TRADE",
        evidence_key=(
          f"qmt-trade:account-1:stable-{owner_kind}"
          if evidence_kind == "TRADE"
          else ""
        ),
      )
      assert not update.accepted

    async with sessions() as db:
      pending_evidence = await db.get(PendingTradeOrder, "client-1")
      pending_replacement = await db.get(PendingTradeOrder, "client-2")
      commands = list(
        (await db.execute(select(TradeCommandOutbox))).scalars().all()
      )
      cancels = [
        command
        for command in commands
        if str(dict(command.payload or {}).get("command_kind") or "").upper()
        == "CANCEL_ORDER"
      ]
      assert pending_evidence is not None
      assert pending_evidence.status == "CANCEL_REQUESTED"
      assert pending_replacement is not None
      assert pending_replacement.status == "CANCEL_REQUESTED"
      assert len(cancels) == 2
      assert {str(command.payload["broker_order_id"]) for command in cancels} == {
        "9001",
        "9002",
      }
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("strategy_run_id", "dedicated"),
  [("", False), ("exit-run-1", False), ("exit-run-1", True)],
)
async def test_released_terminal_evidence_only_cancels_replacement(
  monkeypatch: pytest.MonkeyPatch,
  strategy_run_id: str,
  dedicated: bool,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(
    snapshot_id=f"terminal-evidence-{strategy_run_id}-{dedicated}"
  )
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id=strategy_run_id,
    dedicated=dedicated,
  )
  try:
    await report_processor._stage_runtime_events(snapshot)
    await _release_exit_plan_zero_fill(sessions, add_next_intent=True)
    await _seed_delivered_replacement_and_live_agent(
      sessions,
      strategy_run_id=strategy_run_id,
    )
    await report_processor._update_pending(
      "client-1",
      status="FILLED",
      broker_order_id="9001",
      source_sequence=11,
      cumulative_filled_volume=100,
    )

    async with sessions() as db:
      cancels = [
        command
        for command in (
          (await db.execute(select(TradeCommandOutbox))).scalars().all()
        )
        if str(dict(command.payload or {}).get("command_kind") or "").upper()
        == "CANCEL_ORDER"
      ]
      assert len(cancels) == 1
      assert str(cancels[0].payload["broker_order_id"]) == "9002"
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_released_order_late_accepted_ack_cancels_after_broker_id_arrives(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(snapshot_id="accepted-ack-evidence-cancel")
  await _seed_exit_plan_order(sessions, snapshot=snapshot, strategy_run_id="")

  try:
    await _replace_snapshot_proof_with_local_proof(sessions)
    await _release_exit_plan_zero_fill(sessions, add_next_intent=True)
    await _seed_delivered_replacement_and_live_agent(sessions, strategy_run_id="")
    now = utcnow()
    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, "client-1", with_for_update=True)
      assert pending is not None
      pending.broker_order_id = None
      db.add(
        TradeCommandOutbox(
          message_id="released-place-message",
          client_order_id="client-1",
          idempotency_key="released-place",
          device_id="device-1",
          account_id="account-1",
          owner_type="EXIT_PLAN",
          owner_id="exit-plan-1",
          environment="LIVE",
          payload={
            "command_kind": "PLACE_ORDER",
            "client_order_id": "client-1",
            "account_id": "account-1",
            "execution_mode": "live",
            "instrument_code": "605499.SH",
            "side": "SELL",
            "intent_id": "exit-intent-1",
            "request_metadata": dict(pending.request_metadata or {}),
            "expires_at": (now + timedelta(minutes=5)).isoformat() + "Z",
          },
          delivery_status="EXPIRED",
          delivered_at=None,
          expires_at=now - timedelta(seconds=1),
          attempts=0,
          last_error="command_expired_before_delivery",
        )
      )
      await db.commit()

    monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(agent_api, "_wake_runtime_event_consumer", AsyncMock())
    await agent_api._record_command_ack(
      "device-1",
      {
        "command_message_id": "released-place-message",
        "client_order_id": "client-1",
        "accepted": True,
        "reason": "journal_replay",
      },
    )

    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, "client-1")
      assert pending is not None
      assert pending.status == "RECONCILE_REQUIRED"
      assert pending.broker_order_id is None
      assert pending.request_metadata[
        exit_plan_zero_fill_safety.QUARANTINE_CANCEL_REQUIRED_METADATA_KEY
      ] is True
      commands = list((await db.execute(select(TradeCommandOutbox))).scalars())
      cancels = [
        command
        for command in commands
        if str(dict(command.payload or {}).get("command_kind") or "").upper()
        == "CANCEL_ORDER"
      ]
      assert [command.payload["broker_order_id"] for command in cancels] == [
        "9002"
      ]

    for source_sequence in (11, 12):
      update = await report_processor._update_pending(
        "client-1",
        status="ACCEPTED",
        broker_order_id="9001",
        source_sequence=source_sequence,
      )
      assert not update.accepted

    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, "client-1")
      assert pending is not None
      assert pending.status == "CANCEL_REQUESTED"
      assert pending.broker_order_id == "9001"
      commands = list((await db.execute(select(TradeCommandOutbox))).scalars())
      cancels = [
        command
        for command in commands
        if str(dict(command.payload or {}).get("command_kind") or "").upper()
        == "CANCEL_ORDER"
      ]
      assert len(cancels) == 2
      assert {command.payload["broker_order_id"] for command in cancels} == {
        "9001",
        "9002",
      }
      assert len(
        {
          str(command.idempotency_key).split(":attempt:", 1)[0]
          for command in cancels
        }
      ) == 2
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("dedicated", [False, True], ids=["runtime-book", "dedicated"])
async def test_runtime_released_snapshot_late_working_stages_reconcile_required(
  monkeypatch: pytest.MonkeyPatch,
  dedicated: bool,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(snapshot_id=f"runtime-late-order-{dedicated}")
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="exit-run-1",
    dedicated=dedicated,
  )

  try:
    await report_processor._stage_runtime_events(snapshot)
    await _release_exit_plan_zero_fill(sessions, add_next_intent=True)
    late = _terminal_report("ACCEPTED")
    late.message_id = f"late-runtime-order-{dedicated}"
    late.business_idempotency_key = f"late-runtime-order-{dedicated}"
    late.payload["source_sequence"] = 11
    late.payload["order"]["source_sequence"] = 11
    await report_processor._update_pending(
      "client-1",
      status="ACCEPTED",
      broker_order_id="9001",
      source_sequence=11,
    )
    await report_processor._stage_runtime_events(late)

    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      assert record is not None and not record.enabled and record.status == "ERROR"
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert plan.pending_intent_id == "exit-intent-2"
      events = list(
        (
          await db.execute(
            select(StrategyRuntimeEvent).order_by(StrategyRuntimeEvent.created_at)
          )
        )
        .scalars()
        .all()
      )
      reconciliation = [
        event
        for event in events
        if event.payload["report"].get("effective_order_status")
        == "RECONCILE_REQUIRED"
      ]
      assert len(reconciliation) == 1
      assert reconciliation[0].payload["metadata"][
        "zero_fill_proof_invalidation"
      ]["released"] is True
      assert reconciliation[0].payload["metadata"]["intent_id"] == "exit-intent-1"
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "owner_kind",
  ["monitor", "runtime-book", "dedicated"],
)
async def test_released_local_proof_late_working_burns_every_exit_owner(
  monkeypatch: pytest.MonkeyPatch,
  owner_kind: str,
) -> None:
  engine, sessions = await _database(monkeypatch)
  has_runtime = owner_kind != "monitor"
  dedicated = owner_kind == "dedicated"
  snapshot = _exit_snapshot_report(snapshot_id=f"local-late-order-{owner_kind}")
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="exit-run-1" if has_runtime else "",
    dedicated=dedicated,
  )

  try:
    await report_processor._stage_runtime_events(snapshot)
    await _replace_snapshot_proof_with_local_proof(sessions)
    await _release_exit_plan_zero_fill(sessions, add_next_intent=True)
    update = await report_processor._update_pending(
      "client-1",
      status="ACCEPTED",
      broker_order_id="9001",
      source_sequence=11,
    )
    assert not update.accepted

    if has_runtime:
      late = _terminal_report("ACCEPTED")
      late.message_id = f"local-late-order-{owner_kind}"
      late.business_idempotency_key = f"local-late-order-{owner_kind}"
      late.payload["source_sequence"] = 11
      late.payload["order"]["source_sequence"] = 11
      await report_processor._stage_runtime_events(late)

    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      intent = await db.get(TradeIntentRecord, "exit-intent-1")
      control = await db.get(AccountExecutionControl, "account-1")
      assert record is not None and not record.enabled and record.status == "ERROR"
      assert intent is not None and intent.status == "RECONCILE_REQUIRED"
      assert control is not None
      assert control.reconcile_status == "RECONCILE_REQUIRED"
      assert control.authorization_state == "PAUSED"
      assert json.loads(str(control.paused_reason))[0]["kind"] == (
        "BROKER_EXECUTION_AFTER_RELEASE"
      )
      control_events = list(
        (await db.execute(select(AccountExecutionControlEvent))).scalars().all()
      )
      assert [event.event_type for event in control_events] == [
        "BROKER_EXECUTION_AFTER_RELEASE"
      ]
      invalidation = intent.intent_metadata["zero_fill_proof_invalidation"]
      assert invalidation["released"] is True
      assert invalidation["release_kind"] == "ZERO_FILL_PROOF"
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert len(plan.reconciled_zero_fill_intent_ids) == 21
      assert plan.reconciled_zero_fill_intent_ids[0] == "exit-intent-1"
      assert plan.pending_intent_id == "exit-intent-2"
      assert plan.status == ExitPlanStatus.ERROR
      assert plan.error_message == (
        "ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:exit-intent-1"
      )
      assert ExitPlanBook([plan]).evaluate(
        "605499.SH",
        ExitEvaluationContext(
          timestamp=datetime(2026, 8, 20, 10, 7),
          current_price=12.0,
          bid_price=12.0,
          ask_price=12.01,
        ),
      ) == []
      events = list((await db.execute(select(StrategyRuntimeEvent))).scalars().all())
      if has_runtime:
        assert any(
          event.payload["report"].get("effective_order_status")
          == "RECONCILE_REQUIRED"
          for event in events
        )
      else:
        # A monitor has no StrategyRun runtime, but its SELL still has the
        # first-class EXIT_PLAN owner and therefore stages the same durable
        # report events as every other ExitPlan source.
        assert len(events) == 2
        assert all(
          event.owner_type == "EXIT_PLAN" and event.owner_id == "exit-plan-1"
          for event in events
        )
        assert any(
          event.payload["report"].get("effective_order_status")
          == "RECONCILED_ZERO_FILL"
          for event in events
        )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_paper_exit_invalidation_does_not_quarantine_live_account(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _exit_snapshot_report(snapshot_id="paper-late-order")
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="exit-run-paper",
    environment="PAPER",
  )
  quarantine = AsyncMock()
  monkeypatch.setattr(
    exit_plan_zero_fill_safety.AccountExecutionQuarantineService,
    "quarantine_released_exit_plan",
    quarantine,
  )

  try:
    await _replace_snapshot_proof_with_local_proof(sessions)
    async with sessions() as db:
      record = await db.get(
        AutoExitPlanRecord,
        "exit-plan-1",
        with_for_update=True,
      )
      pending = await db.get(
        PendingTradeOrder,
        "client-1",
        with_for_update=True,
      )
      assert record is not None and pending is not None
      await db.commit()
    await _release_exit_plan_zero_fill(sessions, add_next_intent=True)

    update = await report_processor._update_pending(
      "client-1",
      status="ACCEPTED",
      broker_order_id="9001",
      source_sequence=11,
    )

    assert update.accepted is False
    quarantine.assert_not_awaited()
    async with sessions() as db:
      control = await db.get(AccountExecutionControl, "account-1")
      assert control is not None
      assert control.reconcile_status == "READY"
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("owner_kind", "evidence_kind"),
  [
    (owner_kind, evidence_kind)
    for owner_kind in ("monitor", "runtime-book", "dedicated")
    for evidence_kind in ("ORDER", "TRADE")
  ],
)
async def test_ordinary_finalized_intent_late_broker_fact_fails_closed(
  monkeypatch: pytest.MonkeyPatch,
  owner_kind: str,
  evidence_kind: str,
) -> None:
  engine, sessions = await _database(monkeypatch)
  has_runtime = owner_kind != "monitor"
  snapshot = _exit_snapshot_report(snapshot_id=f"ordinary-late-{owner_kind}")
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="exit-run-1" if has_runtime else "",
    dedicated=owner_kind == "dedicated",
  )

  try:
    await _finalize_exit_intent(sessions)
    update = await report_processor._update_pending(
      "client-1",
      status="ACCEPTED" if evidence_kind == "ORDER" else "PARTIAL_FILLED",
      broker_order_id="9001",
      source_sequence=11,
      execution_evidence=evidence_kind == "TRADE",
      evidence_key=(
        f"qmt-trade:account-1:ordinary-{owner_kind}"
        if evidence_kind == "TRADE"
        else ""
      ),
    )
    assert not update.accepted

    if has_runtime:
      if evidence_kind == "ORDER":
        late = _terminal_report("ACCEPTED")
        late.message_id = f"ordinary-late-order-{owner_kind}"
        late.business_idempotency_key = f"ordinary-late-order-{owner_kind}"
        late.payload["source_sequence"] = 11
        late.payload["order"]["source_sequence"] = 11
      else:
        late = _late_exit_trade_report(
          execution_id=f"ordinary-late-trade-{owner_kind}"
        )
      await report_processor._stage_runtime_events(late)
    elif evidence_kind == "TRADE":
      await AutoExitPlanService().apply_execution_for_report(
        execution_id="ordinary-monitor-late-trade",
        client_order_id="client-1",
        broker_order_id="9001",
        volume=20,
        price=11.1,
      )

    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      intent = await db.get(TradeIntentRecord, "exit-intent-1")
      assert record is not None and not record.enabled and record.status == "ERROR"
      assert intent is not None and intent.status == "RECONCILE_REQUIRED"
      invalidation = intent.intent_metadata["zero_fill_proof_invalidation"]
      assert invalidation["released"] is True
      assert invalidation["release_kind"] == "FINALIZED_OR_REPLACED_INTENT"
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      expected_error = "EXIT_FILL_INTENT_MISMATCH:exit-intent-1:CURRENT_PENDING:NONE"
      assert plan.status == ExitPlanStatus.ERROR
      assert plan.error_message == expected_error
      assert plan.pending_intent_id == ""
      assert plan.rule_filled_volumes["exit-plan-1:target"] == 50
      assert plan.exited_volume == (
        70 if owner_kind == "monitor" and evidence_kind == "TRADE" else 50
      )
      events = list((await db.execute(select(StrategyRuntimeEvent))).scalars().all())
      if has_runtime:
        assert len(events) == 1
        assert events[0].payload["metadata"][
          "zero_fill_proof_invalidation"
        ]["error_code"] == expected_error
        if evidence_kind == "ORDER":
          assert events[0].payload["report"]["effective_order_status"] == (
            "RECONCILE_REQUIRED"
          )
        else:
          assert events[0].event_type == "TRADE"
      else:
        assert events == []
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_kind", ["monitor", "runtime-book", "dedicated"])
@pytest.mark.parametrize(
  ("cumulative_filled_volume", "contradiction"),
  [(50, False), (70, True)],
  ids=["same-terminal-replay", "cumulative-increased"],
)
async def test_finalized_filled_order_replay_uses_durable_cumulative_truth(
  monkeypatch: pytest.MonkeyPatch,
  owner_kind: str,
  cumulative_filled_volume: int,
  contradiction: bool,
) -> None:
  engine, sessions = await _database(monkeypatch)
  has_runtime = owner_kind != "monitor"
  snapshot = _exit_snapshot_report(snapshot_id=f"filled-replay-{owner_kind}")
  await _seed_exit_plan_order(
    sessions,
    snapshot=snapshot,
    strategy_run_id="exit-run-1" if has_runtime else "",
    dedicated=owner_kind == "dedicated",
  )

  try:
    await _finalize_exit_intent(sessions, cancelled=False)
    update = await report_processor._update_pending(
      "client-1",
      status="FILLED",
      broker_order_id="9001",
      source_sequence=11,
      cumulative_filled_volume=cumulative_filled_volume,
    )
    if not contradiction:
      assert not update.accepted

    if has_runtime:
      late = _terminal_report("FILLED")
      late.message_id = (
        f"filled-replay-{owner_kind}-{cumulative_filled_volume}"
      )
      late.business_idempotency_key = late.message_id
      late.payload["source_sequence"] = 11
      late.payload["order"]["source_sequence"] = 11
      late.payload["order"]["traded_volume"] = cumulative_filled_volume
      await report_processor._stage_runtime_events(late)

    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "exit-plan-1")
      pending = await db.get(PendingTradeOrder, "client-1")
      intent = await db.get(TradeIntentRecord, "exit-intent-1")
      events = list((await db.execute(select(StrategyRuntimeEvent))).scalars().all())
      assert record is not None and pending is not None and intent is not None
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      assert pending.last_source_sequence == 11
      assert plan.exited_volume == 50
      assert plan.pending_intent_id == ""
      if contradiction:
        assert not record.enabled and record.status == "ERROR"
        assert intent.status == "RECONCILE_REQUIRED"
        assert plan.status == ExitPlanStatus.ERROR
        assert plan.error_message == (
          "EXIT_FILL_INTENT_MISMATCH:exit-intent-1:CURRENT_PENDING:NONE"
        )
        if has_runtime:
          assert len(events) == 1
          assert events[0].payload["report"]["effective_order_status"] == (
            "RECONCILE_REQUIRED"
          )
      else:
        assert record.enabled and record.status == "PARTIALLY_EXITED"
        assert intent.status == "FILLED"
        assert "zero_fill_proof_invalidation" not in intent.intent_metadata
        assert plan.status == ExitPlanStatus.PARTIALLY_EXITED
        assert events == []
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_late_execution_prevents_full_snapshot_zero_fill_proof(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status="CANCELLED",
    snapshot_id="snapshot-after-late-fill",
    source_sequence=12,
  )
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
  )
  late_fill = AgentReportInbox(
    message_id="late-fill-report",
    device_id="device-1",
    message_type="execution_report",
    protocol_version=PROTOCOL_VERSION,
    client_order_id="client-1",
    raw_payload_hash="c" * 64,
    business_idempotency_key="late-fill:1",
    payload={
      "client_order_id": "client-1",
      "execution": {
        "client_order_id": "client-1",
        "account_id": "account-1",
        "order_id": 9001,
        "execution_id": "execution-late-1",
        "stock_code": "605499.SH",
        "order_type": 23,
        "traded_volume": 100,
        "traded_price": 10,
        "traded_time": datetime(
          2026, 8, 20, 10, 4, tzinfo=timezone.utc
        ).isoformat(),
      },
    },
    received_at=utcnow(),
    processing_status="PROCESSING",
  )

  try:
    await report_processor._stage_runtime_events(_terminal_report("CANCELLED"))
    await report_processor._stage_runtime_events(late_fill)
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
      )
      assert len(events) == 2
      assert {
        event.payload["report"].get("effective_order_status")
        for event in events
        if event.event_type == "ORDER"
      } == {"CANCELLED"}
      assert not any(
        event.payload["report"].get("effective_order_status")
        == "RECONCILED_ZERO_FILL"
        for event in events
      )
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.executed_volume == 100
      assert "qmt_zero_fill_reconciliation" not in intent.intent_metadata
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_declared_complete_snapshot_with_incomplete_trade_section_cannot_prove_zero(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status="CANCELLED",
    snapshot_id="snapshot-incomplete-trades",
  )
  snapshot.payload["section_completeness_by_account"]["account-1"][
    "trades"
  ] = False
  hash_input = {
    key: value
    for key, value in snapshot.payload.items()
    if key != "snapshot_hash"
  }
  snapshot.payload["snapshot_hash"] = sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
  )

  try:
    await report_processor._stage_runtime_events(_terminal_report("CANCELLED"))
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
      )
      assert not any(
        event.payload["report"].get("effective_order_status")
        == "RECONCILED_ZERO_FILL"
        for event in events
      )
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status != "RECONCILED_ZERO_FILL"
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_historical_terminal_fill_projection_cannot_regress_to_zero(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status="CANCELLED",
    snapshot_id="snapshot-after-terminal-fill-projection",
    source_sequence=12,
  )
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
  )
  terminal = _terminal_report("CANCELLED")
  terminal.payload["order"]["traded_volume"] = 100
  terminal.payload["order"]["traded_price"] = 10

  try:
    await report_processor._stage_runtime_events(terminal)
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
      )
      assert len(events) == 2
      assert not any(
        event.payload["report"].get("effective_order_status")
        == "RECONCILED_ZERO_FILL"
        for event in events
      )
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status == "RECONCILE_REQUIRED"
      assert intent.notes == (
        "AWAITING_ORDER_EXECUTION_REPORT: terminal=CANCELLED, "
        "expected=100, received=0"
      )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_conflicting_snapshot_fill_fields_fail_closed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine, sessions = await _database(monkeypatch)
  snapshot = _snapshot_report(
    terminal_status="CANCELLED",
    snapshot_id="snapshot-conflicting-fill-fields",
  )
  snapshot.payload["orders"][0]["filled_volume"] = 100
  hash_input = {
    key: value
    for key, value in snapshot.payload.items()
    if key != "snapshot_hash"
  }
  snapshot.payload["snapshot_hash"] = sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  await _seed_managed_order(
    sessions,
    terminal_status="CANCELLED",
    snapshot=snapshot,
  )

  try:
    await report_processor._stage_runtime_events(_terminal_report("CANCELLED"))
    await report_processor._stage_runtime_events(snapshot)

    async with sessions() as db:
      events = list(
        (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
      )
      assert len(events) == 2
      event = next(
        item
        for item in events
        if (
          dict(dict(item.payload or {}).get("report") or {}).get(
            "filled_volume"
          )
          == 100
          and dict(dict(item.payload or {}).get("report") or {}).get(
            "effective_order_status"
          )
          == "CANCELLED"
        )
      )
      assert (
        event.payload["report"]["effective_order_status"] == "CANCELLED"
      )
      intent = await db.get(TradeIntentRecord, "intent-1")
      assert intent.status == "RECONCILE_REQUIRED"
      assert intent.notes == (
        "AWAITING_ORDER_EXECUTION_REPORT: terminal=CANCELLED, "
        "expected=100, received=0"
      )
  finally:
    await engine.dispose()
