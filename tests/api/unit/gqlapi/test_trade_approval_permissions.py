from datetime import timedelta

import pytest
import strawberry
from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import utcnow
from quantx_api.gqlapi.security import (
  AuthorizationExtension,
  required_permission,
  required_permissions,
)


@strawberry.type
class _Query:
  @strawberry.field
  def ready(self) -> bool:
    return True


@strawberry.type
class _Mutation:
  @strawberry.mutation
  def place_order(self) -> bool:
    raise AssertionError("mobile manual permission must not reach direct order")


_AUTHORIZATION_SCHEMA = strawberry.Schema(
  query=_Query,
  mutation=_Mutation,
  extensions=[AuthorizationExtension],
)


@pytest.mark.parametrize(
  "field_name",
  [
    "previewTTradeEntryApproval",
    "confirmTTradeEntryApproval",
    "previewStrategyTradeIntentApproval",
    "confirmStrategyTradeIntentApproval",
    "previewStrategyControl",
    "confirmStrategyControl",
    "previewTTradeControl",
    "confirmTTradeControl",
    "previewAccountExecutionControl",
    "confirmAccountExecutionControl",
  ],
)
def test_trade_approval_mutations_require_independent_permission(
  field_name: str,
):
  assert required_permissions("Mutation", field_name)[-1] == "trade:approve"


def test_strategy_lifecycle_uses_narrow_control_permission():
  assert required_permission("Mutation", "pauseStrategyInstance") == "strategy:control"


def test_t_trade_risk_reduction_uses_narrow_control_permission():
  assert required_permission("Mutation", "pauseTTradeEntries") == "t-trade:control"


@pytest.mark.parametrize(
  "field_name",
  [
    "approveTTradeEntry",
    "approveStrategyTradeIntent",
    "activateTTradeLive",
  ],
)
def test_legacy_or_unchallenged_risk_writes_use_domain_permission(
  field_name: str,
):
  assert required_permission("Mutation", field_name) == "strategy:write"


@pytest.mark.parametrize(
  "field_name",
  ["previewManualOrder", "confirmManualOrder", "cancelOrder"],
)
def test_manual_trade_mutations_require_independent_permission(field_name: str):
  assert required_permission("Mutation", field_name) == "trade:manual"


@pytest.mark.parametrize(
  "field_name",
  [
    "previewLiquidation",
    "confirmLiquidation",
    "previewExitPlanAuthorization",
    "confirmExitPlanAuthorization",
  ],
)
def test_liquidation_mutations_require_independent_control_permission(
  field_name: str,
):
  assert required_permission("Mutation", field_name) == "liquidation:control"


def test_legacy_direct_order_does_not_share_mobile_manual_permission():
  assert required_permission("Mutation", "placeOrder") == "orders:write"


@pytest.mark.asyncio
async def test_manual_permission_cannot_execute_legacy_direct_order():
  result = await _AUTHORIZATION_SCHEMA.execute(
    "mutation { placeOrder }",
    context_value={
      "principal": Principal(
        user_id="user-1",
        username="operator",
        display_name="Operator",
        device_session_id="session-1",
        access_token_expires_at=utcnow() + timedelta(minutes=5),
        permissions=frozenset({"trade:manual"}),
        authorized_account_ids=("ACCOUNT-1",),
      ),
      "request_id": "manual-cannot-direct",
    },
  )

  assert result.data is None
  assert result.errors[0].extensions["code"] == "FORBIDDEN"
