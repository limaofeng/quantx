from __future__ import annotations

import json
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from quantx_domain.clock import utcnow
from quantx_engine import report_processor
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountTradingRollout,
  AccountTradingRolloutEvent,
  AgentDevice,
  AgentReportInbox,
  OperationalAlert,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.models.auth import AuthUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _session_with_pending(pending):
  result = MagicMock()
  result.scalars.return_value.all.return_value = pending
  db = SimpleNamespace(execute=AsyncMock(return_value=result))

  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  return SessionContext


@pytest.mark.asyncio
async def test_shadow_reconciliation_classifies_qmt_manual_activity(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    report_processor,
    "AsyncSessionLocal",
    _session_with_pending([]),
  )
  payload = {
    "orders": [
      {"account_id": "account-1", "order_id": 101},
    ],
    "trades": [
      {
        "account_id": "account-1",
        "order_id": 101,
        "execution_id": "trade-1",
      }
    ],
  }

  result = await report_processor._snapshot_discrepancies(
    "account-1",
    payload,
    allow_external_activity=True,
  )

  assert result["blocking_discrepancies"] == []
  assert [item["kind"] for item in result["external_orders"]] == [
    "EXTERNAL_BROKER_ORDER"
  ]
  assert [item["kind"] for item in result["external_trades"]] == [
    "EXTERNAL_BROKER_TRADE"
  ]


@pytest.mark.asyncio
async def test_controlled_reconciliation_blocks_qmt_manual_activity(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    report_processor,
    "AsyncSessionLocal",
    _session_with_pending([]),
  )
  payload = {
    "orders": [{"account_id": "account-1", "order_id": 101}],
    "trades": [
      {
        "account_id": "account-1",
        "order_id": 101,
        "execution_id": "trade-1",
      }
    ],
  }

  result = await report_processor._snapshot_discrepancies(
    "account-1",
    payload,
    allow_external_activity=False,
  )

  assert [item["kind"] for item in result["blocking_discrepancies"]] == [
    "UNKNOWN_BROKER_ORDER",
    "UNKNOWN_BROKER_TRADE",
  ]


@pytest.mark.asyncio
async def test_controlled_reconciliation_accepts_only_acknowledged_baseline(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    report_processor,
    "AsyncSessionLocal",
    _session_with_pending([]),
  )
  payload = {
    "orders": [
      {
        "account_id": "account-1",
        "order_id": 101,
        "order_status": 50,
        "effective_order_status": "EXPIRED",
        "effective_status_reason": "MARKET_SESSION_CLOSED",
      },
      {"account_id": "account-1", "order_id": 102, "order_status": 50},
    ],
    "trades": [
      {
        "account_id": "account-1",
        "order_id": 101,
        "execution_id": "trade-1",
      }
    ],
  }

  result = await report_processor._snapshot_discrepancies(
    "account-1",
    payload,
    allow_external_activity=False,
    acknowledged_external_order_ids={"101"},
    acknowledged_external_trade_ids={"trade-1"},
  )

  assert result["blocking_discrepancies"] == [
    {"kind": "UNKNOWN_BROKER_ORDER", "business_id": "102"}
  ]
  assert result["external_orders"][0]["acknowledged"] is True
  assert result["external_orders"][0]["status"] == "EXPIRED"
  assert result["external_orders"][0]["raw_status"] == "SUBMITTED"
  assert (
    result["external_orders"][0]["status_reason"]
    == "MARKET_SESSION_CLOSED"
  )
  assert result["external_orders"][1]["acknowledged"] is False
  assert result["external_orders"][1]["status"] == "SUBMITTED"
  assert result["external_trades"][0]["acknowledged"] is True


@pytest.mark.asyncio
async def test_new_external_activity_pauses_and_invalidates_controlled_window(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AccountTradingRollout.__table__,
    AccountTradingRolloutEvent.__table__,
    RuntimeComponentHeartbeat.__table__,
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
  monkeypatch.setattr(
    report_processor,
    "_process_order_report",
    AsyncMock(),
  )
  monkeypatch.setattr(report_processor, "_upsert_account", AsyncMock())
  position_service = SimpleNamespace(
    apply_full_snapshot=AsyncMock(),
    apply_position_delta=AsyncMock(),
  )
  monkeypatch.setattr(
    report_processor,
    "PositionService",
    lambda: position_service,
  )
  discrepancy = {
    "kind": "UNKNOWN_BROKER_ORDER",
    "business_id": "new-external-order",
  }
  reconciliation = {
    "blocking_discrepancies": [discrepancy],
    "external_orders": [
      {
        **discrepancy,
        "acknowledged": False,
        "status": "SUBMITTED",
      }
    ],
    "external_trades": [],
  }
  snapshot_discrepancies = AsyncMock(return_value=reconciliation)
  monkeypatch.setattr(
    report_processor,
    "_snapshot_discrepancies",
    snapshot_discrepancies,
  )

  async with sessions() as db:
    db.add(
      AccountTradingRollout(
        account_id="account-1",
        stage="LIVE",
        enabled=True,
        reconcile_status="READY",
        controlled_window_active=True,
        controlled_window_snapshot_id="baseline-snapshot",
        controlled_window_snapshot_hash="b" * 64,
        controlled_window_external_order_ids=["old-terminal-order"],
        controlled_window_external_trade_ids=["old-trade"],
      )
    )
    db.add(
      RuntimeComponentHeartbeat(
        component="qmt-agent:device-1",
        instance_id="device-1",
        status="READY",
        details={"accountReconciliation": {}},
        updated_at=utcnow(),
      )
    )
    await db.commit()

  payload = {
    "snapshot_id": "snapshot-2",
    "is_complete": True,
    "source_sequence": 2,
    "source_event_at": utcnow().isoformat(),
    "accounts": [{"account_id": "account-1"}],
    "positions_by_account": {"account-1": []},
    "orders": [
      {
        "account_id": "account-1",
        "order_id": "new-external-order",
        "order_status": 50,
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

  await report_processor._process_delta_report(
    "device-1",
    payload,
    protocol_version="1.1",
  )

  snapshot_discrepancies.assert_awaited_once_with(
    "account-1",
    payload,
    allow_external_activity=False,
    acknowledged_external_order_ids={"old-terminal-order"},
    acknowledged_external_trade_ids={"old-trade"},
  )
  async with sessions() as db:
    rollout = await db.get(AccountTradingRollout, "account-1")
    assert rollout is not None
    assert rollout.stage == "PAUSED"
    assert rollout.enabled is False
    assert rollout.reconcile_status == "RECONCILE_REQUIRED"
    assert rollout.controlled_window_active is False
    assert rollout.controlled_window_snapshot_id is None
    assert rollout.controlled_window_external_order_ids == []
    assert rollout.controlled_window_external_trade_ids == []
    assert "UNKNOWN_BROKER_ORDER" in str(rollout.paused_reason)

    events = (
      await db.execute(select(AccountTradingRolloutEvent))
    ).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "CONTROLLED_WINDOW_INVALIDATED"
    assert events[0].previous_stage == "LIVE"
    assert events[0].next_stage == "PAUSED"
    assert events[0].snapshot_id == "snapshot-2"

    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      "qmt-agent:device-1",
    )
    assert heartbeat is not None
    # Account reconciliation is projected in the account summary; an already
    # READY Agent heartbeat remains a transport-health signal.
    assert heartbeat.status == "READY"
    assert heartbeat.details["blockedAccounts"] == ["account-1"]
    account_summary = heartbeat.details["accountReconciliation"]["account-1"]
    assert account_summary["newExternalOrderCount"] == 1
    assert account_summary["workingExternalOrderCount"] == 1
    assert account_summary["controlledWindowActive"] is False

  await engine.dispose()


@pytest.mark.asyncio
async def test_missing_quantx_working_order_still_blocks_shadow(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  pending = SimpleNamespace(
    client_order_id="client-1",
    broker_order_id="101",
    status="SUBMITTED",
  )
  monkeypatch.setattr(
    report_processor,
    "AsyncSessionLocal",
    _session_with_pending([pending]),
  )

  result = await report_processor._snapshot_discrepancies(
    "account-1",
    {"orders": [], "trades": []},
    allow_external_activity=True,
  )

  assert result["blocking_discrepancies"] == [
    {"kind": "MISSING_WORKING_ORDER", "business_id": "client-1"}
  ]


@pytest.mark.asyncio
async def test_new_authoritative_snapshot_supersedes_old_snapshot_dead_letter(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    AgentDevice.__table__,
    AgentReportInbox.__table__,
    OperationalAlert.__table__,
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
  now = utcnow()
  old_id = "00000000-0000-4000-8000-000000000001"
  current_id = "00000000-0000-4000-8000-000000000002"
  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="reconcile-test",
        display_name="Reconcile Test",
        password_hash="unused",
        permissions=[],
      )
    )
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="live-agent",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["live"],
      )
    )
    common_payload = {
      "accounts": [{"account_id": "account-1"}],
      "positions_by_account": {"account-1": []},
      "orders": [],
      "trades": [],
      "is_complete": True,
    }
    db.add(
      AgentReportInbox(
        message_id=old_id,
        device_id="device-1",
        message_type="delta_report",
        protocol_version="1.1",
        raw_payload_hash="a" * 64,
        business_idempotency_key="old-snapshot",
        payload=common_payload,
        received_at=now - timedelta(minutes=1),
        processing_status="FAILED",
        processing_attempts=10,
        processing_error="数据库会话未正确配置",
      )
    )
    db.add(
      AgentReportInbox(
        message_id=current_id,
        device_id="device-1",
        message_type="delta_report",
        protocol_version="1.1",
        raw_payload_hash="b" * 64,
        business_idempotency_key="current-snapshot",
        payload=common_payload,
        received_at=now,
        processing_status="PROCESSING",
        processing_attempts=1,
      )
    )
    db.add(
      OperationalAlert(
        id="alert-1",
        fingerprint="f" * 64,
        severity="SEV2",
        source="ENGINE",
        code="AGENT_REPORT_DEAD_LETTER",
        account_id=None,
        business_id=old_id,
        message="old failure",
        details={},
        status="OPEN",
        occurrences=1,
        first_seen_at=now - timedelta(minutes=1),
        last_seen_at=now - timedelta(minutes=1),
      )
    )
    await db.commit()

  await report_processor._finish(current_id)

  async with sessions() as db:
    old_report = await db.get(AgentReportInbox, old_id)
    current_report = await db.get(AgentReportInbox, current_id)
    alert = await db.get(OperationalAlert, "alert-1")
    assert old_report is not None
    assert old_report.processing_status == "SUPERSEDED"
    assert old_report.processing_error == "数据库会话未正确配置"
    assert current_report is not None
    assert current_report.processing_status == "PROCESSED"
    assert alert is not None
    assert alert.status == "RESOLVED"
    assert alert.resolved_by == "SYSTEM_RECONCILIATION"
  await engine.dispose()
