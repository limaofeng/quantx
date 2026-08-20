from __future__ import annotations

from datetime import datetime

import pytest
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.schemas import realtime_schema
from quantx_api.gqlapi.schemas.realtime_schema import RealtimeSubscription
from quantx_api.gqlapi.types.limit_up_board_replay_types import (
  LimitUpBoardReplayUpdateKind,
)


def test_limit_up_board_replay_subscription_is_exposed_in_schema() -> None:
  sdl = schema.as_str()

  assert "limitUpBoardReplayUpdates(accountId: String!)" in sdl
  assert "type LimitUpBoardReplayUpdateNotice" in sdl
  assert "enum LimitUpBoardReplayUpdateKind" in sdl
  assert "jobId: String!" in sdl
  assert "revision: String!" in sdl


@pytest.mark.asyncio
async def test_limit_up_board_replay_subscription_authorizes_and_maps_notice(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  occurred_at = datetime(2026, 8, 20, 10, 30)
  authorized_accounts: list[str] = []

  async def subscribe(account_id: str):
    assert account_id == "authorized-account"
    yield {
      "account_id": account_id,
      "job_id": "job-1",
      "revision": "8",
      "kind": "RESULT_READY",
      "occurred_at": occurred_at.isoformat(),
    }

  def authorize(_info, account_id: str) -> str:
    authorized_accounts.append(account_id)
    return "authorized-account"

  monkeypatch.setattr(realtime_schema, "authorized_account_id", authorize)
  monkeypatch.setattr(
    realtime_schema.limit_up_board_replay_projection_service,
    "subscribe",
    subscribe,
  )

  stream = RealtimeSubscription().limit_up_board_replay_updates(
    object(),
    "requested-account",
  )
  notice = await anext(stream)
  await stream.aclose()

  assert authorized_accounts == ["requested-account"]
  assert notice.account_id == "authorized-account"
  assert notice.job_id == "job-1"
  assert notice.revision == "8"
  assert notice.kind is LimitUpBoardReplayUpdateKind.RESULT_READY
  assert notice.occurred_at == occurred_at
