from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
import quantx_infrastructure.services.t_trade_operations_service as operations_module
import quantx_infrastructure.services.trade_command_service as trade_command_module
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService

FRESHNESS_CHECKS: tuple[Callable[[object], bool], ...] = (
  TTradeOperationsService._fresh,
  TTradeOperationsService._agent_fresh,
  TradeCommandService._heartbeat_fresh,
)
CURRENT_LAUNCH_FRESHNESS_CHECKS: tuple[Callable[[object], bool], ...] = (
  TTradeOperationsService._agent_fresh,
  TradeCommandService._heartbeat_fresh,
)


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
  monkeypatch.setattr(operations_module, "utcnow", lambda: value)
  monkeypatch.setattr(trade_command_module, "utcnow", lambda: value)
  return value


@pytest.mark.parametrize("freshness_check", FRESHNESS_CHECKS)
def test_heartbeat_freshness_accepts_naive_utc(
  freshness_check: Callable[[object], bool],
  fixed_utcnow: datetime,
) -> None:
  heartbeat = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow - timedelta(seconds=30),
  )

  assert freshness_check(heartbeat)


@pytest.mark.parametrize("freshness_check", FRESHNESS_CHECKS)
def test_heartbeat_freshness_normalizes_aware_datetime(
  freshness_check: Callable[[object], bool],
  fixed_utcnow: datetime,
) -> None:
  heartbeat = SimpleNamespace(
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

  assert freshness_check(heartbeat)


@pytest.mark.parametrize("freshness_check", FRESHNESS_CHECKS)
def test_heartbeat_freshness_rejects_stale_timestamp(
  freshness_check: Callable[[object], bool],
  fixed_utcnow: datetime,
) -> None:
  heartbeat = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow - timedelta(seconds=91),
  )

  assert not freshness_check(heartbeat)


@pytest.mark.parametrize(
  "freshness_check",
  CURRENT_LAUNCH_FRESHNESS_CHECKS,
)
def test_heartbeat_freshness_requires_current_managed_launch(
  freshness_check: Callable[[object], bool],
  fixed_utcnow: datetime,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  launch_started_at = fixed_utcnow - timedelta(seconds=20)
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv(
    "QMT_AGENT_LAUNCH_STARTED_AT",
    launch_started_at.replace(tzinfo=timezone.utc).isoformat(),
  )

  prior_heartbeat = SimpleNamespace(
    status="READY",
    updated_at=launch_started_at - timedelta(seconds=1),
  )
  current_heartbeat = SimpleNamespace(
    status="READY",
    updated_at=launch_started_at + timedelta(seconds=1),
  )

  assert not freshness_check(prior_heartbeat)
  assert freshness_check(current_heartbeat)


@pytest.mark.parametrize(
  "freshness_check",
  CURRENT_LAUNCH_FRESHNESS_CHECKS,
)
def test_current_launch_freshness_fails_closed_for_invalid_boundary(
  freshness_check: Callable[[object], bool],
  fixed_utcnow: datetime,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STARTED_AT", "not-a-timestamp")
  heartbeat = SimpleNamespace(status="READY", updated_at=fixed_utcnow)

  assert not freshness_check(heartbeat)


def test_operations_freshness_requires_ready_status(
  fixed_utcnow: datetime,
) -> None:
  heartbeat = SimpleNamespace(
    status="DEGRADED",
    updated_at=fixed_utcnow,
  )

  assert not TTradeOperationsService._fresh(heartbeat)


def test_blocked_qmt_launch_reason_is_stable_and_sanitized(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "do-not-expose-this")

  assert (
    operations_module.qmt_agent_launch_block_reason()
    == "QMT_LAUNCH_BLOCKED"
  )


@pytest.mark.asyncio
async def test_readiness_uses_one_database_round_trip(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  snapshot_result = MagicMock()
  snapshot_result.all.return_value = [
    (None, None, None, None, 0, None, 0, 0)
  ]
  db = SimpleNamespace(
    execute=AsyncMock(return_value=snapshot_result)
  )

  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    SessionContext,
  )

  result = await TTradeOperationsService().readiness("TEST-ACCOUNT")

  assert db.execute.await_count == 1
  assert len(result["checks"]) == 17
  assert result["ready"] is False


@pytest.mark.asyncio
async def test_readiness_distinguishes_preparation_from_automation(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  rollout = SimpleNamespace(
    account_id="TEST-ACCOUNT",
    stage="SHADOW",
    enabled=False,
    kill_switch=False,
    reconcile_status="READY",
    policy_version=1,
    last_snapshot_id="snapshot-1",
    last_snapshot_hash="a" * 64,
    last_snapshot_at=fixed_utcnow - timedelta(seconds=10),
    last_backup_at=None,
  )
  engine = SimpleNamespace(status="READY", updated_at=fixed_utcnow)
  device = SimpleNamespace(
    id="device-1",
    authorized_account_ids=["TEST-ACCOUNT"],
    capabilities=["live"],
  )
  agent = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow,
    details={
      "capabilities": ["live"],
      "protocolVersion": "1.1",
      "accountReconciliation": {
        "TEST-ACCOUNT": {
          "snapshotId": "snapshot-1",
          "manualCoexistence": True,
          "externalOrderCount": 2,
          "externalTradeCount": 1,
        }
      },
    },
  )
  snapshot_result = MagicMock()
  snapshot_result.all.return_value = [
    (rollout, engine, device, agent, 0, None, 0, 0)
  ]
  db = SimpleNamespace(execute=AsyncMock(return_value=snapshot_result))

  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  monkeypatch.setattr(operations_module, "AsyncSessionLocal", SessionContext)
  monkeypatch.setattr(
    operations_module.settings,
    "real_trading_account_allowlist",
    ["TEST-ACCOUNT"],
  )
  monkeypatch.setattr(operations_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(operations_module.settings, "t_trade_live_enabled", True)

  result = await TTradeOperationsService().readiness("TEST-ACCOUNT")

  assert result["status"] == "PREPARING"
  assert result["preparation_ready"] is True
  assert result["automation_ready"] is False
  assert result["ready"] is False
  assert result["manual_coexistence"] is True
  assert result["external_order_count"] == 2
  assert result["external_trade_count"] == 1
  assert result["preparation_blocked_reasons"] == []
  assert "最近成功备份缺失或已超过 24 小时" in result["blocked_reasons"]
  assert "尚未基于最新完整快照建立受控交易窗口" in result["blocked_reasons"]
  assert any("QMT 手工/外部交易" in item for item in result["blocked_reasons"])

  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "QMT_ENROLLMENT_REQUIRED")
  blocked = await TTradeOperationsService().readiness("TEST-ACCOUNT")
  checks = {item["code"]: item for item in blocked["checks"]}

  assert checks["ENGINE_READY"]["passed"] is True
  assert checks["LIVE_AGENT_READY"]["passed"] is False


@pytest.mark.asyncio
async def test_readiness_prefers_fresh_ready_agent_for_the_account(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  rollout = SimpleNamespace(
    account_id="TEST-ACCOUNT",
    stage="SHADOW",
    enabled=False,
    kill_switch=False,
    reconcile_status="READY",
    policy_version=1,
    last_snapshot_id="snapshot-1",
    last_snapshot_hash="a" * 64,
    last_snapshot_at=fixed_utcnow - timedelta(seconds=10),
    last_backup_at=fixed_utcnow - timedelta(hours=1),
  )
  engine = SimpleNamespace(status="READY", updated_at=fixed_utcnow)
  stale_device = SimpleNamespace(
    id="device-stale",
    authorized_account_ids=["TEST-ACCOUNT"],
    capabilities=["live"],
  )
  fresh_device = SimpleNamespace(
    id="device-fresh",
    authorized_account_ids=["TEST-ACCOUNT"],
    capabilities=["live"],
  )
  stale_agent = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow - timedelta(hours=3),
    details={"capabilities": ["live"], "protocolVersion": "1.1"},
  )
  fresh_agent = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow,
    details={
      "capabilities": ["live"],
      "protocolVersion": "1.1",
      "accountReconciliation": {
        "TEST-ACCOUNT": {
          "snapshotId": "snapshot-1",
          "manualCoexistence": True,
          "externalOrderCount": 0,
          "externalTradeCount": 0,
        }
      },
    },
  )
  snapshot_result = MagicMock()
  snapshot_result.all.return_value = [
    (rollout, engine, stale_device, stale_agent, 0, None, 0, 0),
    (rollout, engine, fresh_device, fresh_agent, 0, None, 0, 0),
  ]
  db = SimpleNamespace(execute=AsyncMock(return_value=snapshot_result))

  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  monkeypatch.setattr(operations_module, "AsyncSessionLocal", SessionContext)
  monkeypatch.setattr(
    operations_module.settings,
    "real_trading_account_allowlist",
    ["TEST-ACCOUNT"],
  )
  monkeypatch.setattr(operations_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(operations_module.settings, "t_trade_live_enabled", True)

  result = await TTradeOperationsService().readiness("TEST-ACCOUNT")

  assert result["agent_device_id"] == "device-fresh"
  assert result["ready_live_agent_count"] == 1
  assert result["agent_status"] == "READY"
  assert result["agent_mode"] == "live"
  assert result["protocol_version"] == "1.1"
  assert next(
    item for item in result["checks"] if item["code"] == "LIVE_AGENT_READY"
  )["passed"] is True

  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv(
    "QMT_AGENT_LAUNCH_STARTED_AT",
    (fixed_utcnow + timedelta(seconds=1))
    .replace(tzinfo=timezone.utc)
    .isoformat(),
  )

  prior_launch = await TTradeOperationsService().readiness("TEST-ACCOUNT")

  assert prior_launch["agent_status"] == "OFFLINE"
  assert prior_launch["ready_live_agent_count"] == 0
  assert prior_launch["can_activate_live"] is False
  assert next(
    item
    for item in prior_launch["checks"]
    if item["code"] == "LIVE_AGENT_READY"
  )["passed"] is False

  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "QMT_ENROLLMENT_REQUIRED")
  monkeypatch.setenv("QMT_AGENT_MODE", "live")

  blocked = await TTradeOperationsService().readiness("TEST-ACCOUNT")

  assert blocked["status"] == "BLOCKED"
  assert blocked["ready"] is False
  assert blocked["preparation_ready"] is False
  assert blocked["automation_ready"] is False
  assert blocked["can_activate_live"] is False
  assert blocked["can_approve"] is False
  assert blocked["agent_status"] == "BLOCKED"
  assert blocked["agent_mode"] == "offline"
  assert blocked["requested_agent_mode"] == "live"
  assert blocked["ready_live_agent_count"] == 0
  assert blocked["protocol_version"] == ""
  assert blocked["qmt_launch_state"] == "BLOCKED"
  assert (
    blocked["qmt_launch_reason_code"] == "QMT_ENROLLMENT_REQUIRED"
  )
  live_agent_check = next(
    item
    for item in blocked["checks"]
    if item["code"] == "LIVE_AGENT_READY"
  )
  assert live_agent_check["passed"] is False
  assert "QMT_ENROLLMENT_REQUIRED" in live_agent_check["message"]
  assert next(
    item for item in blocked["checks"] if item["code"] == "AGENT_MODE_LIVE"
  )["passed"] is False


@pytest.mark.asyncio
async def test_readiness_fails_closed_for_multiple_ready_live_agents(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  rollout = SimpleNamespace(
    account_id="TEST-ACCOUNT",
    stage="CANARY",
    enabled=True,
    kill_switch=False,
    reconcile_status="READY",
    policy_version=1,
    last_snapshot_id="snapshot-1",
    last_snapshot_hash="a" * 64,
    last_snapshot_at=fixed_utcnow - timedelta(seconds=10),
    last_backup_at=fixed_utcnow - timedelta(hours=1),
  )
  engine = SimpleNamespace(status="READY", updated_at=fixed_utcnow)

  def device(value: str):
    return SimpleNamespace(
      id=value,
      authorized_account_ids=["TEST-ACCOUNT"],
      capabilities=["live"],
    )

  def agent():
    return SimpleNamespace(
      status="READY",
      updated_at=fixed_utcnow,
      details={
        "capabilities": ["live"],
        "protocolVersion": "1.1",
        "accountReconciliation": {
          "TEST-ACCOUNT": {
            "snapshotId": "snapshot-1",
            "manualCoexistence": True,
            "externalOrderCount": 0,
            "externalTradeCount": 0,
          }
        },
      },
    )

  snapshot_result = MagicMock()
  snapshot_result.all.return_value = [
    (rollout, engine, device("device-1"), agent(), 0, None, 0, 0),
    (rollout, engine, device("device-2"), agent(), 0, None, 0, 0),
  ]
  db = SimpleNamespace(execute=AsyncMock(return_value=snapshot_result))

  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  monkeypatch.setattr(operations_module, "AsyncSessionLocal", SessionContext)
  monkeypatch.setattr(
    operations_module.settings,
    "real_trading_account_allowlist",
    ["TEST-ACCOUNT"],
  )
  monkeypatch.setattr(operations_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(operations_module.settings, "t_trade_live_enabled", True)

  result = await TTradeOperationsService().readiness("TEST-ACCOUNT")

  assert result["ready_live_agent_count"] == 2
  live_agent_check = next(
    item for item in result["checks"] if item["code"] == "LIVE_AGENT_READY"
  )
  assert live_agent_check["passed"] is False
  assert "多个就绪 live QMT Agent" in live_agent_check["message"]


@pytest.mark.asyncio
async def test_readiness_reports_stale_agent_as_offline(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  device = SimpleNamespace(
    id="device-stale",
    authorized_account_ids=["TEST-ACCOUNT"],
    capabilities=["live"],
  )
  agent = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow - timedelta(hours=3),
    details={"capabilities": ["live"], "protocolVersion": "1.1"},
  )
  snapshot_result = MagicMock()
  snapshot_result.all.return_value = [
    (None, None, device, agent, 0, None, 0, 0)
  ]
  db = SimpleNamespace(execute=AsyncMock(return_value=snapshot_result))

  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  monkeypatch.setattr(operations_module, "AsyncSessionLocal", SessionContext)

  result = await TTradeOperationsService().readiness("TEST-ACCOUNT")

  assert result["agent_status"] == "OFFLINE"
  assert result["agent_mode"] == "offline"
  assert result["protocol_version"] == ""
  assert any(
    "心跳超过 90 秒" in reason
    for reason in result["preparation_blocked_reasons"]
  )


@pytest.mark.asyncio
async def test_readiness_recovers_after_agent_reconnect(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  rollout = SimpleNamespace(
    account_id="TEST-ACCOUNT",
    stage="SHADOW",
    enabled=False,
    kill_switch=False,
    reconcile_status="READY",
    policy_version=1,
    last_snapshot_id="snapshot-1",
    last_snapshot_hash="a" * 64,
    last_snapshot_at=fixed_utcnow - timedelta(seconds=10),
    last_backup_at=fixed_utcnow - timedelta(hours=1),
  )
  engine = SimpleNamespace(status="READY", updated_at=fixed_utcnow)
  device = SimpleNamespace(
    id="device-1",
    authorized_account_ids=["TEST-ACCOUNT"],
    capabilities=["live"],
  )
  agent = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow - timedelta(seconds=91),
    details={
      "capabilities": ["live"],
      "protocolVersion": "1.1",
      "accountReconciliation": {
        "TEST-ACCOUNT": {
          "snapshotId": "snapshot-1",
          "manualCoexistence": True,
          "externalOrderCount": 0,
          "externalTradeCount": 0,
        }
      },
    },
  )
  snapshot_result = MagicMock()
  snapshot_result.all.return_value = [
    (rollout, engine, device, agent, 0, None, 0, 0)
  ]
  db = SimpleNamespace(execute=AsyncMock(return_value=snapshot_result))

  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  monkeypatch.setattr(operations_module, "AsyncSessionLocal", SessionContext)

  disconnected = await TTradeOperationsService().readiness("TEST-ACCOUNT")
  assert disconnected["agent_status"] == "OFFLINE"
  assert disconnected["preparation_ready"] is False

  agent.updated_at = fixed_utcnow
  reconnected = await TTradeOperationsService().readiness("TEST-ACCOUNT")

  assert reconnected["agent_status"] == "READY"
  assert reconnected["agent_mode"] == "live"
  assert reconnected["protocol_version"] == "1.1"
  assert reconnected["preparation_ready"] is True
  assert reconnected["status"] == "PREPARING"
