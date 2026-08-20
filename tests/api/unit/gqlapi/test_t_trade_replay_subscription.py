from __future__ import annotations

from datetime import datetime

import pytest
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.schemas import realtime_schema
from quantx_api.gqlapi.schemas.realtime_schema import RealtimeSubscription
from quantx_api.gqlapi.types.t_trade_types import TTradeReplayUpdateKind


def test_t_trade_replay_subscription_is_exposed_in_schema() -> None:
  sdl = schema.as_str()

  assert "tTradeReplayUpdates(accountId: String!)" in sdl
  assert "type TTradeReplayUpdateNotice" in sdl
  assert "enum TTradeReplayUpdateKind" in sdl
  assert "revision: String!" in sdl
  assert "processedUntil: DateTime" in sdl


@pytest.mark.asyncio
async def test_t_trade_replay_subscription_maps_wakeup_notice(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  occurred_at = datetime(2026, 8, 16, 10, 30)

  async def subscribe(account_id: str):
    assert account_id == "account-1"
    yield {
      "account_id": account_id,
      "run_id": "run-1",
      "revision": "7",
      "kind": "RESULT_READY",
      "occurred_at": occurred_at.isoformat(),
    }

  monkeypatch.setattr(
    realtime_schema,
    "authorized_account_id",
    lambda _info, account_id: account_id,
  )
  monkeypatch.setattr(
    realtime_schema.t_trade_replay_projection_service,
    "subscribe",
    subscribe,
  )

  stream = RealtimeSubscription().t_trade_replay_updates(
    object(),
    "account-1",
  )
  notice = await anext(stream)
  await stream.aclose()

  assert notice.account_id == "account-1"
  assert notice.run_id == "run-1"
  assert notice.revision == "7"
  assert notice.kind is TTradeReplayUpdateKind.RESULT_READY
  assert notice.occurred_at == occurred_at
