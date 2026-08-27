import pytest
from quantx_api.gqlapi.resolvers.trading_safety import (
  AccountExecutionSafetyResolver,
)
from quantx_api.gqlapi.types.trading_safety_types import (
  AccountExecutionHealthStatus,
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
  }


def test_account_execution_health_status_is_a_closed_business_enum():
  safety = AccountExecutionSafetyResolver.from_payload(_payload("HEALTHY"))

  assert safety.health_status is AccountExecutionHealthStatus.HEALTHY
  assert {status.value for status in AccountExecutionHealthStatus} == {
    "HEALTHY",
    "BLOCKED",
    "KILLED",
  }


@pytest.mark.parametrize("transient_status", ["CHECK", "CHECKING"])
def test_account_execution_health_rejects_query_process_states(
  transient_status: str,
):
  with pytest.raises(ValueError, match=transient_status):
    AccountExecutionSafetyResolver.from_payload(_payload(transient_status))
