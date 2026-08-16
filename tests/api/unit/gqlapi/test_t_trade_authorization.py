from datetime import datetime, timedelta, timezone

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.resolvers.t_trade import TTradeResolver
from quantx_api.gqlapi.schema import schema


def _context(account_id: str) -> dict:
  return {
    "principal": Principal(
      user_id="t-trade-user",
      username="t-trade-user",
      display_name="T Trade User",
      device_session_id="t-trade-session",
      access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
      + timedelta(minutes=5),
      permissions=frozenset({"strategy:write"}),
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
