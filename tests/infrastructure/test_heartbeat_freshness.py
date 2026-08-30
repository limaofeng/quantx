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
from quantx_infrastructure.services.market_stream_readiness import (
  MarketStreamReadiness,
  MarketStreamReadinessStatus,
  classify_authoritative_market_stream_readiness,
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
  (AccountExecutionSafetyService._fresh,),
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


@pytest.mark.parametrize(
  ("target", "value"),
  [
    ("stream.status", "SYNCING"),
    ("stream.commit_phase", "APPLYING"),
    ("stream.sequence", 2),
    ("freshness.stream_id", "stream-other"),
    ("freshness.sequence", 2),
    ("engine.status", "SYNCING"),
    ("engine.stream_id", "stream-other"),
    ("engine.sequence", 2),
  ],
)
def test_authoritative_market_readiness_requires_exact_committed_watermarks(
  target: str,
  value: object,
) -> None:
  stream = SimpleNamespace(
    status="READY",
    commit_phase="IDLE",
    sequence=3,
    stream_id="stream-1",
  )
  freshness = SimpleNamespace(stream_id="stream-1", sequence=3)
  engine = SimpleNamespace(status="READY", stream_id="stream-1", sequence=3)
  owner, attribute = target.split(".", maxsplit=1)
  setattr(
    {"stream": stream, "freshness": freshness, "engine": engine}[owner],
    attribute,
    value,
  )
  readiness = classify_authoritative_market_stream_readiness(
    stream_state=stream,
    freshness_lease=freshness,
    engine_state=engine,
    trading_session=True,
  )

  assert readiness.status is MarketStreamReadinessStatus.FAILED
  assert not readiness.tradable_now


def test_authoritative_market_readiness_is_passed_only_during_a_fresh_session() -> None:
  stream = SimpleNamespace(
    status="READY",
    commit_phase="IDLE",
    sequence=3,
    stream_id="stream-1",
  )
  freshness = SimpleNamespace(stream_id="stream-1", sequence=3)
  engine = SimpleNamespace(status="READY", stream_id="stream-1", sequence=3)
  readiness = classify_authoritative_market_stream_readiness(
    stream_state=stream,
    freshness_lease=freshness,
    engine_state=engine,
    trading_session=True,
  )

  assert readiness.status is MarketStreamReadinessStatus.PASSED
  assert readiness.tradable_now


def test_authoritative_market_readiness_is_standby_while_market_is_closed() -> None:
  stream = SimpleNamespace(
    status="READY",
    commit_phase="IDLE",
    sequence=3,
    stream_id="stream-1",
  )
  engine = SimpleNamespace(status="READY", stream_id="stream-1", sequence=3)

  readiness = classify_authoritative_market_stream_readiness(
    stream_state=stream,
    freshness_lease=None,
    engine_state=engine,
    trading_session=False,
  )

  assert readiness.status is MarketStreamReadinessStatus.STANDBY
  assert readiness.converged
  assert not readiness.tradable_now
  assert "休市" in readiness.message


def _api(now: datetime, *, instance_id: str = "api-instance-1"):
  return SimpleNamespace(
    instance_id=instance_id,
    status="READY",
    updated_at=now,
    details={"apiInstanceId": instance_id},
  )


def test_local_agent_freshness_accepts_server_heartbeat(
  fixed_utcnow: datetime,
) -> None:
  heartbeat = _agent(fixed_utcnow)

  assert AccountExecutionSafetyService._agent_fresh(heartbeat)
  assert TradeCommandService._heartbeat_fresh(heartbeat)


def test_api_generation_is_not_an_account_health_gate(
  fixed_utcnow: datetime,
) -> None:
  heartbeat = _agent(fixed_utcnow, api_instance_id="api-instance-old")

  assert AccountExecutionSafetyService._agent_fresh(heartbeat)


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


def _agent(
  now: datetime,
  *,
  age_seconds: int = 0,
  api_instance_id: str = "api-instance-1",
):
  received_at = now - timedelta(seconds=age_seconds)
  return SimpleNamespace(
    status="READY",
    updated_at=now - timedelta(seconds=age_seconds),
    details={
      "capabilities": ["live"],
      "protocolVersion": "1.1",
      "apiInstanceId": api_instance_id,
      "agentSessionId": "agent-session-1",
      "serverReceivedAt": received_at.isoformat(),
      "agentSentAt": received_at.isoformat(),
      "sessionActive": True,
      "marketStreamStatus": "READY",
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
  *,
  market_status: MarketStreamReadinessStatus = MarketStreamReadinessStatus.PASSED,
) -> dict:
  @asynccontextmanager
  async def session():
    yield SimpleNamespace()

  normalized_rows = [row[:4] + row[5:] if len(row) == 9 else row for row in rows]
  snapshot = AsyncMock(return_value=normalized_rows)
  monkeypatch.setattr(safety_module, "AsyncSessionLocal", session)
  monkeypatch.setattr(AccountExecutionSafetyService, "_readiness_snapshot", snapshot)
  monkeypatch.setattr(
    safety_module.AccountExecutionQuarantineService,
    "list_quarantined_orders",
    AsyncMock(return_value=[]),
  )
  monkeypatch.setattr(safety_module.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    safety_module.settings,
    "real_trading_account_allowlist",
    ["TEST-ACCOUNT"],
  )
  monkeypatch.setattr(
    safety_module,
    "authoritative_market_stream_readiness",
    AsyncMock(
      return_value=MarketStreamReadiness(
        status=market_status,
        message=(
          "当前休市，权威水位已收敛"
          if market_status is MarketStreamReadinessStatus.STANDBY
          else "全市场行情未就绪"
          if market_status is MarketStreamReadinessStatus.FAILED
          else ""
        ),
        converged=market_status is not MarketStreamReadinessStatus.FAILED,
        freshness_current=market_status is MarketStreamReadinessStatus.PASSED,
        trading_session=market_status is MarketStreamReadinessStatus.PASSED,
      )
    ),
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
      _api(fixed_utcnow),
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
      _api(fixed_utcnow),
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
async def test_account_status_blocks_increase_until_market_stream_is_ready(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  agent = _agent(fixed_utcnow)
  agent.details["marketStreamStatus"] = "SYNCING"
  rows = [
    (
      _control(fixed_utcnow),
      SimpleNamespace(status="READY", updated_at=fixed_utcnow),
      _device("device-1"),
      agent,
      _api(fixed_utcnow),
      0,
      None,
      0,
      0,
    )
  ]

  result = await _status(monkeypatch, rows)
  checks = {item["code"]: item for item in result["checks"]}

  assert result["can_increase_risk"] is False
  assert checks["MARKET_STREAM_READY"]["status"] == "FAILED"
  assert "QMT Agent" in checks["MARKET_STREAM_READY"]["message"]


@pytest.mark.asyncio
async def test_account_status_rejects_agent_ready_claim_without_server_watermark(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  rows = [
    (
      _control(fixed_utcnow),
      SimpleNamespace(status="READY", updated_at=fixed_utcnow),
      _device("device-1"),
      _agent(fixed_utcnow),
      _api(fixed_utcnow),
      0,
      None,
      0,
      0,
    )
  ]

  result = await _status(
    monkeypatch,
    rows,
    market_status=MarketStreamReadinessStatus.FAILED,
  )
  checks = {item["code"]: item for item in result["checks"]}

  assert result["can_increase_risk"] is False
  assert checks["MARKET_STREAM_READY"]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_account_status_treats_closed_market_as_healthy_standby(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  rows = [
    (
      _control(fixed_utcnow),
      SimpleNamespace(status="READY", updated_at=fixed_utcnow),
      _device("device-1"),
      _agent(fixed_utcnow),
      _api(fixed_utcnow),
      0,
      None,
      0,
      0,
    )
  ]

  result = await _status(
    monkeypatch,
    rows,
    market_status=MarketStreamReadinessStatus.STANDBY,
  )
  checks = {item["code"]: item for item in result["checks"]}

  assert checks["MARKET_STREAM_READY"]["status"] == "STANDBY"
  assert result["health_status"] == "HEALTHY"
  assert result["execution_mode"] == "TRADING"
  assert result["can_increase_risk"] is True
  assert result["blocked_reasons"] == []
  assert "休市待机" in result["summary"]


@pytest.mark.asyncio
async def test_account_status_fails_closed_for_multiple_ready_live_agents(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  control = _control(fixed_utcnow)
  engine = SimpleNamespace(status="READY", updated_at=fixed_utcnow)
  rows = [
    (
      control,
      engine,
      _device("device-1"),
      _agent(fixed_utcnow),
      _api(fixed_utcnow),
      0,
      None,
      0,
      0,
    ),
    (
      control,
      engine,
      _device("device-2"),
      _agent(fixed_utcnow),
      _api(fixed_utcnow),
      0,
      None,
      0,
      0,
    ),
  ]

  result = await _status(monkeypatch, rows)
  checks = {item["code"]: item for item in result["checks"]}

  assert result["ready_live_agent_count"] == 2
  assert result["can_increase_risk"] is False
  assert checks["LIVE_AGENT_READY"]["status"] == "FAILED"
  assert "多个就绪 live QMT Agent" in checks["LIVE_AGENT_READY"]["message"]


@pytest.mark.asyncio
async def test_account_status_ignores_api_generation_after_local_heartbeat(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  rows = [
    (
      _control(fixed_utcnow),
      SimpleNamespace(status="READY", updated_at=fixed_utcnow),
      _device("device-1"),
      _agent(fixed_utcnow, api_instance_id="api-instance-old"),
      _api(fixed_utcnow, instance_id="api-instance-new"),
      0,
      None,
      0,
      0,
    )
  ]

  result = await _status(monkeypatch, rows)

  assert result["agent_status"] == "READY"
  assert result["agent_mode"] == "live"
  assert result["qmt_launch_reason_code"] == ""
  assert result["can_increase_risk"] is True


@pytest.mark.asyncio
async def test_blocked_windows_launch_overrides_persisted_account_heartbeat(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "QMT_ENROLLMENT_REQUIRED")
  rows = [
    (
      _control(fixed_utcnow),
      SimpleNamespace(status="READY", updated_at=fixed_utcnow),
      _device("device-1"),
      _agent(fixed_utcnow),
      _api(fixed_utcnow),
      0,
      None,
      0,
      0,
    )
  ]

  result = await _status(monkeypatch, rows)

  assert result["agent_status"] == "BLOCKED"
  assert result["agent_mode"] == "offline"
  assert result["qmt_launch_reason_code"] == "QMT_ENROLLMENT_REQUIRED"
  assert result["can_increase_risk"] is False
