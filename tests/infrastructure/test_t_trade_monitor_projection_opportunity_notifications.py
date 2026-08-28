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


def _full_signal_snapshot(version: int) -> dict[str, object]:
  return {
    "instrument_code": "600000.SH",
    "trade_date": "2026-08-28",
    "evaluated_at_ms": 1_777_000_000_250,
    "source_time_ms": 1_777_000_000_000,
    "tick_ordinal": 7,
    "continuity_generation": 3,
    "data_health": "READY",
    "features": {"sample_count": 12},
    "pullback": {"phase": "BASELINING"},
    "momentum": {"phase": "BASELINING"},
    "selected_path": "NONE",
    "preview_threshold": 55.0,
    "candidate_threshold": 72.0,
    "revalidate_threshold": 60.0,
    "rearm_threshold": 45.0,
    "signal_version": version,
    "candidate_state_version": version,
    "policy_version": "policy-v3",
    "config_version": 9,
    "feature_schema_version": "1",
  }


def _compact_signal_snapshot(version: int) -> dict[str, object]:
  snapshot = _full_signal_snapshot(version)
  return {
    key: snapshot[key]
    for key in (
      "instrument_code",
      "trade_date",
      "evaluated_at_ms",
      "source_time_ms",
      "tick_ordinal",
      "continuity_generation",
      "data_health",
      "selected_path",
      "signal_version",
      "candidate_state_version",
      "policy_version",
      "config_version",
      "feature_schema_version",
    )
  }


def _monitor_payload(snapshot: dict[str, object], *, checked_at: str) -> dict:
  session = {
    "run_id": "run-1",
    "stock_code": "600000.SH",
    "signal_snapshot": snapshot,
    "updated_at": checked_at,
  }
  return {
    "account_id": "account-1",
    "sessions": [dict(session)],
    "holdings": [
      {
        "stock_code": "600000.SH",
        "session": dict(session),
      }
    ],
    "last_reconciled_at": checked_at,
    "readiness": {
      "stage": "SHADOW",
      "checked_at": checked_at,
      "account_safety": {
        "checked_at": checked_at,
        "reconciliation_age_seconds": 10.0,
      },
    },
  }


class _ProjectionResult:
  def __init__(self, row):
    self._row = row

  def scalar_one_or_none(self):
    return self._row


class _ProjectionDb:
  def __init__(self, row):
    self.row = row
    self.committed = False

  async def execute(self, _statement):
    return _ProjectionResult(self.row)

  async def commit(self):
    self.committed = True


def _projection_sessions(db):
  async def _sessions():
    yield db

  return _sessions


@pytest.mark.asyncio
async def test_periodic_save_preserves_complete_snapshot_without_clock_wakeup(
  monkeypatch,
):
  full_snapshot = _full_signal_snapshot(7)
  row = SimpleNamespace(
    account_id="account-1",
    version=8,
    payload=_monitor_payload(full_snapshot, checked_at="2026-08-28T10:00:00Z"),
    generated_at=None,
  )
  db = _ProjectionDb(row)
  publish = AsyncMock(return_value=1)
  monkeypatch.setattr(module, "get_async_db", _projection_sessions(db))
  monkeypatch.setattr(module.redis_pubsub, "publish", publish)

  result = await TTradeMonitorProjectionService().save(
    "account-1",
    _monitor_payload(
      _compact_signal_snapshot(7),
      checked_at="2026-08-28T10:00:10Z",
    ),
  )

  assert db.committed is True
  assert row.version == 9
  assert result["sessions"][0]["signal_snapshot"] == full_snapshot
  assert result["holdings"][0]["session"]["signal_snapshot"] == full_snapshot
  assert result["last_reconciled_at"] == "2026-08-28T10:00:10Z"
  publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_periodic_save_repairs_legacy_compact_snapshot_from_evidence(
  monkeypatch,
):
  compact_snapshot = _compact_signal_snapshot(7)
  full_snapshot = _full_signal_snapshot(7)
  row = SimpleNamespace(
    account_id="account-1",
    version=8,
    payload=_monitor_payload(compact_snapshot, checked_at="2026-08-28T10:00:00Z"),
    generated_at=None,
  )
  db = _ProjectionDb(row)
  publish = AsyncMock(return_value=1)
  service = TTradeMonitorProjectionService()
  service._load_latest_complete_signal_snapshots = AsyncMock(
    return_value={("run-1", "600000.SH"): full_snapshot}
  )
  monkeypatch.setattr(module, "get_async_db", _projection_sessions(db))
  monkeypatch.setattr(module.redis_pubsub, "publish", publish)

  result = await service.save(
    "account-1",
    _monitor_payload(compact_snapshot, checked_at="2026-08-28T10:00:10Z"),
  )

  assert result["sessions"][0]["signal_snapshot"] == full_snapshot
  service._load_latest_complete_signal_snapshots.assert_awaited_once()
  publish.assert_awaited_once()
  assert publish.await_args.args[1]["version"] == "9"


@pytest.mark.asyncio
async def test_periodic_save_clears_snapshot_when_checkpoint_identity_changes(
  monkeypatch,
):
  row = SimpleNamespace(
    account_id="account-1",
    version=8,
    payload=_monitor_payload(
      _full_signal_snapshot(7),
      checked_at="2026-08-28T10:00:00Z",
    ),
    generated_at=None,
  )
  db = _ProjectionDb(row)
  publish = AsyncMock(return_value=1)
  monkeypatch.setattr(module, "get_async_db", _projection_sessions(db))
  monkeypatch.setattr(module.redis_pubsub, "publish", publish)

  result = await TTradeMonitorProjectionService().save(
    "account-1",
    _monitor_payload(
      _compact_signal_snapshot(8),
      checked_at="2026-08-28T10:00:10Z",
    ),
  )

  assert result["sessions"][0]["signal_snapshot"] is None
  assert result["holdings"][0]["session"]["signal_snapshot"] is None
  publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_repair_loader_returns_only_complete_latest_evidence():
  complete = _full_signal_snapshot(7)
  rows = [
    SimpleNamespace(
      run_id="run-1",
      instrument_code="600000.sh",
      payload={"signal_snapshot": complete},
    ),
    SimpleNamespace(
      run_id="run-1",
      instrument_code="000001.SZ",
      payload={"signal_snapshot": _compact_signal_snapshot(7)},
    ),
  ]
  db = SimpleNamespace(execute=AsyncMock(return_value=rows))

  result = await TTradeMonitorProjectionService._load_latest_complete_signal_snapshots(
    db,
    account_id="account-1",
    session_keys=(("run-1", "600000.SH"), ("run-1", "000001.SZ")),
  )

  assert result == {("run-1", "600000.SH"): complete}
  db.execute.assert_awaited_once()


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
