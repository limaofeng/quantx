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

  async def status(_self, requested_account_id: str) -> dict:
    assert requested_account_id == account_id
    return {
      "account_id": account_id,
      "checks": [
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
      ],
    }

  monkeypatch.setattr(observation.AccountExecutionSafetyService, "status", status)
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


@pytest.mark.asyncio
async def test_observation_is_disabled_without_one_configured_account(monkeypatch):
  monkeypatch.setattr(
    observation.settings,
    "real_trading_account_allowlist",
    [],
  )

  snapshot = await observation.account_safety_observation_snapshot()

  assert snapshot.status == "disabled"
  assert snapshot.checks == []
