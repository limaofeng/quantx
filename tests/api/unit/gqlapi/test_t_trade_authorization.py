from datetime import datetime, timedelta, timezone

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.resolvers.t_trade import TTradeResolver
from quantx_api.gqlapi.schema import schema
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityPolicy


def _camel_case(value: str) -> str:
  head, *tail = value.split("_")
  return head + "".join(item[:1].upper() + item[1:] for item in tail)


def _graphql_policy_input() -> dict:
  raw = OpportunityPolicy().to_dict()
  return {
    _camel_case(key): value
    for key, value in raw.items()
    if key not in {"policy_version", "feature_schema_version"}
  }


def _context(
  account_id: str,
  permissions: frozenset[str] = frozenset({"strategy:write"}),
) -> dict:
  return {
    "principal": Principal(
      user_id="t-trade-user",
      username="t-trade-user",
      display_name="T Trade User",
      device_session_id="t-trade-session",
      access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
      + timedelta(minutes=5),
      permissions=permissions,
      authorized_account_ids=(account_id,),
    ),
    "request_id": "t-trade-request",
  }


@pytest.mark.asyncio
async def test_t_trade_run_owner_is_authorized_before_stop(monkeypatch):
  called = False

  async def fake_owner(run_id):
    assert run_id == "run-other-account"
    return "OTHER-ACCOUNT"

  async def fake_stop(run_id):
    nonlocal called
    called = True
    raise AssertionError(run_id)

  monkeypatch.setattr(TTradeResolver, "session_account_id", fake_owner)
  monkeypatch.setattr(TTradeResolver, "stop_session", fake_stop)

  result = await schema.execute(
    """
    mutation {
      stopTTradeSession(runId: "run-other-account") {
        success
      }
    }
    """,
    context_value=_context("AUTHORIZED-ACCOUNT"),
  )

  assert not called
  assert result.errors
  assert result.errors[0].extensions["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_signal_policy_preview_rejects_unauthorized_account(monkeypatch):
  resolver = None

  async def fail_if_called(input):
    nonlocal resolver
    resolver = input
    raise AssertionError("unauthorized preview reached resolver")

  monkeypatch.setattr(TTradeResolver, "preview_signal_policy", fail_if_called)
  result = await schema.execute(
    """
    mutation PreviewPolicy($input: TTradeSignalPolicyPreviewInput!) {
      previewTTradeSignalPolicy(input: $input) {
        valid
      }
    }
    """,
    variable_values={
      "input": {
        "accountId": "OTHER-ACCOUNT",
        "expectedConfigVersion": 0,
        "signalPolicy": _graphql_policy_input(),
      }
    },
    context_value=_context(
      "AUTHORIZED-ACCOUNT",
      permissions=frozenset({"t-trade:control"}),
    ),
  )

  assert resolver is None
  assert result.errors
  assert result.errors[0].extensions["code"] == "FORBIDDEN"
