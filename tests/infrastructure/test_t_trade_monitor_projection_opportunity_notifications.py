from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.services import t_trade_monitor_projection_service as module
from quantx_infrastructure.services.t_trade_monitor_projection_service import (
  TTradeMonitorProjectionService,
  t_trade_update_channel,
)


def _session_patch(version: int) -> dict[str, object]:
  return {
    "signal_snapshot": {
      "state_schema_version": 3,
      "policy_version": "policy-v3",
      "signal_version": version,
    },
    "pending_entry_intent_id": None,
    "entry_order_status": "",
  }


@pytest.mark.asyncio
async def test_diagnostic_opportunity_notices_are_trailing_coalesced(monkeypatch):
  publish = AsyncMock(return_value=1)
  monkeypatch.setattr(module.redis_pubsub, "publish", publish)
  service = TTradeMonitorProjectionService(
    opportunity_notice_window_seconds=60.0,
  )
  service._persist_opportunity_projection = AsyncMock(return_value="projection-1")

  await service.notify_opportunity(
    account_id="account-1",
    strategy_run_id="run-1",
    instrument_code="600000.sh",
    version="state-1",
    immediate=False,
    session_patch=_session_patch(1),
  )
  await service.notify_opportunity(
    account_id="account-1",
    strategy_run_id="run-1",
    instrument_code="600000.SH",
    version="state-2",
    immediate=False,
    session_patch=_session_patch(2),
  )

  publish.assert_not_awaited()
  assert (
    await service.flush_opportunity_notices(
      account_id="account-1",
      strategy_run_id="run-1",
    )
    == 1
  )
  publish.assert_awaited_once()
  channel, payload = publish.await_args.args
  assert channel == t_trade_update_channel("account-1")
  assert payload["instrument_code"] == "600000.SH"
  assert payload["version"] == "state-2"
  metrics = service.metrics_snapshot()
  assert metrics["counters"]["received_total"] == 2
  assert metrics["counters"]["coalesced_windows_total"] == 1
  assert metrics["counters"]["coalesced_replacements_total"] == 1
  assert metrics["counters"]["published_total"] == 1
  assert metrics["pendingNoticeCount"] == 0


@pytest.mark.asyncio
async def test_material_opportunity_notice_cancels_pending_and_publishes_immediately(
  monkeypatch,
):
  publish = AsyncMock(return_value=1)
  monkeypatch.setattr(module.redis_pubsub, "publish", publish)
  service = TTradeMonitorProjectionService(
    opportunity_notice_window_seconds=60.0,
  )
  service._persist_opportunity_projection = AsyncMock(return_value="projection-1")
  await service.notify_opportunity(
    account_id="account-1",
    strategy_run_id="run-1",
    instrument_code="600000.SH",
    version="diagnostic-1",
    immediate=False,
    session_patch=_session_patch(1),
  )

  assert await service.notify_opportunity(
    account_id="account-1",
    strategy_run_id="run-1",
    instrument_code="600000.SH",
    version="candidate-2",
    immediate=True,
    session_patch=_session_patch(2),
  )

  publish.assert_awaited_once()
  assert publish.await_args.args[1]["version"] == "candidate-2"
  assert (
    await service.flush_opportunity_notices(
      account_id="account-1",
      strategy_run_id="run-1",
    )
    == 0
  )


@pytest.mark.asyncio
async def test_opportunity_notice_is_best_effort_after_durable_state(monkeypatch):
  monkeypatch.setattr(
    module.redis_pubsub,
    "publish",
    AsyncMock(side_effect=RuntimeError("redis unavailable")),
  )
  service = TTradeMonitorProjectionService()
  service._persist_opportunity_projection = AsyncMock(return_value="projection-1")

  assert not await service.notify_opportunity(
    account_id="account-1",
    strategy_run_id="run-1",
    instrument_code="600000.SH",
    version="candidate-1",
    immediate=True,
    session_patch=_session_patch(1),
  )
  assert service.metrics_snapshot()["counters"]["publish_failures_total"] == 1


@pytest.mark.asyncio
async def test_opportunity_projection_commits_before_notification(monkeypatch):
  row = SimpleNamespace(
    account_id="account-1",
    version=8,
    payload={
      "sessions": [
        {
          "run_id": "run-1",
          "stock_code": "600000.SH",
          "signal_snapshot": {"signal_version": 1},
          "pending_entry_intent_id": None,
        }
      ],
      "pending_signal_count": 0,
    },
    generated_at=None,
  )

  class _Result:
    def scalar_one_or_none(self):
      return row

  class _Db:
    committed = False

    async def execute(self, _statement):
      return _Result()

    async def commit(self):
      self.committed = True

  db = _Db()

  async def _sessions():
    yield db

  async def _publish(_channel, payload):
    assert db.committed is True
    assert row.payload["sessions"][0]["signal_snapshot"]["signal_version"] == 9
    assert row.payload["sessions"][0]["pending_entry_intent_id"] == "intent-9"
    assert payload["projection_version"] == "9"
    return 1

  monkeypatch.setattr(module, "get_async_db", _sessions)
  monkeypatch.setattr(module.redis_pubsub, "publish", AsyncMock(side_effect=_publish))

  assert await TTradeMonitorProjectionService().notify_opportunity(
    account_id="account-1",
    strategy_run_id="run-1",
    instrument_code="600000.SH",
    version="state-9",
    immediate=True,
    session_patch={
      "signal_snapshot": {
        "state_schema_version": 3,
        "policy_version": "policy-v3",
        "signal_version": 9,
      },
      "pending_entry_intent_id": "intent-9",
      "entry_order_status": "AWAITING_APPROVAL",
    },
  )
