from __future__ import annotations

import json
from datetime import timedelta
from hashlib import sha256

import pytest
from quantx_contracts import PROTOCOL_VERSION
from quantx_domain.clock import utcnow
from quantx_engine import report_processor
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentDevice,
  AgentReportInbox,
  OperationalAlert,
)
from quantx_infrastructure.models.auth import AuthUser
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

LEGACY_PROTOCOL_VERSION = "1.0"


def _snapshot(now, *, complete: bool, sequence: int, account_id="account-1"):
  payload = {
    "snapshot_id": f"snapshot-{sequence}",
    "source_sequence": sequence,
    "source_event_at": now.isoformat(),
    "mode": "live",
    "is_complete": complete,
    "accounts": [{"account_id": account_id}] if complete else [],
    "positions_by_account": {account_id: []} if complete else {},
    "orders": [],
    "trades": [],
    "unavailable_accounts": [] if complete else [account_id],
    "section_completeness_by_account": {
      account_id: {
        section: complete for section in ("account", "positions", "orders", "trades")
      }
    },
    "snapshot_authority_by_account": {
      account_id: {
        "initial_status": 0 if complete else 3,
        "final_status": 0 if complete else 3,
        "stable": True,
        "snapshot_eligible": complete,
        "status_name": "OK" if complete else "FAIL",
        "reason_code": (
          "XTTRADING_ACCOUNT_STATUS_AUTHORITATIVE"
          if complete
          else "XTTRADING_ACCOUNT_STATUS_NOT_SNAPSHOT_ELIGIBLE"
        ),
      }
    },
  }
  if complete:
    payload["snapshot_hash"] = sha256(
      json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
  return payload


@pytest.fixture
async def reports(monkeypatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    AgentDevice.__table__,
    AgentReportInbox.__table__,
    OperationalAlert.__table__,
    AccountExecutionControl.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(sync, tables=tables)
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
  now = utcnow() - timedelta(seconds=1)
  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="recovery-test",
        display_name="Recovery Test",
        password_hash="unused",
        permissions=[],
      )
    )
    for device_id in ("device-1", "device-2"):
      db.add(
        AgentDevice(
          id=device_id,
          user_id="user-1",
          name=device_id,
          secret_hash="x" * 64,
          authorized_account_ids=["account-1"],
          capabilities=["live"],
        )
      )
    db.add(
      AccountExecutionControl(
        account_id="account-1",
        authorization_state="PAUSED",
        reconcile_status="READY",
        state_version=337,
        controlled_window_active=False,
        last_snapshot_at=now,
        last_snapshot_id="snapshot-2",
      )
    )
    for message_id, complete, sequence, status, received_at in (
      ("old", False, 1, "FAILED", now - timedelta(minutes=1)),
      ("current", True, 2, "PROCESSING", now),
    ):
      db.add(
        AgentReportInbox(
          message_id=message_id,
          device_id="device-1",
          message_type="delta_report",
          protocol_version=PROTOCOL_VERSION,
          raw_payload_hash="a" * 64,
          business_idempotency_key=message_id,
          payload=_snapshot(received_at, complete=complete, sequence=sequence),
          received_at=received_at,
          processing_status=status,
          processing_attempts=1,
          processing_error="original lock timeout" if message_id == "old" else None,
        )
      )
    db.add(
      OperationalAlert(
        id="old-alert",
        fingerprint="f" * 64,
        severity="SEV2",
        source="ENGINE",
        code="AGENT_REPORT_DEAD_LETTER",
        account_id="account-1",
        business_id="old",
        message="old failure",
        details={"error_class": "DBAPIError"},
        status="OPEN",
        occurrences=1,
        first_seen_at=now - timedelta(minutes=1),
        last_seen_at=now - timedelta(minutes=1),
      )
    )
    await db.commit()
  try:
    yield sessions
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_snapshot_closes_fact_free_failure_without_restoring_authorization(
  reports,
):
  async with reports() as db:
    original = dict((await db.get(AgentReportInbox, "old")).payload)

  await report_processor._finish("current")

  async with reports() as db:
    old = await db.get(AgentReportInbox, "old")
    alert = await db.get(OperationalAlert, "old-alert")
    control = await db.get(AccountExecutionControl, "account-1")
    assert old.processing_status == "SUPERSEDED"
    assert old.payload == original
    assert old.processing_error == "original lock timeout"
    assert alert.status == "RESOLVED"
    assert alert.resolved_by == "SYSTEM_RECONCILIATION"
    assert "snapshot-2" in alert.resolution
    assert control.authorization_state == "PAUSED"
    assert control.controlled_window_active is False
    assert control.state_version == 337
    assert control.last_snapshot_id == "snapshot-2"
    resolved_at = alert.resolved_at

  await report_processor._finish("current")
  async with reports() as db:
    alert = await db.get(OperationalAlert, "old-alert")
    assert alert.resolved_at == resolved_at
    assert alert.occurrences == 1


@pytest.mark.parametrize(
  "field",
  [
    "accounts",
    "positions_by_account",
    "positions",
    "position_deltas",
    "orders",
    "trades",
    "order_errors",
    "cancel_errors",
    "unknown_business_fact",
  ],
)
@pytest.mark.asyncio
async def test_partial_broker_facts_are_never_superseded(reports, field):
  async with reports() as db:
    old = await db.get(AgentReportInbox, "old")
    value = (
      {"account-1": []}
      if field == "positions_by_account"
      else [{"account_id": "account-1"}]
    )
    old.payload = {**old.payload, field: value}
    await db.commit()

  await report_processor._finish("current")

  async with reports() as db:
    assert (await db.get(AgentReportInbox, "old")).processing_status == "FAILED"
    assert (await db.get(OperationalAlert, "old-alert")).status == "OPEN"


@pytest.mark.parametrize(
  "case",
  [
    "missing_authority",
    "eligible_authority",
    "partial_sections",
    "missing_time",
    "equal_time",
    "future_time",
    "newer_sequence",
    "different_account",
    "different_device",
    "old_protocol",
    "newer_received",
    "incremental_report",
    "order_report",
    "invalid_current_hash",
    "incomplete_current",
  ],
)
@pytest.mark.asyncio
async def test_recovery_requires_matching_scope_and_newer_authoritative_evidence(
  reports, case
):
  async with reports() as db:
    old = await db.get(AgentReportInbox, "old")
    current = await db.get(AgentReportInbox, "current")
    payload = dict(old.payload)
    if case == "missing_authority":
      payload["snapshot_authority_by_account"] = {}
    elif case == "eligible_authority":
      payload["snapshot_authority_by_account"]["account-1"]["snapshot_eligible"] = True
    elif case == "partial_sections":
      payload["section_completeness_by_account"]["account-1"]["orders"] = True
    elif case == "missing_time":
      payload.pop("source_event_at")
    elif case == "equal_time":
      payload["source_event_at"] = current.payload["source_event_at"]
    elif case == "future_time":
      payload["source_event_at"] = (utcnow() + timedelta(hours=1)).isoformat()
    elif case == "newer_sequence":
      payload["source_sequence"] = 3
    elif case == "different_account":
      payload = _snapshot(
        old.received_at, complete=False, sequence=1, account_id="account-2"
      )
    elif case == "different_device":
      old.device_id = "device-2"
    elif case == "old_protocol":
      old.protocol_version = LEGACY_PROTOCOL_VERSION
    elif case == "newer_received":
      old.received_at = current.received_at + timedelta(seconds=1)
    elif case == "incremental_report":
      payload = {"account_id": "account-1", "position_deltas": []}
    elif case == "order_report":
      old.message_type = "order_report"
    elif case == "invalid_current_hash":
      current.payload = {**current.payload, "snapshot_hash": "b" * 64}
    elif case == "incomplete_current":
      current.payload = _snapshot(current.received_at, complete=False, sequence=2)
    old.payload = payload
    # Ensure nested JSON mutations are persisted, not just changed in memory.
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(old, "payload")
    await db.commit()

  await report_processor._finish("current")

  async with reports() as db:
    assert (await db.get(AgentReportInbox, "old")).processing_status == "FAILED"
    assert (await db.get(OperationalAlert, "old-alert")).status == "OPEN"


class _PostgresError(Exception):
  def __init__(self, sqlstate):
    self.sqlstate = sqlstate
    super().__init__("private-query-parameter")


@pytest.mark.parametrize(
  ("sqlstate", "attempts", "expected"),
  [
    ("55P03", 1, "PENDING"),
    ("40P01", 1, "PENDING"),
    ("40001", 1, "PENDING"),
    ("55P03", 10, "FAILED"),
    ("23505", 1, "FAILED"),
    (None, 1, "FAILED"),
  ],
)
@pytest.mark.asyncio
async def test_database_failures_retry_only_transient_sqlstates_and_keep_attempt_limit(
  reports,
  sqlstate,
  attempts,
  expected,
):
  async with reports() as db:
    current = await db.get(AgentReportInbox, "current")
    current.processing_attempts = attempts
    await db.commit()
  error = DBAPIError(
    "UPDATE private_table",
    {"secret": "private-query-parameter"},
    _PostgresError(sqlstate),
  )

  await report_processor._finish("current", error=error)

  async with reports() as db:
    current = await db.get(AgentReportInbox, "current")
    assert current.processing_status == expected
    assert "private-query-parameter" not in current.processing_error
    assert "UPDATE" not in current.processing_error
    alerts = (
      await db.scalars(
        select(OperationalAlert).where(OperationalAlert.business_id == "current")
      )
    ).all()
    if expected == "PENDING":
      assert current.next_attempt_at > utcnow()
      assert alerts == []
    else:
      assert len(alerts) == 1
      assert alerts[0].status == "OPEN"
      assert "private-query-parameter" not in json.dumps(alerts[0].details)
