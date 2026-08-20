from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


@pytest.fixture
def snapshot_dependencies(monkeypatch):
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  state_repo_calls: list[int] = []
  state_repo_results: list[bool] = []
  deleted_position_snapshots: list[tuple[str, list[str], bool]] = []

  class FakeDb:
    def __init__(self):
      self.commit_calls = 0

    async def commit(self):
      self.commit_calls += 1

  fake_db = FakeDb()

  async def fake_get_async_db():
    yield fake_db

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def upsert_state(self, **kwargs):
      state_repo_calls.append(kwargs["expected_version"])
      return state_repo_results.pop(0)

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def update_position(self, **_kwargs):
      return None

    async def delete_missing_positions(
      self,
      run_id,
      instrument_codes,
      *,
      commit=True,
    ):
      deleted_position_snapshots.append(
        (run_id, list(instrument_codes), commit)
      )

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )
  return (
    state_repo_calls,
    state_repo_results,
    deleted_position_snapshots,
    fake_db,
  )


@pytest.mark.asyncio
async def test_snapshot_advances_optimistic_lock_version(snapshot_dependencies):
  calls, results, _deleted, fake_db = snapshot_dependencies
  results.extend([True, True])
  manager = RuntimeStateManager(run_id="run-1", persist_enabled=True)
  manager._state["version"] = 7

  manager._dirty = True
  assert await manager.save_snapshot() is True
  manager._dirty = True
  assert await manager.save_snapshot() is True

  assert calls == [7, 8]
  assert manager._state["version"] == 9
  assert manager._dirty is False
  assert fake_db.commit_calls == 2


@pytest.mark.asyncio
async def test_state_repository_compare_and_swap_allows_only_one_session(
  tmp_path,
) -> None:
  from quantx_infrastructure.database.relational_base import Base
  from quantx_infrastructure.models.strategy_run_state import StrategyRunState
  from quantx_infrastructure.repositories.strategy_run_state_repository import (
    StrategyRunStateRepository,
  )
  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

  database_path = (tmp_path / "runtime-state-cas.sqlite3").as_posix()
  engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[StrategyRunState.__table__],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    assert await StrategyRunStateRepository(db).upsert_state(
      run_id="run-cas",
      custom_state={"winner": "seed"},
      expected_version=0,
    )

  async with sessions() as first_db, sessions() as second_db:
    first_repo = StrategyRunStateRepository(first_db)
    second_repo = StrategyRunStateRepository(second_db)
    assert (await first_repo.get_state("run-cas")).version == 1
    assert (await second_repo.get_state("run-cas")).version == 1

    first_saved = await first_repo.upsert_state(
      run_id="run-cas",
      custom_state={"winner": "first"},
      expected_version=1,
    )
    second_saved = await second_repo.upsert_state(
      run_id="run-cas",
      custom_state={"winner": "second"},
      expected_version=1,
    )

  assert first_saved is True
  assert second_saved is False
  async with sessions() as db:
    authoritative = await StrategyRunStateRepository(db).get_state("run-cas")
    assert authoritative.version == 2
    assert authoritative.custom_state == {"winner": "first"}
  await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_keeps_dirty_state_after_version_conflict(
  snapshot_dependencies,
):
  calls, results, _deleted, fake_db = snapshot_dependencies
  results.append(False)
  manager = RuntimeStateManager(run_id="run-2", persist_enabled=True)
  manager._state["version"] = 4
  manager._dirty = True

  assert await manager.save_snapshot() is False

  assert calls == [4]
  assert manager._state["version"] == 4
  assert manager._dirty is True
  assert fake_db.commit_calls == 0


@pytest.mark.asyncio
async def test_snapshot_deletes_all_persisted_positions_when_memory_is_empty(
  snapshot_dependencies,
):
  _calls, results, deleted, fake_db = snapshot_dependencies
  results.append(True)
  manager = RuntimeStateManager(run_id="run-empty-position", persist_enabled=True)
  manager._state["positions"] = {}
  manager._dirty = True

  assert await manager.save_snapshot() is True

  assert deleted == [("run-empty-position", [], False)]
  assert fake_db.commit_calls == 1


@pytest.mark.asyncio
async def test_snapshot_does_not_clear_change_created_during_database_write(
  monkeypatch,
):
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  write_started = asyncio.Event()
  allow_write = asyncio.Event()

  class FakeDb:
    async def commit(self):
      return None

  async def fake_get_async_db():
    yield FakeDb()

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def upsert_state(self, **_kwargs):
      write_started.set()
      await allow_write.wait()
      return True

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def delete_missing_positions(self, *_args, **_kwargs):
      return None

    async def update_position(self, **_kwargs):
      return None

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )

  manager = RuntimeStateManager(run_id="run-concurrent-dirty", persist_enabled=True)
  manager.update_custom_state({"before": 1})
  save_task = asyncio.create_task(manager.save_snapshot())
  await write_started.wait()
  manager.update_custom_state({"after": 2})
  allow_write.set()

  assert await save_task is True
  assert manager._state["version"] == 1
  assert manager._dirty is True


@pytest.mark.asyncio
async def test_durable_marker_retries_checkpoint_after_first_save_failure(
  monkeypatch,
):
  manager = RuntimeStateManager(run_id="run-marker-retry", persist_enabled=True)
  outcomes = iter([False, True])
  save_calls = 0

  async def fake_save_snapshot():
    nonlocal save_calls
    save_calls += 1
    return next(outcomes)

  async def marker_not_committed(_event_key):
    return False

  monkeypatch.setattr(manager, "save_snapshot", fake_save_snapshot)
  monkeypatch.setattr(
    manager,
    "_adopt_committed_runtime_event",
    marker_not_committed,
  )

  assert await manager.checkpoint_durable_runtime_event("trade:event-1") is False
  assert manager.has_applied_runtime_event("trade:event-1") is True
  assert await manager.checkpoint_durable_runtime_event("trade:event-1") is True
  assert save_calls == 2


@pytest.mark.asyncio
async def test_checkpoint_adopts_authoritative_marker_after_commit_unknown(
  monkeypatch,
):
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  authoritative = SimpleNamespace(
    version=3,
    cash=9_000.0,
    frozen_cash=0.0,
    total_asset=10_000.0,
    custom_state={},
  )
  authoritative_position = SimpleNamespace(
    instrument_code="600000.SH",
    to_dict=lambda: {
      "instrument_code": "600000.SH",
      "long_volume": 100,
      "short_volume": 0,
      "long_avg_price": 10.0,
      "short_avg_price": 0.0,
      "market_value": 1_000.0,
      "pnl": 0.0,
      "last_price": 10.0,
    },
  )

  async def fake_get_async_db():
    yield object()

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def get_state(self, _run_id):
      return authoritative

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def get_all_positions(self, _run_id):
      return [authoritative_position]

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )

  manager = RuntimeStateManager(run_id="run-commit-unknown", persist_enabled=True)

  async def commit_then_raise_equivalent():
    manager._last_snapshot_attempt_revision = manager._dirty_revision
    authoritative.custom_state = {
      "applied_runtime_event_keys": ["trade:committed-event"],
      "bucket_ledger_snapshot": {
        "run_id": "run-commit-unknown",
        "instruments": {
          "600000.SH": {
            "core": {
              "bucket": "core",
              "total_volume": 70,
              "available_volume": 40,
              "frozen_volume": 10,
              "today_buy_volume": 20,
              "market_value": 700.0,
              "avg_price": 10.0,
              "last_price": 10.0,
            },
            "swing": {
              "bucket": "swing",
              "total_volume": 30,
              "available_volume": 10,
              "frozen_volume": 5,
              "today_buy_volume": 10,
              "market_value": 300.0,
              "avg_price": 10.0,
              "last_price": 10.0,
            },
          }
        },
        "pending_orders": {},
        "pending_substitutions": {},
      },
    }
    return False

  monkeypatch.setattr(manager, "save_snapshot", commit_then_raise_equivalent)

  assert (
    await manager.checkpoint_durable_runtime_event("trade:committed-event")
    is True
  )
  assert manager._state["version"] == 3
  assert manager.get_account()["cash"] == pytest.approx(9_000.0)
  adopted_position = manager.get_position("600000.SH")
  assert adopted_position["long_volume"] == 100
  assert adopted_position["available_volume"] == 50
  assert adopted_position["frozen_volume"] == 15
  assert adopted_position["today_buy_volume"] == 30
  assert manager.has_applied_runtime_event("trade:committed-event")
  assert manager._dirty is False


@pytest.mark.asyncio
async def test_generic_snapshot_adopts_commit_unknown_token_and_saves_next_change(
  monkeypatch,
) -> None:
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  authoritative = SimpleNamespace(
    version=0,
    cash=0.0,
    frozen_cash=0.0,
    total_asset=0.0,
    custom_state={},
  )
  commit_calls = 0
  expected_versions: list[int] = []

  class FakeDb:
    async def commit(self):
      nonlocal commit_calls
      commit_calls += 1
      if commit_calls == 1:
        raise RuntimeError("connection lost after commit")

  fake_db = FakeDb()

  async def fake_get_async_db():
    yield fake_db

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def upsert_state(self, **kwargs):
      expected_versions.append(kwargs["expected_version"])
      authoritative.version = kwargs["expected_version"] + 1
      authoritative.cash = kwargs["cash"]
      authoritative.frozen_cash = kwargs["frozen_cash"]
      authoritative.total_asset = kwargs["total_asset"]
      authoritative.custom_state = dict(kwargs["custom_state"])
      return True

    async def get_state(self, _run_id):
      return authoritative

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def delete_missing_positions(self, *_args, **_kwargs):
      return None

    async def update_position(self, **_kwargs):
      return None

    async def get_all_positions(self, _run_id):
      return []

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )

  manager = RuntimeStateManager(
    run_id="run-generic-commit-unknown",
    persist_enabled=True,
  )
  manager.update_custom_state({"signal_window": [1]})
  assert await manager.save_snapshot() is True
  assert manager._state["version"] == 1
  assert manager._dirty is False

  manager.update_custom_state({"signal_window": [1, 2]})
  assert await manager.save_snapshot() is True
  assert expected_versions == [0, 1]
  assert manager._state["version"] == 2
  assert manager._dirty is False


@pytest.mark.asyncio
async def test_checkpoint_does_not_replace_manager_markers_from_strategy_snapshot():
  manager = RuntimeStateManager(
    run_id="run-protected-marker",
    persist_enabled=False,
  )
  manager._state["custom"] = {
    "applied_runtime_event_keys": ["trade:already-applied"],
    "order_cash_reservations": {"order-1": 100.0},
  }

  assert await manager.checkpoint_durable_runtime_event(
    "trade:new-event",
    custom_updates={
      "applied_runtime_event_keys": [],
      "order_cash_reservations": {},
      "strategy_value": 42,
    },
  )

  assert manager._state["custom"]["applied_runtime_event_keys"] == [
    "trade:already-applied",
    "trade:new-event",
  ]
  assert manager._state["custom"]["order_cash_reservations"] == {
    "order-1": 100.0
  }
  assert manager.get_custom("strategy_value") == 42


@pytest.mark.asyncio
async def test_stop_state_sync_drains_latest_strategy_checkpoint():
  class FakeStrategy:
    def __init__(self):
      self.queue = asyncio.Queue()

    def subscribe_state(self):
      return self.queue

    def unsubscribe_state(self, queue):
      assert queue is self.queue

  strategy = FakeStrategy()
  manager = RuntimeStateManager(run_id="run-state-drain", persist_enabled=False)
  await manager.start()
  await manager.start_state_sync(strategy)
  strategy.queue.put_nowait(
    SimpleNamespace(
      persist=True,
      changes={
        "signal_sample_windows": {
          "version": 1,
          "instruments": {"600000.SH": [[1, 10.0, 9.99, 10.0, 100.0, 10.0]]},
        }
      },
      key=None,
      value=None,
    )
  )

  await manager.stop_state_sync(strategy)

  assert manager.get_custom("signal_sample_windows")["version"] == 1
  assert strategy.queue.empty()
  await manager.stop()
