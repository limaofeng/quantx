from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_infrastructure.services.account_execution_safety_service as safety_module
import quantx_infrastructure.services.trade_command_service as trade_command_module
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService


@pytest.fixture(autouse=True)
def clear_qmt_launch_guard(monkeypatch: pytest.MonkeyPatch) -> None:
  for name in (
    "QMT_AGENT_LAUNCH_STATE",
    "QMT_AGENT_LAUNCH_REASON",
    "QMT_AGENT_LAUNCH_STARTED_AT",
  ):
    monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fixed_utcnow(monkeypatch: pytest.MonkeyPatch) -> datetime:
  value = datetime(2026, 7, 28, 10, 30)
  monkeypatch.setattr(safety_module, "utcnow", lambda: value)
  monkeypatch.setattr(trade_command_module, "utcnow", lambda: value)
  return value


@pytest.mark.parametrize(
  "freshness_check",
  (
    AccountExecutionSafetyService._fresh,
    AccountExecutionSafetyService._agent_fresh,
    TradeCommandService._heartbeat_fresh,
  ),
)
def test_heartbeat_freshness_accepts_naive_and_aware_utc(
  freshness_check,
  fixed_utcnow: datetime,
) -> None:
  naive = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow - timedelta(seconds=30),
  )
  aware = SimpleNamespace(
    status="READY",
    updated_at=datetime(
      2026,
      7,
      28,
      18,
      29,
      30,
      tzinfo=timezone(timedelta(hours=8)),
    ),
  )

  assert freshness_check(naive)
  assert freshness_check(aware)


def test_account_freshness_rejects_stale_or_degraded_heartbeat(
  fixed_utcnow: datetime,
) -> None:
  stale = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow - timedelta(seconds=91),
  )
  degraded = SimpleNamespace(status="DEGRADED", updated_at=fixed_utcnow)

  assert not AccountExecutionSafetyService._fresh(stale)
  assert not AccountExecutionSafetyService._fresh(degraded)


def test_current_launch_boundary_fails_closed(
  fixed_utcnow: datetime,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  heartbeat = SimpleNamespace(status="READY", updated_at=fixed_utcnow)
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STARTED_AT", "not-a-timestamp")

  assert not AccountExecutionSafetyService._agent_fresh(heartbeat)


def _control(now: datetime):
  return SimpleNamespace(
    account_id="TEST-ACCOUNT",
    authorization_state="ENABLED",
    state_version=4,
    reconcile_status="READY",
    last_snapshot_id="snapshot-1",
    last_snapshot_hash="a" * 64,
    last_snapshot_at=now - timedelta(seconds=10),
    last_backup_at=now - timedelta(hours=1),
    controlled_window_active=True,
    controlled_window_snapshot_id="snapshot-1",
    controlled_window_started_at=now - timedelta(minutes=1),
  )


def _device(value: str):
  return SimpleNamespace(
    id=value,
    authorized_account_ids=["TEST-ACCOUNT"],
    capabilities=["live"],
  )


def _agent(now: datetime, *, age_seconds: int = 0):
  return SimpleNamespace(
    status="READY",
    updated_at=now - timedelta(seconds=age_seconds),
    details={
      "capabilities": ["live"],
      "protocolVersion": "1.1",
      "accountReconciliation": {
        "TEST-ACCOUNT": {
          "snapshotId": "snapshot-1",
          "manualCoexistence": False,
          "externalOrderCount": 0,
          "externalTradeCount": 0,
          "newExternalOrderCount": 0,
          "newExternalTradeCount": 0,
          "workingExternalOrderCount": 0,
        }
      },
    },
  )


async def _status(
  monkeypatch: pytest.MonkeyPatch,
  rows: list[tuple],
) -> dict:
  @asynccontextmanager
  async def session():
    yield SimpleNamespace()

  snapshot = AsyncMock(return_value=rows)
  monkeypatch.setattr(safety_module, "AsyncSessionLocal", session)
  monkeypatch.setattr(AccountExecutionSafetyService, "_readiness_snapshot", snapshot)
  monkeypatch.setattr(safety_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    safety_module.settings,
    "real_trading_account_allowlist",
    ["TEST-ACCOUNT"],
  )
  result = await AccountExecutionSafetyService().status("TEST-ACCOUNT")
  snapshot.assert_awaited_once()
  return result


@pytest.mark.asyncio
async def test_account_status_prefers_the_single_fresh_live_agent(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  control = _control(fixed_utcnow)
  engine = SimpleNamespace(status="READY", updated_at=fixed_utcnow)
  rows = [
    (
      control,
      engine,
      _device("device-stale"),
      _agent(fixed_utcnow, age_seconds=180),
      0,
      None,
      0,
      0,
    ),
    (
      control,
      engine,
      _device("device-fresh"),
      _agent(fixed_utcnow),
      0,
      None,
      0,
      0,
    ),
  ]

  result = await _status(monkeypatch, rows)

  assert result["agent_device_id"] == "device-fresh"
  assert result["ready_live_agent_count"] == 1
  assert result["agent_status"] == "READY"
  assert result["can_increase_risk"] is True


@pytest.mark.asyncio
async def test_account_status_fails_closed_for_multiple_ready_live_agents(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  control = _control(fixed_utcnow)
  engine = SimpleNamespace(status="READY", updated_at=fixed_utcnow)
  rows = [
    (control, engine, _device("device-1"), _agent(fixed_utcnow), 0, None, 0, 0),
    (control, engine, _device("device-2"), _agent(fixed_utcnow), 0, None, 0, 0),
  ]

  result = await _status(monkeypatch, rows)
  checks = {item["code"]: item for item in result["checks"]}

  assert result["ready_live_agent_count"] == 2
  assert result["can_increase_risk"] is False
  assert checks["LIVE_AGENT_READY"]["passed"] is False
  assert "多个就绪 live QMT Agent" in checks["LIVE_AGENT_READY"]["message"]


@pytest.mark.asyncio
async def test_blocked_launch_overrides_a_fresh_persisted_heartbeat(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "QMT_ENROLLMENT_REQUIRED")
  monkeypatch.setenv("QMT_AGENT_MODE", "live")
  rows = [
    (
      _control(fixed_utcnow),
      SimpleNamespace(status="READY", updated_at=fixed_utcnow),
      _device("device-1"),
      _agent(fixed_utcnow),
      0,
      None,
      0,
      0,
    )
  ]

  result = await _status(monkeypatch, rows)

  assert result["agent_status"] == "BLOCKED"
  assert result["agent_mode"] == "offline"
  assert result["requested_agent_mode"] == "live"
  assert result["qmt_launch_reason_code"] == "QMT_ENROLLMENT_REQUIRED"
  assert result["can_increase_risk"] is False
