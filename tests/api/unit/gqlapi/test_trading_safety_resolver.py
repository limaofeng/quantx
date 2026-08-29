import pytest
from quantx_api.gqlapi.resolvers.trading_safety import (
  AccountExecutionSafetyResolver,
)
from quantx_api.gqlapi.types.trading_safety_types import (
  AccountExecutionHealthStatus,
  AccountExecutionSafetyCheckStatus,
  AccountSafetyHistoryRange,
  AccountSafetyHistoryStatus,
)


def _payload(health_status: str) -> dict:
  return {
    "account_id": "300000013250",
    "authorization_state": "ENABLED",
    "state_version": 3,
    "health_status": health_status,
    "execution_mode": "TRADING",
    "can_increase_risk": True,
    "can_reduce_risk": True,
    "can_activate_automation": True,
    "summary": "账户状态与买入条件均已通过",
    "checks": [
      {
        "code": "MARKET_STREAM_READY",
        "status": "STANDBY",
        "message": "当前休市",
        "scope": "INCREASE_RISK",
      }
    ],
  }


def test_account_execution_health_status_is_a_closed_business_enum():
  safety = AccountExecutionSafetyResolver.from_payload(_payload("HEALTHY"))

  assert safety.health_status is AccountExecutionHealthStatus.HEALTHY
  assert {status.value for status in AccountExecutionHealthStatus} == {
    "HEALTHY",
    "BLOCKED",
    "KILLED",
  }
  assert safety.checks[0].status is AccountExecutionSafetyCheckStatus.STANDBY


@pytest.mark.parametrize("transient_status", ["CHECK", "CHECKING"])
def test_account_execution_health_rejects_query_process_states(
  transient_status: str,
):
  with pytest.raises(ValueError, match=transient_status):
    AccountExecutionSafetyResolver.from_payload(_payload(transient_status))


@pytest.mark.asyncio
async def test_history_maps_monitor_observations_without_account_data(monkeypatch):
  async def fetch(history_range: str) -> dict:
    assert history_range == "30d"
    return {
      "available": True,
      "range": "30d",
      "generatedAt": "2026-08-27T05:00:00Z",
      "firstObservedAt": "2026-08-26T05:00:00Z",
      "lastObservedAt": "2026-08-27T05:00:00Z",
      "observerFresh": True,
      "bucketSeconds": 14400,
      "checks": [
        {
          "code": "MARKET_STREAM_READY",
          "currentStatus": "standby",
          "checkedAt": "2026-08-27T05:00:00Z",
          "reasonCode": "MARKET_CLOSED_STANDBY",
          "publicMessage": "当前休市",
          "coveragePct": 100,
          "incidentCount": 0,
          "points": [],
        }
      ],
      "incidents": [],
      "incidentsTruncated": False,
    }

  monkeypatch.setattr(
    "quantx_api.gqlapi.resolvers.trading_safety.fetch_account_safety_history",
    fetch,
  )

  history = await AccountExecutionSafetyResolver.history(
    AccountSafetyHistoryRange.DAYS_30
  )

  assert history.available is True
  assert history.checks[0].current_status is AccountSafetyHistoryStatus.STANDBY
  assert history.checks[0].incident_count == 0
