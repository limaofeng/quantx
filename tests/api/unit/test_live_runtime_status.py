from datetime import datetime, timedelta, timezone

import pytest
from quantx_api import live_runtime_status


def _configure_live(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(live_runtime_status.settings, "runtime_profile", "full")
  monkeypatch.setattr(live_runtime_status.settings, "enable_real_trading", True)
  monkeypatch.setattr(
    live_runtime_status.settings,
    "real_trading_account_allowlist",
    ["account-1"],
  )


@pytest.mark.asyncio
async def test_live_status_is_disabled_when_runtime_is_not_configured(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(live_runtime_status.settings, "runtime_profile", "web")
  monkeypatch.setattr(live_runtime_status.settings, "enable_real_trading", False)
  monkeypatch.setattr(
    live_runtime_status.settings,
    "real_trading_account_allowlist",
    [],
  )

  result = await live_runtime_status.live_trading_runtime_status()

  assert result == {
    "status": "DISABLED",
    "configuredLive": False,
    "accountId": None,
    "executionMode": "OBSERVE_ONLY",
    "agentStatus": "NOT_REQUIRED",
    "agentMode": "data-only",
    "protocolVersion": "",
    "reconciliationStatus": "NOT_REQUIRED",
    "snapshotAgeSeconds": None,
    "backupAgeSeconds": None,
    "marketStreamStatus": "NOT_REQUIRED",
    "blockedChecks": [],
  }


@pytest.mark.asyncio
async def test_live_status_requires_account_safety_and_ready_market_stream(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _configure_live(monkeypatch)
  now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)

  class FakeSafetyService:
    async def status(self, account_id: str):
      assert account_id == "account-1"
      return {
        "execution_mode": "REDUCE_ONLY",
        "can_increase_risk": False,
        "agent_status": "READY",
        "agent_mode": "live",
        "protocol_version": "1.1",
        "reconcile_status": "RECONCILED",
        "reconciliation_age_seconds": 12.5,
        "checked_at": now,
        "last_backup_at": now - timedelta(seconds=45),
        "checks": [
          {"code": "ACCOUNT_RISK_INCREASE_AUTHORIZED", "passed": False}
        ],
      }

  async def stale_market_status():
    return {"status": "stale"}

  monkeypatch.setattr(
    live_runtime_status,
    "account_execution_safety_service",
    FakeSafetyService(),
  )
  monkeypatch.setattr(
    live_runtime_status,
    "market_data_runtime_status",
    stale_market_status,
  )

  result = await live_runtime_status.live_trading_runtime_status()

  assert result["status"] == "DISABLED"
  assert result["configuredLive"] is True
  assert result["accountId"] == "account-1"
  assert result["executionMode"] == "REDUCE_ONLY"
  assert result["snapshotAgeSeconds"] == 12.5
  assert result["backupAgeSeconds"] == 45.0
  assert result["marketStreamStatus"] == "STALE"
  assert result["blockedChecks"] == [
    "ACCOUNT_RISK_INCREASE_AUTHORIZED",
    "MARKET_STREAM_READY",
  ]


@pytest.mark.asyncio
async def test_live_status_is_enabled_only_when_all_effective_gates_pass(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _configure_live(monkeypatch)
  now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)

  class FakeSafetyService:
    async def status(self, account_id: str):
      assert account_id == "account-1"
      return {
        "execution_mode": "TRADING",
        "can_increase_risk": True,
        "agent_status": "READY",
        "agent_mode": "live",
        "protocol_version": "1.1",
        "reconcile_status": "RECONCILED",
        "reconciliation_age_seconds": 3.0,
        "checked_at": now,
        "last_backup_at": now - timedelta(seconds=30),
        "checks": [{"code": "LIVE_AGENT_READY", "passed": True}],
      }

  async def ready_market_status():
    return {"status": "ready"}

  monkeypatch.setattr(
    live_runtime_status,
    "account_execution_safety_service",
    FakeSafetyService(),
  )
  monkeypatch.setattr(
    live_runtime_status,
    "market_data_runtime_status",
    ready_market_status,
  )

  result = await live_runtime_status.live_trading_runtime_status()

  assert result["status"] == "ENABLED"
  assert result["executionMode"] == "TRADING"
  assert result["agentStatus"] == "READY"
  assert result["agentMode"] == "live"
  assert result["protocolVersion"] == "1.1"
  assert result["reconciliationStatus"] == "RECONCILED"
  assert result["marketStreamStatus"] == "READY"
  assert result["backupAgeSeconds"] == 30.0
  assert result["blockedChecks"] == []


@pytest.mark.asyncio
async def test_live_status_fails_closed_when_runtime_status_is_unavailable(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _configure_live(monkeypatch)

  class FailingSafetyService:
    async def status(self, _account_id: str):
      raise RuntimeError("database unavailable")

  async def ready_market_status():
    return {"status": "ready"}

  monkeypatch.setattr(
    live_runtime_status,
    "account_execution_safety_service",
    FailingSafetyService(),
  )
  monkeypatch.setattr(
    live_runtime_status,
    "market_data_runtime_status",
    ready_market_status,
  )

  result = await live_runtime_status.live_trading_runtime_status()

  assert result["status"] == "DISABLED"
  assert result["configuredLive"] is True
  assert result["executionMode"] == "OBSERVE_ONLY"
  assert result["blockedChecks"] == ["RUNTIME_STATUS_UNAVAILABLE"]
  assert result["error"] == "RuntimeError"
