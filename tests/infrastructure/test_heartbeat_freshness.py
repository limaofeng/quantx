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


def test_authoritative_market_readiness_marks_recent_engine_progress_as_transient() -> (
  None
):
  observed_at = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)
  stream = SimpleNamespace(
    status="READY",
    commit_phase="IDLE",
    generation=7,
    sequence=125,
    stream_id="stream-1",
    updated_at=observed_at - timedelta(milliseconds=50),
  )
  freshness = SimpleNamespace(stream_id="stream-1", sequence=125)
  engine = SimpleNamespace(
    status="READY",
    generation=7,
    sequence=119,
    stream_id="stream-1",
    updated_at=observed_at - timedelta(milliseconds=1500),
  )

  readiness = classify_authoritative_market_stream_readiness(
    stream_state=stream,
    freshness_lease=freshness,
    engine_state=engine,
    trading_session=True,
    observed_at=observed_at,
  )

  assert readiness.status is MarketStreamReadinessStatus.TRANSIENT
  assert readiness.sequence_lag == 6
  assert readiness.progress_age_seconds == pytest.approx(1.5)
  assert not readiness.tradable_now
  assert "落后 6 批" in readiness.message


def test_authoritative_market_readiness_fails_when_engine_progress_stalls() -> None:
  observed_at = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)
  stream = SimpleNamespace(
    status="READY",
    commit_phase="IDLE",
    generation=7,
    sequence=125,
    stream_id="stream-1",
    updated_at=observed_at,
  )
  freshness = SimpleNamespace(stream_id="stream-1", sequence=125)
  engine = SimpleNamespace(
    status="READY",
    generation=7,
    sequence=119,
    stream_id="stream-1",
    updated_at=observed_at - timedelta(seconds=4),
  )

  readiness = classify_authoritative_market_stream_readiness(
    stream_state=stream,
    freshness_lease=freshness,
    engine_state=engine,
    trading_session=True,
    observed_at=observed_at,
  )

  assert readiness.status is MarketStreamReadinessStatus.FAILED
  assert not readiness.tradable_now
  assert "长时间未收敛" in readiness.message


def test_authoritative_market_readiness_marks_short_atomic_commit_as_transient() -> (
  None
):
  observed_at = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)
  stream = SimpleNamespace(
    status="READY",
    commit_phase="APPLYING",
    pending_sequence=126,
    generation=7,
    sequence=125,
    stream_id="stream-1",
    updated_at=observed_at - timedelta(milliseconds=20),
  )
  freshness = SimpleNamespace(stream_id="stream-1", sequence=125)
  engine = SimpleNamespace(
    status="READY",
    generation=7,
    sequence=125,
    stream_id="stream-1",
    updated_at=observed_at - timedelta(milliseconds=50),
  )

  readiness = classify_authoritative_market_stream_readiness(
    stream_state=stream,
    freshness_lease=freshness,
    engine_state=engine,
    trading_session=True,
    observed_at=observed_at,
  )

  assert readiness.status is MarketStreamReadinessStatus.TRANSIENT
  assert not readiness.tradable_now
  assert "原子提交" in readiness.message


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


def _position_snapshot(control, *, complete: bool = True, error: str | None = None):
  return SimpleNamespace(
    sequence=1,
    reported_at=control.last_snapshot_at,
    received_at=control.last_snapshot_at,
    position_count=1,
    is_complete=complete,
    last_error=error,
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

  normalized_rows = []
  for row in rows:
    normalized = row[:4] + row[5:] if len(row) in {9, 10} else row
    if len(normalized) == 8:
      normalized = (*normalized, _position_snapshot(normalized[0]))
    normalized_rows.append(normalized)
  snapshot = AsyncMock(return_value=normalized_rows)
  monkeypatch.setattr(safety_module, "AsyncSessionLocal", session)
  monkeypatch.setattr(AccountExecutionSafetyService, "_readiness_snapshot", snapshot)
  details = AsyncMock(return_value=[])
  monkeypatch.setattr(
    safety_module.AccountExecutionQuarantineService,
    "list_quarantined_orders",
    details,
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
          else "Engine 正在追赶全市场行情水位"
          if market_status is MarketStreamReadinessStatus.TRANSIENT
          else "全市场行情未就绪"
          if market_status is MarketStreamReadinessStatus.FAILED
          else ""
        ),
        converged=market_status
        in {
          MarketStreamReadinessStatus.PASSED,
          MarketStreamReadinessStatus.STANDBY,
        },
        freshness_current=market_status is MarketStreamReadinessStatus.PASSED,
        trading_session=market_status is MarketStreamReadinessStatus.PASSED,
      )
    ),
  )
  result = await AccountExecutionSafetyService().status("TEST-ACCOUNT")
  snapshot.assert_awaited_once()
  details.assert_awaited_once()
  details.reset_mock()
  checks = await AccountExecutionSafetyService().checks("TEST-ACCOUNT")
  assert checks == result["checks"]
  assert snapshot.await_count == 2
  details.assert_not_awaited()
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


@pytest.mark.parametrize(
  ("window_active", "working_orders", "expected_message"),
  [
    (False, 0, "活动委托 0 笔；需建立实盘窗口确认这些历史交易"),
    (True, 0, "实盘窗口后新增手工/外部委托 1 笔、成交 1 笔"),
    (False, 2, "仍有 2 笔 QMT 手工/外部活动委托"),
  ],
)
@pytest.mark.asyncio
async def test_external_activity_explains_confirmation_without_relaxing_gates(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
  window_active: bool,
  working_orders: int,
  expected_message: str,
) -> None:
  control = _control(fixed_utcnow)
  control.authorization_state = "PAUSED"
  control.controlled_window_active = window_active
  control.controlled_window_snapshot_id = "earlier-snapshot" if window_active else None
  agent = _agent(fixed_utcnow)
  agent.details["accountReconciliation"]["TEST-ACCOUNT"].update(
    externalOrderCount=1,
    externalTradeCount=1,
    newExternalOrderCount=1,
    newExternalTradeCount=1,
    workingExternalOrderCount=working_orders,
  )

  result = await _status(
    monkeypatch,
    [
      (
        control,
        SimpleNamespace(status="READY", updated_at=fixed_utcnow),
        _device("device-1"),
        agent,
        0,
        None,
        0,
        0,
      )
    ],
  )

  checks = {item["code"]: item for item in result["checks"]}
  assert checks["NO_EXTERNAL_BROKER_ACTIVITY"]["status"] == "FAILED"
  assert expected_message in checks["NO_EXTERNAL_BROKER_ACTIVITY"]["message"]
  assert "需人工启用" in checks["ACCOUNT_RISK_INCREASE_AUTHORIZED"]["message"]
  assert result["authorization_state"] == "PAUSED"
  assert result["can_increase_risk"] is False
  assert result["can_activate_automation"] is False
  assert result["can_reduce_risk"] is True
  assert control.controlled_window_active is window_active
  assert control.state_version == 4


@pytest.mark.asyncio
async def test_incomplete_marker_never_refreshes_snapshot_freshness(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  control = _control(fixed_utcnow)
  rows = [
    (
      control,
      SimpleNamespace(status="READY", updated_at=fixed_utcnow),
      _device("device-1"),
      _agent(fixed_utcnow),
      _api(fixed_utcnow),
      0,
      None,
      0,
      0,
      _position_snapshot(
        control,
        complete=False,
        error="SNAPSHOT_ACCOUNT_STATUS_INVALID:ACCOUNT_STATUS_FAIL",
      ),
    )
  ]

  result = await _status(monkeypatch, rows)
  checks = {item["code"]: item for item in result["checks"]}

  assert result["reconciliation_age_seconds"] == pytest.approx(10)
  assert checks["SNAPSHOT_RECONCILED"]["status"] == "FAILED"
  assert checks["SNAPSHOT_FRESH"]["status"] == "FAILED"
  assert "ACCOUNT_STATUS_FAIL" in checks["SNAPSHOT_FRESH"]["message"]
  assert result["can_increase_risk"] is False


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
  assert checks["MARKET_STREAM_READY"]["status"] == "TRANSIENT"
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
async def test_account_status_keeps_transient_market_catchup_fail_closed(
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
    market_status=MarketStreamReadinessStatus.TRANSIENT,
  )
  checks = {item["code"]: item for item in result["checks"]}

  assert checks["MARKET_STREAM_READY"]["status"] == "TRANSIENT"
  assert result["can_increase_risk"] is False
  assert result["can_reduce_risk"] is True
  assert "追赶" in result["blocked_reasons"][0]


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
async def test_account_status_keeps_market_standby_when_trading_is_unavailable(
  monkeypatch: pytest.MonkeyPatch,
  fixed_utcnow: datetime,
) -> None:
  agent = _agent(fixed_utcnow)
  agent.status = "TRADING_UNAVAILABLE"
  agent.details["xttradingStatus"] = "DISCONNECTED"
  agent.details["xttradingReason"] = "XTTRADING_UNAVAILABLE"
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

  result = await _status(
    monkeypatch,
    rows,
    market_status=MarketStreamReadinessStatus.STANDBY,
  )
  checks = {item["code"]: item for item in result["checks"]}

  assert result["agent_status"] == "TRADING_UNAVAILABLE"
  assert result["agent_mode"] == "live"
  assert result["protocol_version"] == "1.1"
  assert result["qmt_launch_reason_code"] == "XTTRADING_UNAVAILABLE"
  assert checks["LIVE_AGENT_READY"]["status"] == "FAILED"
  assert "XTTRADING_UNAVAILABLE" in checks["LIVE_AGENT_READY"]["message"]
  assert checks["AGENT_MODE_LIVE"]["status"] == "PASSED"
  assert checks["PROTOCOL_1_1"]["status"] == "PASSED"
  assert checks["MARKET_STREAM_READY"]["status"] == "STANDBY"
  assert checks["SNAPSHOT_FRESH"]["status"] == "FAILED"
  assert result["can_increase_risk"] is False


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
