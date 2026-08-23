import asyncio
import json
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine import report_processor
from quantx_engine.t_trade_coordination import t_trade_account_coordination_lock


@pytest.mark.asyncio
async def test_partial_delta_uses_position_delta_without_full_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls = SimpleNamespace(delta=[], full=[])

  class FakePositionService:
    async def apply_position_delta(self, value, account_id):
      calls.delta.append((value, account_id))

    async def prepare_full_snapshot(self, **kwargs):
      calls.full.append(dict(kwargs))
      return {"applied": True, "reason": "PREPARED"}

    async def finalize_full_snapshot(self, **_kwargs):
      return {"applied": True, "reason": "APPLIED"}

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  monkeypatch.setattr(
    report_processor,
    "_invalidate_t_trade_entry_authority_for_account",
    AsyncMock(),
  )
  monkeypatch.setattr(
    report_processor,
    "_upsert_account",
    lambda value: _async_noop(value),
  )

  await report_processor._process_delta_report(
    "device-1",
    {
      "account_id": "account-1",
      "position_deltas": [
        {
          "stock_code": "600000.SH",
          "volume": 100,
          "can_use_volume": 0,
        }
      ],
      "is_complete": False,
      "sequence": 100,
    },
  )

  assert calls.full == []
  assert calls.delta == [
    (
      {
        "stock_code": "600000.SH",
        "volume": 100,
        "can_use_volume": 0,
      },
      "account-1",
    )
  ]


@pytest.mark.asyncio
async def test_complete_delta_still_applies_authoritative_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls = SimpleNamespace(delta=[], full=[])

  class FakePositionService:
    async def apply_position_delta(self, value, account_id):
      calls.delta.append((value, account_id))

    async def get_snapshot_status(self, _account_id: str):
      return None

    async def mark_snapshot_failure(self, _account_id: str, _error: str):
      return None

    async def begin_full_snapshot_attempt(self, **_kwargs):
      return {"applied": True, "reason": "STARTED"}

    async def prepare_full_snapshot(self, **kwargs):
      calls.full.append(dict(kwargs))
      return {"applied": True, "reason": "PREPARED"}

    async def finalize_full_snapshot(self, **_kwargs):
      return {"applied": True, "reason": "APPLIED"}

  class FakeDatabase:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def get(self, *_, **__):
      return None

    def add(self, _value):
      return None

    async def commit(self):
      return None

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", FakeDatabase)
  monkeypatch.setattr(
    report_processor,
    "_upsert_account",
    lambda value: _async_noop(value),
  )
  monkeypatch.setattr(
    report_processor,
    "_snapshot_discrepancies",
    lambda *_args, **_kwargs: _async_result(
      {
        "blocking_discrepancies": [],
        "external_orders": [],
        "external_trades": [],
      }
    ),
  )

  payload = {
    "snapshot_id": "snapshot-101",
    "positions_by_account": {"account-1": []},
    "accounts": [{"account_id": "account-1"}],
    "orders": [],
    "trades": [],
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "is_complete": True,
    "sequence": 101,
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()

  await report_processor._process_delta_report(
    "device-1",
    payload,
    protocol_version="1.1",
  )

  assert calls.delta == []
  assert len(calls.full) == 1
  assert calls.full[0]["account_id"] == "account-1"
  assert "complete" not in calls.full[0]


@pytest.mark.asyncio
async def test_snapshot_writer_waits_for_monitor_publication_after_second_read(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """The shared account lock linearizes writer mutation after monitor publish."""

  events: list[str] = []
  monitor_published = asyncio.Event()

  async def invalidate(_account_id: str, *, reason: str) -> None:
    events.append(f"invalidate:{reason}")

  monkeypatch.setattr(
    report_processor,
    "_invalidate_t_trade_entry_authority_for_account",
    invalidate,
  )

  async def mutate() -> None:
    events.append("writer-mutation")

  coordination_lock = t_trade_account_coordination_lock("account-1")
  await coordination_lock.acquire()
  try:
    writer = asyncio.create_task(
      report_processor._run_account_snapshot_mutation(
        "account-1",
        mutate,
        reason="BROKER_POSITION_DELTA_APPLIED",
      )
    )
    await asyncio.sleep(0)
    # The monitor has completed both reads and is still in its publication
    # boundary; the report writer cannot commit a mutation in that interval.
    assert events == []
    events.append("monitor-published")
    monitor_published.set()
  finally:
    coordination_lock.release()

  await asyncio.wait_for(writer, timeout=1)
  assert monitor_published.is_set()
  assert events == [
    "monitor-published",
    "writer-mutation",
    "invalidate:BROKER_POSITION_DELTA_APPLIED",
  ]


@pytest.mark.asyncio
async def test_failed_delta_writer_marks_snapshot_stale_before_rethrow(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  marker = AsyncMock()
  invalidation = AsyncMock()
  monkeypatch.setattr(
    report_processor,
    "PositionService",
    lambda: SimpleNamespace(mark_snapshot_failure=marker),
  )
  monkeypatch.setattr(
    report_processor,
    "_invalidate_t_trade_entry_authority_for_account",
    invalidation,
  )

  async def mutation() -> None:
    raise RuntimeError("delta commit failed")

  with pytest.raises(RuntimeError, match="delta commit failed"):
    await report_processor._run_account_snapshot_mutation(
      "account-1",
      mutation,
      reason="BROKER_POSITION_DELTA_APPLIED",
    )

  marker.assert_awaited_once_with(
    "account-1",
    "BROKER_POSITION_DELTA_APPLIED:APPLY_FAILED",
  )
  invalidation.assert_awaited_once_with(
    "account-1",
    reason="BROKER_POSITION_DELTA_APPLIED",
  )


@pytest.mark.asyncio
async def test_authority_invalidation_targets_only_matching_v3_account_runtime(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Writer invalidation preserves non-T-trade runs and exact account scope."""

  matching = SimpleNamespace(
    run_id="run-matching",
    context=SimpleNamespace(parameters={"account_id": "account-1"}),
  )
  other_account = SimpleNamespace(
    run_id="run-other-account",
    context=SimpleNamespace(parameters={"account_id": "account-2"}),
  )
  non_t_trade = SimpleNamespace(
    run_id="run-non-t-trade",
    context=SimpleNamespace(parameters={"account_id": "account-1"}),
  )
  executor = SimpleNamespace(
    runs={
      matching.run_id: matching,
      other_account.run_id: other_account,
      non_t_trade.run_id: non_t_trade,
    },
    _uses_t_trade_opportunity_runtime=lambda runtime: runtime is matching,
    invalidate_t_trade_entry_authority=AsyncMock(return_value=True),
  )
  manager = SimpleNamespace(executor=executor)
  import importlib

  strategy_manager_module = importlib.import_module(
    "quantx_engine.strategy_manager"
  )
  monkeypatch.setattr(strategy_manager_module, "strategy_manager", manager)

  await report_processor._invalidate_t_trade_entry_authority_for_account(
    "account-1",
    reason="BROKER_POSITION_SNAPSHOT_UPDATED",
  )

  executor.invalidate_t_trade_entry_authority.assert_awaited_once_with(
    "run-matching",
    account_id="account-1",
    reason="BROKER_POSITION_SNAPSHOT_UPDATED",
  )


@pytest.mark.asyncio
async def test_authority_invalidation_rejects_unbounded_runtime_scan(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import importlib

  strategy_manager_module = importlib.import_module(
    "quantx_engine.strategy_manager"
  )
  executor = SimpleNamespace(
    runs={str(index): SimpleNamespace() for index in range(4097)},
    _uses_t_trade_opportunity_runtime=lambda _runtime: False,
    invalidate_t_trade_entry_authority=AsyncMock(return_value=True),
  )
  monkeypatch.setattr(
    strategy_manager_module,
    "strategy_manager",
    SimpleNamespace(executor=executor),
  )

  with pytest.raises(report_processor.RetryableReportError, match="4096"):
    await report_processor._invalidate_t_trade_entry_authority_for_account(
      "account-1",
      reason="BROKER_POSITION_SNAPSHOT_UPDATED",
    )
  executor.invalidate_t_trade_entry_authority.assert_not_awaited()


def test_snapshot_account_scope_rejects_4097_accounts() -> None:
  payload = {
    "positions_by_account": {
      f"account-{index}": [] for index in range(4097)
    }
  }

  with pytest.raises(report_processor.RetryableReportError, match="4096"):
    report_processor._report_account_ids(payload)


@pytest.mark.asyncio
async def test_oversized_full_scope_fails_closed_to_authenticated_device_scope(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  state = {
    "account-authorized": {
      "sequence": 22,
      "is_complete": True,
      "last_error": None,
    }
  }
  marker_calls: list[tuple[str, str]] = []

  class FakePositionService:
    async def mark_snapshot_failure(self, account_id: str, error: str) -> None:
      marker_calls.append((account_id, error))
      state[account_id]["is_complete"] = False
      state[account_id]["last_error"] = error

  class FakeDatabase:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def get(self, model, _key, **_kwargs):
      if model is report_processor.AgentDevice:
        return SimpleNamespace(
          revoked_at=None,
          authorized_account_ids=["account-authorized"],
        )
      return None

    def add(self, _value):
      return None

    async def commit(self):
      return None

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", FakeDatabase)
  monkeypatch.setattr(
    report_processor,
    "_invalidate_t_trade_entry_authority_for_account",
    AsyncMock(),
  )

  payload = {
    "snapshot_id": "oversized-full",
    "is_complete": True,
    "source_sequence": 23,
    "unavailable_accounts": [],
    "accounts": [
      {"account_id": f"account-{index}"} for index in range(4097)
    ],
    "positions_by_account": {
      f"account-{index}": [] for index in range(4097)
    },
    "section_completeness_by_account": {
      f"account-{index}": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
      for index in range(4097)
    },
  }

  with pytest.raises(report_processor.RetryableReportError, match="4096"):
    await report_processor._process_delta_report(
      "device-1",
      payload,
      protocol_version="1.1",
    )

  assert marker_calls == [
    (
      "account-authorized",
      "SNAPSHOT_APPLY_FAILED:FULL_SNAPSHOT_APPLY_FAILED",
    )
  ]
  assert state["account-authorized"]["is_complete"] is False


@pytest.mark.asyncio
async def test_stale_full_duplicate_does_not_replay_business_sections(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls = SimpleNamespace(apply=0, mark=0)

  class FakePositionService:
    async def get_snapshot_status(self, _account_id: str):
      return {
        "sequence": 200,
        "is_complete": True,
      }

    async def mark_snapshot_failure(self, _account_id: str, _error: str):
      calls.mark += 1

    async def begin_full_snapshot_attempt(self, **_kwargs):
      return {"applied": False, "reason": "STALE_SEQUENCE"}

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  process_order = AsyncMock()
  upsert_account = AsyncMock()
  monkeypatch.setattr(report_processor, "_process_order_report", process_order)
  process_trade = AsyncMock()
  monkeypatch.setattr(
    report_processor,
    "_process_execution_report",
    process_trade,
  )
  monkeypatch.setattr(report_processor, "_upsert_account", upsert_account)
  fail_closed = AsyncMock()
  monkeypatch.setattr(report_processor, "_fail_closed_incomplete_snapshot", fail_closed)

  payload = {
    "snapshot_id": "stale-snapshot",
    "is_complete": True,
    "source_sequence": 100,
    "source_event_at": datetime.now(timezone.utc).isoformat(),
    "accounts": [{
      "account_id": "account-1",
      "cash": "999999.99",
      "total_asset": "999999.99",
    }],
    "positions_by_account": {"account-1": []},
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "orders": [{"account_id": "account-1", "order_id": "old-order"}],
    "trades": [{"account_id": "account-1", "trade_id": "old-trade"}],
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()

  await report_processor._process_delta_report(
    "device-1",
    payload,
    protocol_version="1.1",
  )

  assert calls.apply == 0
  assert calls.mark == 0
  process_order.assert_not_awaited()
  process_trade.assert_not_awaited()
  upsert_account.assert_not_awaited()
  fail_closed.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_full_duplicate_does_not_stage_runtime_zero_fill_event() -> None:
  payload = {
    "snapshot_id": "older-snapshot",
    "is_complete": True,
    "source_sequence": 1,
    "source_event_at": datetime.now(timezone.utc).isoformat(),
    "accounts": [{"account_id": "account-1"}],
    "positions_by_account": {"account-1": []},
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "orders": [{
      "account_id": "account-1",
      "client_order_id": "client-1",
      "order_id": "old-order",
      "stock_code": "600000.SH",
      "order_status": "CANCELLED",
      "order_volume": 100,
      "traded_volume": 0,
    }],
    "trades": [],
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()

  class FakeDatabase:
    async def get(self, _model, _account_id):
      return SimpleNamespace(
        last_snapshot_id="newer-snapshot",
        last_snapshot_hash="b" * 64,
        reconcile_status="READY",
      )

  report = SimpleNamespace(
    message_type="delta_report",
    protocol_version="1.1",
    payload=payload,
  )

  assert await report_processor._full_snapshot_zero_fill_items(
    FakeDatabase(),
    report,
  ) == []


@pytest.mark.asyncio
async def test_full_snapshot_keeps_monitor_out_until_final_rollout_projection(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  events: list[str] = []

  class FakePositionService:
    async def get_snapshot_status(self, _account_id: str):
      return None

    async def mark_snapshot_failure(self, _account_id: str, _error: str):
      events.append("preflight-stale-marker")

    async def begin_full_snapshot_attempt(self, **_kwargs):
      events.append("snapshot-begin-incomplete")
      return {"applied": True, "reason": "STARTED"}

    async def prepare_full_snapshot(self, **_kwargs):
      events.append("position-prepare-incomplete")
      return {"applied": True, "reason": "PREPARED"}

    async def finalize_full_snapshot(self, **_kwargs):
      events.append("snapshot-finalize-complete")
      return {"applied": True, "reason": "APPLIED"}

  class FakeDatabase:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def get(self, *_args, **_kwargs):
      return None

    def add(self, _value):
      return None

    async def commit(self):
      events.append("rollout-commit")

  async def monitor_probe() -> None:
    async with t_trade_account_coordination_lock("account-1"):
      events.append("monitor-publish")

  async def discrepancies(*_args, **_kwargs):
    monitor_task = asyncio.create_task(monitor_probe())
    await asyncio.sleep(0)
    events.append("discrepancy-read")
    await asyncio.sleep(0)
    assert not monitor_task.done()
    return {
      "blocking_discrepancies": [],
      "external_orders": [],
      "external_trades": [],
    }

  async def invalidate(_account_id: str, *, reason: str) -> None:
    events.append(f"authority-clear:{reason}")

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", FakeDatabase)
  monkeypatch.setattr(report_processor, "_snapshot_discrepancies", discrepancies)
  monkeypatch.setattr(
    report_processor,
    "_invalidate_t_trade_entry_authority_for_account",
    invalidate,
  )
  monkeypatch.setattr(report_processor, "_upsert_account", AsyncMock())

  payload = {
    "snapshot_id": "snapshot-linearized",
    "is_complete": True,
    "source_sequence": 1,
    "source_event_at": datetime.now(timezone.utc).isoformat(),
    "accounts": [{"account_id": "account-1"}],
    "positions_by_account": {"account-1": []},
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "orders": [],
    "trades": [],
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()

  await report_processor._process_delta_report(
    "device-1",
    payload,
    protocol_version="1.1",
  )
  await asyncio.sleep(0)

  assert events.index("position-prepare-incomplete") < events.index(
    "discrepancy-read"
  )
  assert events.index("snapshot-begin-incomplete") < events.index(
    "position-prepare-incomplete"
  )
  assert events.index("discrepancy-read") < events.index("rollout-commit")
  assert events.index("rollout-commit") < events.index(
    "authority-clear:BROKER_POSITION_SNAPSHOT_UPDATED"
  )
  assert events.index(
    "authority-clear:BROKER_POSITION_SNAPSHOT_UPDATED"
  ) < events.index("snapshot-finalize-complete")
  assert events.index("snapshot-finalize-complete") < events.index(
    "monitor-publish"
  )


@pytest.mark.asyncio
async def test_prepared_full_snapshot_same_sequence_can_resume_to_complete(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  state = {
    "sequence": 7,
    "is_complete": False,
    "last_error": "SNAPSHOT_APPLY_IN_PROGRESS",
  }
  calls: list[str] = []

  class FakePositionService:
    async def get_snapshot_status(self, _account_id: str):
      return dict(state)

    async def mark_snapshot_failure(self, _account_id: str, error: str):
      calls.append("mark:" + error)
      state["last_error"] = error

    async def begin_full_snapshot_attempt(self, **_kwargs):
      calls.append("begin")
      state["is_complete"] = False
      state["last_error"] = "SNAPSHOT_APPLY_IN_PROGRESS"
      return {"applied": True, "reason": "STARTED"}

    async def prepare_full_snapshot(self, **_kwargs):
      calls.append("prepare")
      state["is_complete"] = False
      state["last_error"] = "SNAPSHOT_APPLY_IN_PROGRESS"
      return {"applied": True, "reason": "PREPARED"}

    async def finalize_full_snapshot(self, **_kwargs):
      calls.append("finalize")
      state["is_complete"] = True
      state["last_error"] = None
      return {"applied": True, "reason": "APPLIED"}

  class FakeDatabase:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def get(self, *_args, **_kwargs):
      return None

    def add(self, _value):
      return None

    async def commit(self):
      return None

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", FakeDatabase)
  monkeypatch.setattr(
    report_processor,
    "_snapshot_discrepancies",
    lambda *_args, **_kwargs: _async_result(
      {
        "blocking_discrepancies": [],
        "external_orders": [],
        "external_trades": [],
      }
    ),
  )
  monkeypatch.setattr(
    report_processor,
    "_invalidate_t_trade_entry_authority_for_account",
    AsyncMock(),
  )
  monkeypatch.setattr(report_processor, "_upsert_account", AsyncMock())

  payload = {
    "snapshot_id": "snapshot-resume",
    "is_complete": True,
    "source_sequence": 7,
    "source_event_at": datetime.now(timezone.utc).isoformat(),
    "accounts": [{"account_id": "account-1"}],
    "positions_by_account": {"account-1": []},
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "orders": [],
    "trades": [],
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()

  await report_processor._process_delta_report(
    "device-1",
    payload,
    protocol_version="1.1",
  )

  assert "prepare" in calls
  assert calls[-1] == "finalize"
  assert state["is_complete"] is True
  assert state["last_error"] is None


@pytest.mark.asyncio
async def test_failed_newer_full_generation_blocks_intermediate_sequence(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  state = {
    "sequence": 5,
    "is_complete": True,
    "last_error": None,
  }
  calls: list[str] = []

  def is_resumable() -> bool:
    return (
      not state["is_complete"]
      and str(state["last_error"] or "").startswith(
        (
          "SNAPSHOT_APPLY_IN_PROGRESS",
          "SNAPSHOT_APPLY_FAILED",
          "SNAPSHOT_AUTHORITY_INVALIDATION_FAILED",
        )
      )
    )

  class FakePositionService:
    async def begin_full_snapshot_attempt(self, **kwargs):
      sequence = int(kwargs["sequence"])
      if sequence < state["sequence"]:
        return {"applied": False, "reason": "STALE_SEQUENCE"}
      if sequence == state["sequence"] and not is_resumable():
        return {"applied": False, "reason": "STALE_SEQUENCE"}
      state.update(
        sequence=sequence,
        is_complete=False,
        last_error="SNAPSHOT_APPLY_IN_PROGRESS",
      )
      calls.append(f"begin:{sequence}")
      return {"applied": True, "reason": "STARTED"}

    async def mark_snapshot_failure(self, _account_id: str, error: str):
      state["is_complete"] = False
      state["last_error"] = error
      calls.append("failure:" + error)

    async def prepare_full_snapshot(self, **kwargs):
      assert int(kwargs["sequence"]) == state["sequence"]
      assert is_resumable()
      state["last_error"] = "SNAPSHOT_APPLY_IN_PROGRESS"
      calls.append("prepare")
      return {"applied": True, "reason": "PREPARED"}

    async def finalize_full_snapshot(self, **kwargs):
      assert int(kwargs["sequence"]) == state["sequence"]
      assert state["last_error"] == "SNAPSHOT_APPLY_IN_PROGRESS"
      state["is_complete"] = True
      state["last_error"] = None
      calls.append("finalize")
      return {"applied": True, "reason": "APPLIED"}

  class FakeDatabase:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def get(self, *_args, **_kwargs):
      return None

    def add(self, _value):
      return None

    async def commit(self):
      return None

  position_service = FakePositionService()
  order_report = AsyncMock()
  order_report.side_effect = [RuntimeError("order convergence failed"), None]
  monkeypatch.setattr(report_processor, "PositionService", lambda: position_service)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", FakeDatabase)
  monkeypatch.setattr(report_processor, "_process_order_report", order_report)
  monkeypatch.setattr(report_processor, "_process_execution_report", AsyncMock())
  monkeypatch.setattr(report_processor, "_upsert_account", AsyncMock())
  monkeypatch.setattr(
    report_processor,
    "_snapshot_discrepancies",
    lambda *_args, **_kwargs: _async_result(
      {
        "blocking_discrepancies": [],
        "external_orders": [],
        "external_trades": [],
      }
    ),
  )
  monkeypatch.setattr(
    report_processor,
    "_invalidate_t_trade_entry_authority_for_account",
    AsyncMock(),
  )

  async def fail_closed(_device_id, _payload, **_kwargs):
    await position_service.mark_snapshot_failure(
      "account-1",
      "SNAPSHOT_APPLY_FAILED:FULL_SNAPSHOT_APPLY_FAILED",
    )

  monkeypatch.setattr(
    report_processor,
    "_fail_closed_incomplete_snapshot",
    fail_closed,
  )

  def payload(sequence: int, snapshot_id: str) -> dict:
    value = {
      "snapshot_id": snapshot_id,
      "is_complete": True,
      "source_sequence": sequence,
      "source_event_at": datetime.now(timezone.utc).isoformat(),
      "accounts": [{"account_id": "account-1", "cash": "100"}],
      "positions_by_account": {"account-1": []},
      "section_completeness_by_account": {
        "account-1": {
          "account": True,
          "positions": True,
          "orders": True,
          "trades": True,
        }
      },
      "unavailable_accounts": [],
      "orders": [{
        "account_id": "account-1",
        "order_id": f"order-{sequence}",
        "order_status": "SUBMITTED",
      }],
      "trades": [],
    }
    value["snapshot_hash"] = sha256(
      json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
      ).encode("utf-8")
    ).hexdigest()
    return value

  with pytest.raises(RuntimeError, match="order convergence failed"):
    await report_processor._process_delta_report(
      "device-1",
      payload(7, "snapshot-7"),
      protocol_version="1.1",
    )

  assert state == {
    "sequence": 7,
    "is_complete": False,
    "last_error": "SNAPSHOT_APPLY_FAILED:FULL_SNAPSHOT_APPLY_FAILED",
  }
  assert calls == [
    "begin:7",
    "failure:SNAPSHOT_APPLY_FAILED:FULL_SNAPSHOT_APPLY_FAILED",
  ]

  await report_processor._process_delta_report(
    "device-1",
    payload(6, "snapshot-6"),
    protocol_version="1.1",
  )
  assert state["sequence"] == 7
  assert state["is_complete"] is False
  assert "begin:6" not in calls
  assert order_report.await_count == 1

  await report_processor._process_delta_report(
    "device-1",
    payload(7, "snapshot-7-retry"),
    protocol_version="1.1",
  )
  assert state == {
    "sequence": 7,
    "is_complete": True,
    "last_error": None,
  }
  assert calls[-3:] == ["begin:7", "prepare", "finalize"]
  assert order_report.await_count == 2


@pytest.mark.asyncio
async def test_delta_incomplete_marker_rejects_same_sequence_old_full_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  state = {
    "sequence": 7,
    "is_complete": False,
    "last_error": "ACCOUNT_SNAPSHOT_STALE:持仓增量未形成完整账户快照",
  }
  prepare = AsyncMock()
  finalize = AsyncMock()

  class FakePositionService:
    async def get_snapshot_status(self, _account_id: str):
      return dict(state)

    async def mark_snapshot_failure(self, _account_id: str, _error: str):
      return None

    async def begin_full_snapshot_attempt(self, **_kwargs):
      return {"applied": False, "reason": "STALE_SEQUENCE"}

    prepare_full_snapshot = prepare
    finalize_full_snapshot = finalize

  class FakeDatabase:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def get(self, *_args, **_kwargs):
      return None

    async def commit(self):
      return None

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", FakeDatabase)
  monkeypatch.setattr(report_processor, "_upsert_account", AsyncMock())

  payload = {
    "snapshot_id": "snapshot-old-full",
    "is_complete": True,
    "source_sequence": 7,
    "source_event_at": datetime.now(timezone.utc).isoformat(),
    "accounts": [{"account_id": "account-1"}],
    "positions_by_account": {"account-1": []},
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "orders": [],
    "trades": [],
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()

  await report_processor._process_delta_report(
    "device-1",
    payload,
    protocol_version="1.1",
  )

  prepare.assert_not_awaited()
  finalize.assert_not_awaited()
  assert state["is_complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_time", ["not-a-time", float("nan"), float("inf")])
async def test_invalid_authoritative_snapshot_time_still_fails_closed(
  monkeypatch: pytest.MonkeyPatch,
  bad_time: object,
) -> None:
  position_service = SimpleNamespace(mark_snapshot_failure=AsyncMock())
  monkeypatch.setattr(report_processor, "PositionService", lambda: position_service)
  fail_closed = AsyncMock()
  monkeypatch.setattr(report_processor, "_fail_closed_incomplete_snapshot", fail_closed)

  payload = {
    "snapshot_id": "snapshot-bad-time",
    "is_complete": True,
    "source_sequence": 1,
    "source_event_at": bad_time,
    "accounts": [{"account_id": "account-1"}],
    "positions_by_account": {"account-1": []},
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "orders": [],
    "trades": [],
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()

  with pytest.raises(ValueError, match="source_event_at"):
    await report_processor._process_delta_report(
      "device-1",
      payload,
      protocol_version="1.1",
    )

  fail_closed.assert_awaited_once()
  failure_time = fail_closed.await_args.kwargs["reported_at"]
  assert isinstance(failure_time, datetime)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_sequence", [0, -1, None, "not-an-int"])
async def test_invalid_authoritative_sequence_still_fails_closed(
  monkeypatch: pytest.MonkeyPatch,
  bad_sequence: object,
) -> None:
  fail_closed = AsyncMock()
  monkeypatch.setattr(
    report_processor,
    "_fail_closed_incomplete_snapshot",
    fail_closed,
  )
  monkeypatch.setattr(
    report_processor,
    "PositionService",
    lambda: SimpleNamespace(),
  )

  payload = {
    "snapshot_id": "snapshot-bad-sequence",
    "is_complete": True,
    "source_sequence": bad_sequence,
    "source_event_at": datetime.now(timezone.utc).isoformat(),
    "accounts": [{"account_id": "account-1"}],
    "positions_by_account": {"account-1": []},
    "section_completeness_by_account": {
      "account-1": {
        "account": True,
        "positions": True,
        "orders": True,
        "trades": True,
      }
    },
    "unavailable_accounts": [],
    "orders": [],
    "trades": [],
  }
  payload["snapshot_hash"] = sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()

  with pytest.raises(ValueError, match="sequence"):
    await report_processor._process_delta_report(
      "device-1",
      payload,
      protocol_version="1.1",
    )

  fail_closed.assert_awaited_once()
  assert (
    fail_closed.await_args.kwargs["failure_kind"]
    == "SNAPSHOT_APPLY_FAILED"
  )


@pytest.mark.asyncio
async def test_invalid_full_without_payload_account_uses_authenticated_device_scope(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls: list[tuple[str, str]] = []

  class FakePositionService:
    async def mark_snapshot_failure(self, account_id: str, error: str) -> None:
      calls.append((account_id, error))

  class FakeDatabase:
    def __init__(self) -> None:
      self.added: list[object] = []

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def get(self, model, _key, **_kwargs):
      if model is report_processor.AgentDevice:
        return SimpleNamespace(
          revoked_at=None,
          authorized_account_ids=["account-authorized"],
        )
      return None

    def add(self, value) -> None:
      self.added.append(value)

    async def commit(self) -> None:
      return None

  monkeypatch.setattr(report_processor, "PositionService", FakePositionService)
  monkeypatch.setattr(
    report_processor,
    "AsyncSessionLocal",
    lambda: FakeDatabase(),
  )
  monkeypatch.setattr(
    report_processor,
    "_invalidate_t_trade_entry_authority_for_account",
    AsyncMock(),
  )

  await report_processor._fail_closed_incomplete_snapshot(
    "device-1",
    {"is_complete": True},
    reported_at=datetime.now(timezone.utc),
    failure_kind="SNAPSHOT_IDENTITY_INVALID",
    failure_reason="SNAPSHOT_IDENTITY_MISSING",
  )

  assert calls == [
    ("account-authorized", "SNAPSHOT_IDENTITY_INVALID:SNAPSHOT_IDENTITY_MISSING")
  ]


async def _async_noop(_value):
  return None


async def _async_result(value):
  return value


@pytest.mark.asyncio
async def test_engine_restart_recovers_every_processing_report(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  statements: list[str] = []

  class FakeDatabase:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_):
      return None

    async def execute(self, statement):
      statements.append(str(statement))

    async def commit(self):
      return None

  monkeypatch.setattr(report_processor, "AsyncSessionLocal", FakeDatabase)

  await report_processor._recover_stuck_reports()

  assert len(statements) == 1
  assert "processing_status = :processing_status_1" in statements[0]
  assert "received_at" not in statements[0]
