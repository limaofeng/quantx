import pytest
from quantx_api.gqlapi.security import required_permission


@pytest.mark.parametrize(
  "field_name",
  [
    "approveTTradeEntry",
    "rejectTTradeEntry",
    "previewTTradeEntryApproval",
    "confirmTTradeEntryApproval",
    "approveStrategyTradeIntent",
    "rejectStrategyTradeIntent",
    "previewStrategyTradeIntentApproval",
    "confirmStrategyTradeIntentApproval",
    "beginTTradeControlledWindow",
    "activateTTradeLive",
    "pauseTTradeEntries",
    "triggerTTradeKillSwitch",
  ],
)
def test_trade_approval_mutations_require_independent_permission(
  field_name: str,
):
  assert required_permission("Mutation", field_name) == "trade:approve"


def test_other_mutations_keep_general_write_permission():
  assert required_permission("Mutation", "pauseStrategyInstance") == "mutation:write"
