from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_infrastructure.core import runtime_state_manager as state_manager_module
from quantx_infrastructure.core.runtime_state_manager import (
  RuntimeStateManager,
  RuntimeStateRestoreError,
)
from quantx_infrastructure.repositories.trade_intent_repository import (
  TradeIntentRepository,
)


def _record(*, account_id: str = "account-1", run_id: str = "run-1"):
  metadata = {
    "account_id": account_id,
    "t_trade_role": "entry",
    "execution_mode": "MANUAL_CONFIRM",
    "opportunity_schema_version": 3,
    "candidate_id": "candidate-1",
    "candidate_fingerprint": "fingerprint-1",
    "candidate_state_version": 7,
    "approval_ttl_ms": 30_000,
  }
  values = {
    "id": "intent-1",
    "strategy_run_id": run_id,
    "account_id": account_id,
    "strategy_id": "1",
    "instrument_code": "600000.SH",
    "direction": "BUY",
    "bucket": "swing",
    "reason": "T_TRADE_PULLBACK_REBOUND_ENTRY",
    "priority": "NORMAL",
    "intent_type": None,
    "confidence": 1.0,
    "target_amount": 10_000.0,
    "target_position_pct": None,
    "target_volume": None,
    "limit_price_hint": 10.0,
    "trace_id": "trace-1",
    "status": "PENDING",
    "intent_metadata": metadata,
    "created_at": datetime(2026, 8, 23, 10, 0),
  }
  record = SimpleNamespace(**values)
  record.to_dict = lambda: {
    **values,
    "metadata": dict(metadata),
  }
  return record


async def _single_session(db):
  yield db


def _v3_intent(intent_id: str) -> SimpleNamespace:
  return SimpleNamespace(
    intent_id=intent_id,
    run_id="run-1",
    strategy_id="strategy-1",
    instrument_code="600000.SH",
    direction="BUY",
    bucket="swing",
    reason="T_TRADE_PULLBACK_REBOUND_ENTRY",
    priority="NORMAL",
    confidence=1.0,
    metadata={
      "account_id": "account-1",
      "candidate_id": f"candidate-{intent_id}",
      "opportunity_schema_version": 3,
      "t_trade_role": "entry",
    },
  )


def _durable_v3_intent_record() -> SimpleNamespace:
  executed_time = datetime(2026, 8, 23, 10, 1)
  snapshot = {
    "id": "late-fill-intent",
    "strategy_run_id": "run-1",
    "account_id": "account-1",
    "strategy_id": "strategy-1",
    "instrument_code": "600000.SH",
    "direction": "BUY",
    "bucket": "swing",
    "reason": "T_TRADE_PULLBACK_REBOUND_ENTRY",
    "priority": "NORMAL",
    "confidence": 1.0,
    "status": "FILLED",
    "executed_price": 10.0,
    "executed_volume": 600,
    "executed_time": executed_time.isoformat(),
    "metadata": {
      "account_id": "account-1",
      "candidate_id": "candidate-late-fill",
      "candidate_fingerprint": "fingerprint-late-fill",
      "opportunity_schema_version": 3,
      "t_trade_role": "entry",
    },
  }
  record = SimpleNamespace(
    id=snapshot["id"],
    strategy_run_id=snapshot["strategy_run_id"],
    executed_time=executed_time,
  )
  record.to_dict = lambda: {
    **snapshot,
    "metadata": dict(snapshot["metadata"]),
  }
  return record


@pytest.mark.asyncio
@pytest.mark.parametrize("serialized_parameters", [False, True])
async def test_v3_recovery_loader_validates_account_and_run_scope(
  monkeypatch: pytest.MonkeyPatch,
  serialized_parameters: bool,
) -> None:
  record = _record()
  db = SimpleNamespace()

  async def get(_model, key):
    assert key == "run-1"
    parameters = {"account_id": "account-1"}
    return SimpleNamespace(
      parameters=json.dumps(parameters) if serialized_parameters else parameters
    )

  db.get = get
  monkeypatch.setattr(
    "quantx_infrastructure.database.connection.get_async_db",
    lambda: _single_session(db),
  )

  seen: dict[str, object] = {}

  async def find(_repo, run_id, *, linked_intent_ids=None):
    seen.update(run_id=run_id, linked_intent_ids=linked_intent_ids)
    return [record]

  monkeypatch.setattr(
    TradeIntentRepository,
    "find_v3_manual_candidate_recovery_intents",
    find,
  )
  manager = RuntimeStateManager(run_id="run-1")

  restored = await manager.restore_v3_manual_candidate_intents(
    account_id="account-1",
    linked_intent_ids=["intent-1"],
  )

  assert seen == {"run_id": "run-1", "linked_intent_ids": ["intent-1"]}
  assert len(restored) == 1
  assert restored[0].durable_status == "PENDING"
  assert restored[0].intent.run_id == "run-1"
  assert restored[0].intent.metadata["account_id"] == "account-1"
  persisted = manager._trade_intent_record_data(
    restored[0].intent,
    status="PENDING",
  )
  assert persisted["account_id"] == "account-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "run_account,row_account",
  [("another-account", "account-1"), ("account-1", "another-account")],
)
async def test_v3_recovery_loader_fails_closed_on_ownership_mismatch(
  monkeypatch: pytest.MonkeyPatch,
  run_account: str,
  row_account: str,
) -> None:
  db = SimpleNamespace(
    get=lambda _model, _key: SimpleNamespace(
      parameters={"account_id": run_account}
    )
  )

  async def get(_model, _key):
    return SimpleNamespace(parameters={"account_id": run_account})

  db.get = get
  monkeypatch.setattr(
    "quantx_infrastructure.database.connection.get_async_db",
    lambda: _single_session(db),
  )

  async def find(_repo, _run_id, *, linked_intent_ids=None):
    del linked_intent_ids
    return [_record(account_id=row_account)]

  monkeypatch.setattr(
    TradeIntentRepository,
    "find_v3_manual_candidate_recovery_intents",
    find,
  )
  manager = RuntimeStateManager(run_id="run-1")

  with pytest.raises(RuntimeStateRestoreError, match="账户|所有权"):
    await manager.restore_v3_manual_candidate_intents(account_id="account-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("raw_parameters", "message"),
  [
    ("{", "有效 JSON 对象"),
    (json.dumps(["not", "an", "object"]), "不是 JSON 对象"),
    (["not", "an", "object"], "不是 JSON 对象"),
  ],
)
async def test_v3_recovery_loader_rejects_invalid_or_non_object_strategy_run_parameters(
  monkeypatch: pytest.MonkeyPatch,
  raw_parameters: object,
  message: str,
) -> None:
  db = SimpleNamespace()

  async def get(_model, _key):
    return SimpleNamespace(parameters=raw_parameters)

  db.get = get
  monkeypatch.setattr(
    "quantx_infrastructure.database.connection.get_async_db",
    lambda: _single_session(db),
  )

  manager = RuntimeStateManager(run_id="run-1")

  with pytest.raises(RuntimeStateRestoreError, match=message):
    await manager.restore_v3_manual_candidate_intents(account_id="account-1")


def test_v3_runtime_outboxes_are_bounded_manager_owned_and_idempotent() -> None:
  manager = RuntimeStateManager(run_id="run-1", persist_enabled=False)
  manager.update_custom_state({"strategy_owned": {"value": 1}})
  material = {
    "event_key": "material-1",
    "record_kind": "MATERIAL",
    "event_type": "CANDIDATE_LATCHED",
  }
  paper_fill = {
    "fact_key": "paper-fill:run-1:trade-1",
    "schema_version": 1,
    "price": 10.0,
  }

  manager.enqueue_t_trade_material_events([material, material])
  manager.enqueue_t_trade_paper_fill_fact(paper_fill)

  assert manager.pending_t_trade_material_events() == [material]
  assert manager.pending_t_trade_paper_fill_facts() == [paper_fill]
  assert manager.get_strategy_custom_state() == {
    "strategy_owned": {"value": 1}
  }

  returned = manager.pending_t_trade_material_events()
  returned[0]["event_type"] = "MUTATED"
  assert manager.pending_t_trade_material_events() == [material]

  manager.acknowledge_t_trade_material_events(["material-1"])
  manager.acknowledge_t_trade_paper_fill_facts([paper_fill["fact_key"]])
  assert manager.pending_t_trade_material_events() == []
  assert manager.pending_t_trade_paper_fill_facts() == []


def test_v3_runtime_outboxes_reject_invalid_or_over_capacity_payloads() -> None:
  manager = RuntimeStateManager(run_id="run-1", persist_enabled=False)

  with pytest.raises(ValueError, match="event_key"):
    manager.enqueue_t_trade_material_events([{"event_type": "INVALID"}])
  with pytest.raises(ValueError, match="有限 JSON"):
    manager.enqueue_t_trade_paper_fill_fact(
      {"fact_key": "paper-fill:invalid", "price": float("nan")}
    )

  manager.enqueue_t_trade_material_events(
    [
      {
        "event_key": f"material-{index}",
        "record_kind": "MATERIAL",
      }
      for index in range(8_192)
    ]
  )
  with pytest.raises(RuntimeError, match="8192"):
    manager.enqueue_t_trade_material_events(
      [{"event_key": "material-overflow", "record_kind": "MATERIAL"}]
    )


@pytest.mark.asyncio
async def test_v3_terminal_intent_cache_is_bounded_without_evicting_active_intents(
) -> None:
  stored_events: list[dict[str, object]] = []
  backtest_storage = SimpleNamespace(
    add_trade_intent=lambda payload: stored_events.append(dict(payload))
  )
  manager = RuntimeStateManager(
    run_id="run-1",
    persist_enabled=False,
    is_backtest=True,
    _backtest_storage=backtest_storage,
  )
  active_statuses = {
    "active-pending": "PENDING",
    "active-approval": "AWAITING_APPROVAL",
    "active-approved": "APPROVED",
    # An unknown future status is retained fail-safe rather than guessed to be
    # terminal and silently losing in-flight execution truth.
    "active-future": "FUTURE_ACTIVE_STATE",
  }
  for intent_id, status in active_statuses.items():
    await manager.record_trade_intent(_v3_intent(intent_id), status=status)

  cumulative_id = "active-partial-fill"
  await manager.record_trade_intent(_v3_intent(cumulative_id), status="PENDING")
  await manager.update_trade_intent_status(
    cumulative_id,
    "PARTIAL_FILLED",
    executed_price=10.0,
    executed_volume=600,
    accumulate_executed_volume=True,
  )

  terminal_limit = state_manager_module._MAX_TERMINAL_TRADE_INTENT_CACHE_ENTRIES
  terminal_count = terminal_limit + 7
  for index in range(terminal_count):
    await manager.record_trade_intent(
      _v3_intent(f"terminal-{index}"),
      status="REJECTED",
    )

  cache = manager._state["trade_intents"]
  assert set(active_statuses).issubset(cache)
  assert cumulative_id in cache
  assert "terminal-0" not in cache
  assert f"terminal-{terminal_count - 1}" in cache
  assert sum(
    item["status"] == "REJECTED" for item in cache.values()
  ) == terminal_limit

  # The active partial fill survived terminal-history eviction, so its final
  # fill still accumulates quantity and weighted execution price correctly.
  await manager.update_trade_intent_status(
    cumulative_id,
    "FILLED",
    executed_price=10.2,
    executed_volume=400,
    accumulate_executed_volume=True,
  )

  cached_fill = manager._state["trade_intents"][cumulative_id]
  assert cached_fill["executed_volume"] == 1_000
  assert cached_fill["executed_price"] == pytest.approx(10.08)
  assert set(active_statuses).issubset(manager._state["trade_intents"])
  assert len(manager._state["trade_intents"]) == terminal_limit + len(
    active_statuses
  )
  assert len(stored_events) == len(active_statuses) + terminal_count + 3


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_late_fill_after_terminal_lru_eviction_reloads_complete_durable_truth(
  monkeypatch: pytest.MonkeyPatch,
  strict: bool,
) -> None:
  record = _durable_v3_intent_record()
  manager = RuntimeStateManager(run_id="run-1", persist_enabled=True)
  manager._cache_trade_intent(record.to_dict())
  terminal_limit = state_manager_module._MAX_TERMINAL_TRADE_INTENT_CACHE_ENTRIES
  for index in range(terminal_limit):
    manager._cache_trade_intent(
      {"id": f"newer-terminal-{index}", "status": "REJECTED"}
    )
  assert record.id not in manager._state["trade_intents"]

  db = SimpleNamespace()
  monkeypatch.setattr(
    "quantx_infrastructure.database.connection.get_async_db",
    lambda: _single_session(db),
  )
  find_calls = 0

  async def find(_repo, intent_id):
    nonlocal find_calls
    find_calls += 1
    assert intent_id == record.id
    return record

  updated: dict[str, object] = {}

  async def update(_repo, intent_id, payload):
    assert intent_id == record.id
    updated.update(payload)
    return record

  monkeypatch.setattr(TradeIntentRepository, "find_by_id", find)
  monkeypatch.setattr(TradeIntentRepository, "update_intent", update)

  updater = (
    manager.update_trade_intent_status_strict
    if strict
    else manager.update_trade_intent_status
  )
  await updater(
    record.id,
    "FILLED",
    executed_price=10.2,
    executed_volume=400,
    executed_time=datetime(2026, 8, 23, 10, 2),
    accumulate_executed_volume=True,
  )

  assert find_calls == 2
  assert updated["strategy_run_id"] == "run-1"
  assert updated["metadata"] == record.to_dict()["metadata"]
  assert updated["executed_volume"] == 1_000
  assert updated["executed_price"] == pytest.approx(10.08)
  cached = manager._state["trade_intents"][record.id]
  assert cached["metadata"]["candidate_fingerprint"] == "fingerprint-late-fill"
  assert cached["executed_volume"] == 1_000
  assert cached["executed_price"] == pytest.approx(10.08)
  assert len(manager._state["trade_intents"]) == terminal_limit


@pytest.mark.asyncio
async def test_cache_miss_never_writes_incomplete_persistent_intent(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  db = SimpleNamespace()
  monkeypatch.setattr(
    "quantx_infrastructure.database.connection.get_async_db",
    lambda: _single_session(db),
  )

  async def find(_repo, _intent_id):
    return None

  async def unexpected_update(*_args, **_kwargs):
    raise AssertionError("durable update must not run without a complete row")

  monkeypatch.setattr(TradeIntentRepository, "find_by_id", find)
  monkeypatch.setattr(TradeIntentRepository, "update_intent", unexpected_update)
  manager = RuntimeStateManager(run_id="run-1", persist_enabled=True)

  await manager.update_trade_intent_status(
    "missing-intent",
    "FILLED",
    executed_volume=100,
    accumulate_executed_volume=True,
  )
  assert "missing-intent" not in manager._state["trade_intents"]

  with pytest.raises(RuntimeStateRestoreError, match="持久化记录不存在"):
    await manager.update_trade_intent_status_strict(
      "missing-intent",
      "FILLED",
      executed_volume=100,
      accumulate_executed_volume=True,
    )
  assert "missing-intent" not in manager._state["trade_intents"]
