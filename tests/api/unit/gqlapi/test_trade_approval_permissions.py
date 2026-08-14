from datetime import timedelta

import pytest
import strawberry
from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import utcnow
from quantx_api.gqlapi.security import AuthorizationExtension, required_permission


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
  ],
)
def test_trade_approval_mutations_require_independent_permission(
  field_name: str,
):
  assert required_permission("Mutation", field_name) == "trade:approve"


def test_strategy_lifecycle_uses_narrow_control_permission():
  assert required_permission("Mutation", "pauseStrategyInstance") == "strategy:control"


@pytest.mark.parametrize(
  "field_name",
  [
    "approveTTradeEntry",
    "approveStrategyTradeIntent",
    "beginTTradeControlledWindow",
    "activateTTradeLive",
    "triggerTTradeKillSwitch",
  ],
)
def test_legacy_or_unchallenged_risk_writes_are_not_native_capabilities(
  field_name: str,
):
  assert required_permission("Mutation", field_name) == "mutation:write"


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
  assert required_permission("Mutation", "placeOrder") == "trade:direct"


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
