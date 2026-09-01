from unittest.mock import AsyncMock

import pytest
from quantx_api import account_safety_observation as observation
from quantx_contracts import ACCOUNT_EXECUTION_SAFETY_CHECK_CODES


@pytest.mark.asyncio
async def test_observation_is_complete_account_free_and_sanitized(monkeypatch):
  account_id = "300000013250"
  secret_hash = "a" * 64
  monkeypatch.setattr(
    observation.settings,
    "real_trading_account_allowlist",
    [account_id],
  )

  async def checks(_self, requested_account_id: str) -> list[dict]:
    assert requested_account_id == account_id
    return [
      {
        "code": code,
        "status": "STANDBY" if code == "MARKET_STREAM_READY" else "PASSED",
        "scope": "INCREASE_RISK",
        "message": (
          f"账户 {account_id} 快照 {secret_hash} 当前休市"
          if code == "MARKET_STREAM_READY"
          else "已通过"
        ),
      }
      for code in ACCOUNT_EXECUTION_SAFETY_CHECK_CODES
    ]

  full_status = AsyncMock(side_effect=AssertionError("observer must not load details"))
  monkeypatch.setattr(observation.AccountExecutionSafetyService, "status", full_status)
  monkeypatch.setattr(observation.AccountExecutionSafetyService, "checks", checks)
  snapshot = await observation.account_safety_observation_snapshot()
  serialized = snapshot.model_dump_json()

  assert snapshot.status == "ready"
  assert len(snapshot.checks) == 18
  standby = next(
    check for check in snapshot.checks if check.code == "MARKET_STREAM_READY"
  )
  assert standby.status.value == "STANDBY"
  assert standby.reason_code == "MARKET_CLOSED_STANDBY"
  assert account_id not in serialized
  assert secret_hash not in serialized
  full_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("accounts", [[], ["account-a", "account-b"]])
async def test_observation_is_disabled_without_one_configured_account(
  monkeypatch, accounts
):
  monkeypatch.setattr(
    observation.settings,
    "real_trading_account_allowlist",
    accounts,
  )

  snapshot = await observation.account_safety_observation_snapshot()

  assert snapshot.status == "disabled"
  assert snapshot.checks == []


@pytest.mark.asyncio
async def test_observation_rejects_incomplete_check_results(monkeypatch):
  monkeypatch.setattr(
    observation.settings, "real_trading_account_allowlist", ["account-a"]
  )
  monkeypatch.setattr(
    observation.AccountExecutionSafetyService, "checks", AsyncMock(return_value=[])
  )
  with pytest.raises(RuntimeError, match="missing check"):
    await observation.account_safety_observation_snapshot()


@pytest.mark.asyncio
async def test_observation_projects_market_recovery_without_an_incident_status(
  monkeypatch,
):
  monkeypatch.setattr(
    observation.settings, "real_trading_account_allowlist", ["account-a"]
  )
  monkeypatch.setattr(
    observation.AccountExecutionSafetyService,
    "checks",
    AsyncMock(
      return_value=[
        {
          "code": code,
          "status": "TRANSIENT" if code == "MARKET_STREAM_READY" else "PASSED",
          "scope": "INCREASE_RISK",
          "message": (
            "Engine 正在恢复全市场行情消费水位" if code == "MARKET_STREAM_READY" else ""
          ),
        }
        for code in ACCOUNT_EXECUTION_SAFETY_CHECK_CODES
      ]
    ),
  )

  snapshot = await observation.account_safety_observation_snapshot()
  market = next(item for item in snapshot.checks if item.code == "MARKET_STREAM_READY")

  assert market.status.value == "TRANSIENT"
  assert market.reason_code == "MARKET_STREAM_RECOVERING"
  assert "恢复" in market.public_message
