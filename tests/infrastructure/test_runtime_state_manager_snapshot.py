from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.core.runtime_state_manager import (
  GRID_BOOK_CUSTOM_STATE_KEY,
  RUNTIME_RECONCILIATION_STATUS_KEY,
  T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY,
  RuntimeStateManager,
  RuntimeStateRestoreResult,
  RuntimeStateRestoreStatus,
)


@pytest.fixture
def snapshot_dependencies(monkeypatch):
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  state_repo_calls: list[int] = []
  state_repo_results: list[bool] = []
  deleted_position_snapshots: list[tuple[str, list[str], bool]] = []
  updated_position_snapshots: list[tuple[str, list[str], bool]] = []

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

    async def replace_positions_snapshot(
      self,
      run_id,
      positions,
      *,
      commit=True,
      flush=True,
    ):
      deleted_position_snapshots.append(
        (run_id, list(positions), commit)
      )

    async def update_existing_positions_snapshot(
      self,
      run_id,
      positions,
      *,
      commit=True,
      flush=True,
    ):
      updated_position_snapshots.append(
        (run_id, list(positions), commit)
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
    updated_position_snapshots,
  )


@pytest.mark.asyncio
async def test_snapshot_advances_optimistic_lock_version(snapshot_dependencies):
  calls, results, _deleted, fake_db, _updated = snapshot_dependencies
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
async def test_snapshot_keeps_tick_cas_but_skips_unchanged_position_projection(
  snapshot_dependencies,
) -> None:
  calls, results, replacements, fake_db, incremental_updates = snapshot_dependencies
  results.extend([True, True])
  manager = RuntimeStateManager(run_id="run-position-skip", persist_enabled=True)
  manager.update_position("600000.SH", long_volume=100, last_price=10.0)

  assert await manager.save_snapshot() is True
  manager.update_custom_state({"candidate": "EVALUATING"})
  assert await manager.save_snapshot() is True

  # The opportunity/custom-state checkpoint remains a distinct CAS commit on
  # every tick, while an equal structured position projection performs no
  # DELETE/UPDATE position round trip.
  assert calls == [0, 1]
  assert manager._state["version"] == 2
  assert replacements == [("run-position-skip", ["600000.SH"], False)]
  assert incremental_updates == []
  assert fake_db.commit_calls == 2


@pytest.mark.asyncio
async def test_snapshot_updates_same_code_when_position_values_change(
  snapshot_dependencies,
) -> None:
  _calls, results, replacements, _fake_db, incremental_updates = (
    snapshot_dependencies
  )
  results.extend([True, True])
  manager = RuntimeStateManager(run_id="run-position-value-change", persist_enabled=True)
  manager.update_position("600000.SH", long_volume=100, last_price=10.0)

  assert await manager.save_snapshot() is True
  manager.update_position("600000.SH", long_volume=200, last_price=11.0)
  assert await manager.save_snapshot() is True

  # Same code-set is insufficient: a durable value change must use the batch
  # update path rather than being skipped.
  assert replacements == [
    ("run-position-value-change", ["600000.SH"], False)
  ]
  assert incremental_updates == [
    ("run-position-value-change", ["600000.SH"], False)
  ]


@pytest.mark.asyncio
async def test_snapshot_replaces_complete_positions_for_addition_and_deletion(
  snapshot_dependencies,
) -> None:
  _calls, results, replacements, _fake_db, incremental_updates = (
    snapshot_dependencies
  )
  results.extend([True, True, True])
  manager = RuntimeStateManager(run_id="run-position-set-change", persist_enabled=True)
  manager.update_position("600000.SH", long_volume=100, last_price=10.0)

  assert await manager.save_snapshot() is True
  manager.update_position("000001.SZ", long_volume=200, last_price=20.0)
  assert await manager.save_snapshot() is True
  manager._state["positions"].pop("600000.SH")
  manager._mark_positions_dirty()
  assert await manager.save_snapshot() is True

  assert replacements == [
    ("run-position-set-change", ["600000.SH"], False),
    ("run-position-set-change", ["600000.SH", "000001.SZ"], False),
    ("run-position-set-change", ["000001.SZ"], False),
  ]
  assert incremental_updates == []


@pytest.mark.asyncio
async def test_snapshot_failure_forces_position_replacement_on_retry(
  snapshot_dependencies,
) -> None:
  calls, results, replacements, _fake_db, incremental_updates = (
    snapshot_dependencies
  )
  results.extend([True, False, True])
  manager = RuntimeStateManager(run_id="run-position-retry", persist_enabled=True)
  manager.update_position("600000.SH", long_volume=100, last_price=10.0)

  assert await manager.save_snapshot() is True
  manager.update_custom_state({"candidate": "LATCHED"})
  assert await manager.save_snapshot() is False
  assert manager._force_position_snapshot is True
  assert await manager.save_snapshot() is True

  assert calls == [0, 1, 1]
  assert replacements == [
    ("run-position-retry", ["600000.SH"], False),
    ("run-position-retry", ["600000.SH"], False),
  ]
  assert incremental_updates == []


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
async def test_position_repository_replaces_complete_snapshot_in_one_transaction(
  tmp_path,
) -> None:
  from quantx_infrastructure.database.relational_base import Base
  from quantx_infrastructure.models.strategy_run_state import StrategyRunPosition
  from quantx_infrastructure.repositories.strategy_run_state_repository import (
    StrategyRunPositionRepository,
  )
  from sqlalchemy import event, select
  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

  database_path = (tmp_path / "runtime-position-snapshot.sqlite3").as_posix()
  engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[StrategyRunPosition.__table__],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)

  async with sessions() as db:
    repository = StrategyRunPositionRepository(db)
    await repository.replace_positions_snapshot(
      "run-position-snapshot",
      {
        "600000.SH": {"long_volume": 100, "last_price": 10.0},
        "000001.SZ": {"long_volume": 200, "last_price": 20.0},
      },
      commit=False,
      flush=False,
    )
    await db.commit()

  async with sessions() as db:
    repository = StrategyRunPositionRepository(db)
    await repository.replace_positions_snapshot(
      "run-position-snapshot",
      {
        "600000.SH": {"long_volume": 300, "last_price": 11.0},
        "300001.SZ": {"long_volume": 400, "last_price": 30.0},
      },
    )
    db.add(
      StrategyRunPosition(
        run_id="run-position-snapshot",
        instrument_code="999999.SH",
        long_volume=100,
        last_price=1.0,
      )
    )
    await db.commit()
    checkpoint_statements: list[str] = []

    def capture_statement(
      _connection,
      _cursor,
      statement,
      _parameters,
      _context,
      _executemany,
    ):
      checkpoint_statements.append(str(statement))

    event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
    try:
      await repository.update_existing_positions_snapshot(
        "run-position-snapshot",
        {
          "600000.SH": {"long_volume": 500, "last_price": 12.0},
          "300001.SZ": {"long_volume": 600, "last_price": 31.0},
        },
      )
    finally:
      event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)
    rows = list(
      (
        await db.execute(
          select(StrategyRunPosition)
          .where(StrategyRunPosition.run_id == "run-position-snapshot")
          .order_by(StrategyRunPosition.instrument_code.asc())
        )
      ).scalars()
    )

  assert [(row.instrument_code, row.long_volume, row.last_price) for row in rows] == [
    ("300001.SZ", 600, 31.0),
    ("600000.SH", 500, 12.0),
  ]
  assert not any(statement.lstrip().upper().startswith("SELECT") for statement in checkpoint_statements)
  await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_manager_bulk_position_snapshot_keeps_tick_checkpoint_cas(
  tmp_path,
  monkeypatch,
) -> None:
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.database.relational_base import Base
  from quantx_infrastructure.models.strategy_run_state import (
    StrategyRunPosition,
    StrategyRunState,
  )
  from sqlalchemy import select
  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

  database_path = (tmp_path / "runtime-manager-bulk-checkpoint.sqlite3").as_posix()
  engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[StrategyRunState.__table__, StrategyRunPosition.__table__],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)

  async def fake_get_async_db():
    async with sessions() as db:
      yield db

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  manager = RuntimeStateManager(run_id="run-bulk-checkpoint", persist_enabled=True)
  manager.update_account(cash=1_000.0, frozen_cash=0.0, total_asset=3_000.0)
  manager.update_position("600000.SH", long_volume=100, last_price=10.0)
  manager.update_position("000001.SZ", long_volume=200, last_price=20.0)

  assert await manager.save_snapshot() is True
  manager.update_position("600000.SH", long_volume=300, last_price=11.0)
  assert await manager.save_snapshot() is True

  async with sessions() as db:
    state = (
      await db.execute(
        select(StrategyRunState).where(StrategyRunState.run_id == "run-bulk-checkpoint")
      )
    ).scalar_one()
    positions = list(
      (
        await db.execute(
          select(StrategyRunPosition)
          .where(StrategyRunPosition.run_id == "run-bulk-checkpoint")
          .order_by(StrategyRunPosition.instrument_code.asc())
        )
      ).scalars()
    )

  assert state.version == 2
  assert [(row.instrument_code, row.long_volume, row.last_price) for row in positions] == [
    ("000001.SZ", 200, 20.0),
    ("600000.SH", 300, 11.0),
  ]
  await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_keeps_dirty_state_after_version_conflict(
  snapshot_dependencies,
):
  calls, results, _deleted, fake_db, _updated = snapshot_dependencies
  results.append(False)
  manager = RuntimeStateManager(run_id="run-2", persist_enabled=True)
  manager._state["version"] = 4
  manager._dirty = True

  assert await manager.save_snapshot() is False

  assert calls == [4]
  assert manager._state["version"] == 4
  assert manager._dirty is True
  assert fake_db.commit_calls == 0
  assert manager.snapshot_cas_conflicts == 1
  assert manager.last_snapshot_failure_code == "CAS_CONFLICT"


@pytest.mark.asyncio
async def test_external_cas_winner_replaces_all_stale_runtime_truth(
  monkeypatch,
) -> None:
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  authoritative = SimpleNamespace(
    version=2,
    cash=8_000.0,
    frozen_cash=500.0,
    total_asset=10_000.0,
    custom_state={
      "instrument_states": {"600000.SH": {"candidate_status": "NONE"}},
      "winner": "external",
    },
  )
  authoritative_position = SimpleNamespace(
    instrument_code="600000.SH",
    to_dict=lambda: {
      "instrument_code": "600000.SH",
      "long_volume": 100,
      "short_volume": 0,
      "available_volume": 80,
      "frozen_volume": 0,
      "today_buy_volume": 20,
      "long_avg_price": 10.0,
      "short_avg_price": 0.0,
      "market_value": 1_000.0,
      "pnl": 0.0,
      "last_price": 10.0,
    },
  )

  position_replacements: list[tuple[str, list[str], bool]] = []

  class FakeDb:
    async def commit(self):
      return None

  fake_db = FakeDb()

  async def fake_get_async_db():
    yield fake_db

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def get_state(self, _run_id):
      return authoritative

    async def upsert_state(self, **kwargs):
      assert kwargs["expected_version"] == 2
      return True

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def get_all_positions(self, _run_id):
      return [authoritative_position]

    async def replace_positions_snapshot(
      self,
      run_id,
      positions,
      *,
      commit=True,
      flush=True,
    ):
      position_replacements.append((run_id, list(positions), commit))

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

  manager = RuntimeStateManager(run_id="run-external-cas", persist_enabled=True)
  manager._state["version"] = 1
  manager._state["custom"] = {
    "instrument_states": {"000001.SZ": {"candidate_status": "LATCHED"}},
    "order_cash_reservations": {"stale-order": 100.0},
  }
  manager._state["positions"] = {
    "000001.SZ": {
      "instrument_code": "000001.SZ",
      "long_volume": 100,
      "available_volume": 100,
      "last_price": 9.0,
    }
  }
  manager._restore_reservation_state()
  manager._bucket_ledger.sync_position(
    "000001.SZ",
    manager._state["positions"]["000001.SZ"],
  )
  manager._dirty = True
  manager._dirty_revision = 3

  adopted_own_attempt = await manager._reconcile_snapshot_attempt(
    "losing-token",
    snapshot_revision=3,
    expected_version=1,
  )

  assert adopted_own_attempt is False
  assert manager._state["version"] == 2
  assert manager.get_custom_state() == authoritative.custom_state
  assert "000001.SZ" not in manager.get_all_positions()
  assert manager.get_position("600000.SH")["long_volume"] == 100
  assert manager._persisted_position_codes == frozenset({"600000.SH"})
  account = manager.get_account()
  assert account["cash"] == 8_000.0
  assert account["frozen_cash"] == 500.0
  assert account["total_asset"] == 10_000.0
  ledger = manager.get_bucket_ledger_snapshot()
  assert set(ledger["instruments"]) == {"600000.SH"}
  assert manager._reservations == {}
  assert manager._position_reservations == {}
  assert manager._dirty is False
  assert manager._force_position_snapshot is True

  # A winning external CAS is recovery truth, not a license to trust a stale
  # local position cache.  The next normal checkpoint must replace all rows.
  manager.update_custom_state({"next": "checkpoint"})
  assert await manager.save_snapshot() is True
  assert position_replacements == [
    ("run-external-cas", ["600000.SH"], False)
  ]


@pytest.mark.asyncio
async def test_snapshot_deletes_all_persisted_positions_when_memory_is_empty(
  snapshot_dependencies,
):
  _calls, results, deleted, fake_db, _updated = snapshot_dependencies
  results.append(True)
  manager = RuntimeStateManager(run_id="run-empty-position", persist_enabled=True)
  manager._state["positions"] = {}
  manager._dirty = True

  assert await manager.save_snapshot() is True

  assert deleted == [("run-empty-position", [], False)]
  assert fake_db.commit_calls == 1


@pytest.mark.asyncio
async def test_restore_forces_full_position_snapshot_on_first_checkpoint(
  monkeypatch,
) -> None:
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  durable_state = SimpleNamespace(
    version=6,
    cash=1_000.0,
    frozen_cash=0.0,
    total_asset=2_000.0,
    custom_state={},
  )
  durable_position = SimpleNamespace(
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
  replacements: list[tuple[str, list[str], bool]] = []

  class FakeDb:
    async def commit(self):
      return None

  fake_db = FakeDb()

  async def fake_get_async_db():
    yield fake_db

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def get_state(self, _run_id):
      return durable_state

    async def upsert_state(self, **kwargs):
      assert kwargs["expected_version"] == 6
      return True

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def get_all_positions(self, _run_id):
      return [durable_position]

    async def replace_positions_snapshot(
      self,
      run_id,
      positions,
      *,
      commit=True,
      flush=True,
    ):
      replacements.append((run_id, list(positions), commit))

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

  manager = RuntimeStateManager(run_id="run-restore-force", persist_enabled=True)
  restored = await manager.restore()

  assert restored.status is RuntimeStateRestoreStatus.RESTORED
  assert manager._force_position_snapshot is True
  manager.update_custom_state({"candidate": "RESTORED"})
  assert await manager.save_snapshot() is True
  assert replacements == [
    ("run-restore-force", ["600000.SH"], False)
  ]


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

    async def replace_positions_snapshot(self, *_args, **_kwargs):
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
  position_replacements: list[tuple[str, list[str], bool]] = []

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

    async def replace_positions_snapshot(
      self,
      run_id,
      positions,
      *,
      commit=True,
      flush=True,
    ):
      position_replacements.append((run_id, list(positions), commit))

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
  assert manager._force_position_snapshot is True

  manager.update_custom_state({"signal_window": [1, 2]})
  assert await manager.save_snapshot() is True
  assert expected_versions == [0, 1]
  assert manager._state["version"] == 2
  assert manager._dirty is False
  assert position_replacements == [
    ("run-generic-commit-unknown", [], False),
    ("run-generic-commit-unknown", [], False),
  ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "crash_boundary",
  [
    "PREPARED_COMMITTED",
    "MATERIALIZED_BEFORE_FINALIZE",
    "FINALIZE_KNOWN_FAILURE",
    "FINALIZE_COMMIT_UNKNOWN",
  ],
)
async def test_prepared_checkpoint_crash_boundaries_preserve_replayable_state(
  crash_boundary: str,
) -> None:
  """Every PREPARED/finalize crash boundary has one safe durable outcome."""

  manager = RuntimeStateManager(
    run_id="run-checkpoint-recovery",
    persist_enabled=True,
    is_backtest=True,
  )
  durable_state: dict[str, object] = {}

  def install_durable_save(
    target: RuntimeStateManager,
    *,
    persist: bool = True,
    result: bool = True,
  ) -> None:
    async def fake_save_snapshot() -> bool:
      if persist:
        target._dirty = False
        target._state["version"] += 1
        durable_state["state"] = copy.deepcopy(target._state)
      return result

    target.save_snapshot = fake_save_snapshot  # type: ignore[method-assign]

  install_durable_save(manager)
  manager.update_account(cash=1_000.0, frozen_cash=0.0, total_asset=1_000.0)
  manager.update_position("600000.SH", long_volume=100, last_price=10.0)
  manager.update_custom_state({"strategy_window": {"samples": [1, 2, 3]}})
  checkpoint = await manager.seal_checkpoint(
    trade_date="2026-08-20",
    session=None,
    boundary_source_time=datetime(2026, 8, 20, 15, 0),
    processed_watermark={"stream_id": "backtest", "sequence": 100},
    continuity_generation=1,
    completeness={"complete": True},
  )

  assert checkpoint is not None
  complete_payload = copy.deepcopy(checkpoint.state_payload)
  # An immediate account/position fact after the last COMPLETE checkpoint must
  # survive a later prepared handoff.  The intact prepared state is recovery
  # truth; startup replays its outbox and only then FINALIZEs it.
  manager.update_account(cash=900.0, frozen_cash=100.0, total_asset=1_010.0)
  manager.update_position("600000.SH", long_volume=120, last_price=10.5)
  manager.update_custom_state({"strategy_window": {"samples": [1, 2, 3, 4]}})
  prepared = await manager.prepare_checkpoint(
    trade_date="2026-08-21",
    session=None,
    boundary_source_time=datetime(2026, 8, 21, 15, 0),
    processed_watermark={"stream_id": "backtest", "sequence": 200},
    continuity_generation=1,
    completeness={"complete": True},
    materialization_events=[{"event_key": "diagnostic:prepared"}],
  )
  assert prepared is not None
  assert isinstance(durable_state.get("state"), dict)

  def restored_from_durable() -> RuntimeStateManager:
    restored = RuntimeStateManager(
      run_id="run-checkpoint-recovery",
      persist_enabled=True,
      is_backtest=True,
    )
    restored._state = copy.deepcopy(durable_state["state"])
    return restored

  receipt_keys = ["diagnostic:prepared"]
  if crash_boundary == "FINALIZE_KNOWN_FAILURE":
    failed_finalizer = restored_from_durable()
    install_durable_save(failed_finalizer, persist=False, result=False)
    assert await failed_finalizer.finalize_prepared_checkpoint(
      prepared_checkpoint_id=prepared.checkpoint_id,
      materialization_event_keys=receipt_keys,
    ) is None
    assert failed_finalizer.latest_prepared_checkpoint() is not None
  elif crash_boundary == "FINALIZE_COMMIT_UNKNOWN":
    unknown_finalizer = restored_from_durable()
    # The DB committed, but the commit response was unavailable.  A fresh
    # owner follows durable truth and must not replay a vanished outbox.
    install_durable_save(unknown_finalizer, persist=True, result=False)
    assert await unknown_finalizer.finalize_prepared_checkpoint(
      prepared_checkpoint_id=prepared.checkpoint_id,
      materialization_event_keys=receipt_keys,
    ) is None
    recovered = restored_from_durable()
    assert recovered.has_prepared_checkpoint() is False
    finalized = recovered.latest_complete_checkpoint(
      trade_date="2026-08-21",
      session=None,
    )
    assert finalized is not None and finalized.complete
    assert recovered.pending_t_trade_diagnostic_events() == []
    return

  restored = restored_from_durable()
  install_durable_save(restored)
  recovered = restored.latest_prepared_checkpoint()

  assert recovered is not None
  assert recovered.checkpoint_id == prepared.checkpoint_id
  assert restored._checkpoint_state_payload() != complete_payload
  assert restored._state["account"]["cash"] == 900.0
  assert restored._state["positions"]["600000.SH"]["long_volume"] == 120
  assert restored.pending_t_trade_diagnostic_events() == [
    {"event_key": "diagnostic:prepared"}
  ]
  assert restored._checkpoint_state_fingerprint() == prepared.state_fingerprint
  # MATERIALIZED_BEFORE_FINALIZE has only a local receipt, so its durable
  # PREPARED state is replayed idempotently with the same event key.
  finalized = await restored.finalize_prepared_checkpoint(
    prepared_checkpoint_id=recovered.checkpoint_id,
    materialization_event_keys=receipt_keys,
  )
  assert finalized is not None and finalized.complete
  assert restored.has_prepared_checkpoint() is False
  assert restored.pending_t_trade_diagnostic_events() == []
  matching_complete = [
    item
    for item in restored._runtime_checkpoint_records()
    if item.trade_date == "2026-08-21" and item.complete
  ]
  assert len(matching_complete) == 1


@pytest.mark.asyncio
async def test_damaged_prepared_checkpoint_uses_explicit_complete_fallback(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = RuntimeStateManager(
    run_id="run-checkpoint-fallback",
    persist_enabled=True,
    is_backtest=True,
  )

  async def fake_save_snapshot() -> bool:
    manager._dirty = False
    return True

  monkeypatch.setattr(manager, "save_snapshot", fake_save_snapshot)
  manager.update_custom_state({"strategy_window": {"samples": [1]}})
  complete = await manager.seal_checkpoint(
    trade_date="2026-08-20",
    session=None,
    boundary_source_time=datetime(2026, 8, 20, 15, 0),
    processed_watermark={"stream_id": "backtest", "sequence": 100},
    continuity_generation=1,
    completeness={"complete": True},
  )
  assert complete is not None
  manager.update_custom_state({"strategy_window": {"samples": [1, 2]}})
  prepared = await manager.prepare_checkpoint(
    trade_date="2026-08-21",
    session=None,
    boundary_source_time=datetime(2026, 8, 21, 15, 0),
    processed_watermark={"stream_id": "backtest", "sequence": 200},
    continuity_generation=1,
    completeness={"complete": True},
    materialization_events=[{"event_key": "diagnostic:2"}],
  )
  assert prepared is not None

  restored = RuntimeStateManager(
    run_id="run-checkpoint-fallback",
    persist_enabled=True,
    is_backtest=True,
  )
  restored._state = copy.deepcopy(manager._state)
  restored.update_custom_state({"strategy_window": {"corrupt": True}})

  async def fake_restore():
    return RuntimeStateRestoreResult(
      status=RuntimeStateRestoreStatus.RESTORED,
      state=restored._state,
    )

  monkeypatch.setattr(restored, "restore", fake_restore)
  assert restored.has_prepared_checkpoint() is True
  assert restored.latest_prepared_checkpoint() is None
  recovered = await restored.restore_latest_complete_checkpoint()

  assert recovered is not None
  assert recovered.checkpoint_id == complete.checkpoint_id
  assert restored._checkpoint_state_payload() == complete.state_payload


@pytest.mark.asyncio
async def test_finalize_prepared_checkpoint_requires_the_exact_receipt(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = RuntimeStateManager(
    run_id="run-checkpoint-receipt",
    persist_enabled=True,
    is_backtest=True,
  )

  async def fake_save_snapshot() -> bool:
    manager._dirty = False
    manager._state["version"] += 1
    return True

  monkeypatch.setattr(manager, "save_snapshot", fake_save_snapshot)
  prepared = await manager.prepare_checkpoint(
    trade_date="2026-08-21",
    session=None,
    boundary_source_time=datetime(2026, 8, 21, 15, 0),
    processed_watermark={"stream_id": "backtest", "sequence": 200},
    continuity_generation=1,
    completeness={"complete": True},
    materialization_events=[
      {"event_key": "diagnostic:a"},
      {"event_key": "diagnostic:b"},
    ],
  )
  assert prepared is not None

  assert await manager.finalize_prepared_checkpoint(
    prepared_checkpoint_id=prepared.checkpoint_id,
    materialization_event_keys=["diagnostic:a"],
  ) is None
  assert manager.latest_prepared_checkpoint() is not None
  assert {item["event_key"] for item in manager.pending_t_trade_diagnostic_events()} == {
    "diagnostic:a",
    "diagnostic:b",
  }

  finalized = await manager.finalize_prepared_checkpoint(
    prepared_checkpoint_id=prepared.checkpoint_id,
    materialization_event_keys=["diagnostic:b", "diagnostic:a"],
  )
  assert finalized is not None
  assert finalized.complete is True
  assert manager.has_prepared_checkpoint() is False
  assert manager.pending_t_trade_diagnostic_events() == []


@pytest.mark.asyncio
async def test_prepare_checkpoint_known_failure_retries_without_orphan_outbox() -> None:
  manager = RuntimeStateManager(
    run_id="run-checkpoint-prepare-retry",
    persist_enabled=True,
    is_backtest=True,
  )
  outcomes = [False, True]

  async def fake_save_snapshot() -> bool:
    result = outcomes.pop(0)
    if result:
      manager._dirty = False
      manager._state["version"] += 1
    return result

  manager.save_snapshot = fake_save_snapshot  # type: ignore[method-assign]
  request = {
    "trade_date": "2026-08-21",
    "session": None,
    "boundary_source_time": datetime(2026, 8, 21, 15, 0),
    "processed_watermark": {"stream_id": "backtest", "sequence": 200},
    "continuity_generation": 1,
    "completeness": {"complete": True},
    "materialization_events": [{"event_key": "diagnostic:retry"}],
  }

  assert await manager.prepare_checkpoint(**request) is None
  assert manager.has_prepared_checkpoint() is False
  assert manager.pending_t_trade_diagnostic_events() == []

  prepared = await manager.prepare_checkpoint(**request)
  assert prepared is not None
  assert manager.latest_prepared_checkpoint() is not None
  assert manager.pending_t_trade_diagnostic_events() == [
    {"event_key": "diagnostic:retry"}
  ]


@pytest.mark.asyncio
async def test_live_terminal_checkpoint_requires_terminal_proof_and_round_trips():
  manager = RuntimeStateManager(
    run_id="run-live-terminal-checkpoint",
    persist_enabled=True,
    is_backtest=False,
  )

  async def fake_save_snapshot() -> bool:
    manager._dirty = False
    manager._state["version"] += 1
    return True

  manager.save_snapshot = fake_save_snapshot  # type: ignore[method-assign]
  request = {
    "trade_date": "2026-08-21",
    "session": "TERMINAL",
    "boundary_source_time": datetime(2026, 8, 21, 15, 1),
    "processed_watermark": {"stream_id": "whole-quote", "sequence": 200},
    "continuity_generation": "whole-quote:3",
    "completeness": {"complete": True},
    "materialization_events": [{"event_key": "diagnostic:terminal"}],
  }

  assert await manager.prepare_checkpoint(**request) is None
  request["completeness"] = {"complete": True, "terminal": True}
  prepared = await manager.prepare_checkpoint(**request)

  assert prepared is not None
  assert prepared.checkpoint_kind == "SESSION_BOUNDARY"
  assert prepared.session == "TERMINAL"
  finalized = await manager.finalize_prepared_checkpoint(
    prepared_checkpoint_id=prepared.checkpoint_id,
    materialization_event_keys=["diagnostic:terminal"],
  )

  assert finalized is not None and finalized.complete
  assert finalized.session == "TERMINAL"
  assert (
    manager.latest_complete_checkpoint(
      trade_date="2026-08-21",
      session="TERMINAL",
    )
    == finalized
  )


@pytest.mark.asyncio
async def test_prepare_checkpoint_commit_unknown_recovers_durable_handoff() -> None:
  manager = RuntimeStateManager(
    run_id="run-checkpoint-prepare-unknown",
    persist_enabled=True,
    is_backtest=True,
  )
  durable_state: dict[str, object] = {}

  async def commit_then_return_unknown() -> bool:
    manager._dirty = False
    manager._state["version"] += 1
    durable_state["state"] = copy.deepcopy(manager._state)
    return False

  manager.save_snapshot = commit_then_return_unknown  # type: ignore[method-assign]
  assert await manager.prepare_checkpoint(
    trade_date="2026-08-21",
    session=None,
    boundary_source_time=datetime(2026, 8, 21, 15, 0),
    processed_watermark={"stream_id": "backtest", "sequence": 200},
    continuity_generation=1,
    completeness={"complete": True},
    materialization_events=[{"event_key": "diagnostic:unknown"}],
  ) is None

  restored = RuntimeStateManager(
    run_id="run-checkpoint-prepare-unknown",
    persist_enabled=True,
    is_backtest=True,
  )
  restored._state = copy.deepcopy(durable_state["state"])
  prepared = restored.latest_prepared_checkpoint()
  assert prepared is not None
  assert restored.pending_t_trade_diagnostic_events() == [
    {"event_key": "diagnostic:unknown"}
  ]


@pytest.mark.asyncio
async def test_day_checkpoint_outbox_covers_9600_fixture_worst_case_and_caps() -> None:
  manager = RuntimeStateManager(
    run_id="run-checkpoint-day-capacity",
    persist_enabled=True,
    is_backtest=True,
  )

  async def fake_save_snapshot() -> bool:
    manager._dirty = False
    manager._state["version"] += 1
    return True

  manager.save_snapshot = fake_save_snapshot  # type: ignore[method-assign]
  # The fixed 9,600-Tick fixture can put approximately half its input in one
  # virtual day.  Exact MATERIAL rows must fit without silently coalescing.
  one_day_events = [
    {"event_key": f"diagnostic:day:{index}"}
    for index in range(4_800)
  ]
  prepared = await manager.prepare_checkpoint(
    trade_date="2026-08-21",
    session=None,
    boundary_source_time=datetime(2026, 8, 21, 15, 0),
    processed_watermark={"stream_id": "backtest", "sequence": 4_800},
    continuity_generation=1,
    completeness={"complete": True},
    materialization_events=one_day_events,
  )
  assert prepared is not None
  assert len(manager.pending_t_trade_diagnostic_events()) == 4_800

  capped = RuntimeStateManager(
    run_id="run-checkpoint-hard-cap",
    persist_enabled=False,
    is_backtest=True,
  )
  capped.enqueue_t_trade_diagnostic_events(
    [{"event_key": f"diagnostic:cap:{index}"} for index in range(8_192)]
  )
  with pytest.raises(RuntimeError, match="8192"):
    capped.enqueue_t_trade_diagnostic_events(
      [{"event_key": "diagnostic:cap:overflow"}]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("is_backtest", [False, True])
async def test_start_never_creates_periodic_snapshot_task(is_backtest: bool) -> None:
  manager = RuntimeStateManager(
    run_id=f"run-no-periodic-snapshot-{is_backtest}",
    persist_enabled=True,
    is_backtest=is_backtest,
  )

  await manager.start()

  assert manager._snapshot_task is None
  await manager.stop()


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
  class FakeState:
    def __init__(self):
      self.values: dict[str, object] = {}

    def to_dict(self) -> dict[str, object]:
      return dict(self.values)

  class FakeStrategy:
    def __init__(self):
      self.queue = asyncio.Queue()
      self.state = FakeState()

    def subscribe_state(self):
      return self.queue

    def unsubscribe_state(self, queue):
      assert queue is self.queue

  strategy = FakeStrategy()
  manager = RuntimeStateManager(run_id="run-state-drain", persist_enabled=False)
  await manager.start()
  await manager.start_state_sync(strategy)
  strategy.state.values["signal_sample_windows"] = {
    "version": 1,
    "instruments": {"600000.SH": [[1, 10.0, 9.99, 10.0, 100.0, 10.0]]},
  }
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


@pytest.mark.asyncio
async def test_hot_state_sync_stages_1000_deltas_without_full_capture_until_checkpoint():
  class CountingState:
    def __init__(self):
      self.values: dict[str, object] = {
        "instrument_states": {"600000.SH": {"ordinal": 0, "samples": []}},
        "runtime_events": [],
      }
      self.to_dict_calls = 0

    def to_dict(self) -> dict[str, object]:
      self.to_dict_calls += 1
      return dict(self.values)

  class FakeStrategy:
    def __init__(self):
      self.queue = asyncio.Queue()
      self.state = CountingState()

    def subscribe_state(self):
      return self.queue

    def unsubscribe_state(self, queue):
      assert queue is self.queue

  strategy = FakeStrategy()
  manager = RuntimeStateManager(run_id="run-hot-state-stage", persist_enabled=False)
  await manager.start()
  await manager.start_state_sync(strategy)
  source_reads_at_start = strategy.state.to_dict_calls
  assert source_reads_at_start == 1

  instrument_states = strategy.state.values["instrument_states"]
  assert isinstance(instrument_states, dict)
  for ordinal in range(1_000):
    instrument_states["600000.SH"]["ordinal"] = ordinal
    strategy.queue.put_nowait(
      SimpleNamespace(
        persist=True,
        changes={"instrument_states": instrument_states},
        key=None,
        value=None,
      )
    )

  assert await manager.drain_strategy_state_changes(capture_state=False)
  assert strategy.state.to_dict_calls == source_reads_at_start
  assert manager._state_sync_pending_deltas["instrument_states"] is instrument_states
  assert "instrument_states" not in manager._state["custom"]

  manager.force_save = AsyncMock(return_value=True)
  assert await manager.checkpoint_strategy_state_changes()
  assert strategy.state.to_dict_calls == source_reads_at_start + 1
  assert manager.get_custom("instrument_states") == instrument_states
  assert manager._state_sync_pending_deltas == {}
  manager.force_save.assert_awaited_once()

  await manager.stop_state_sync(strategy)
  assert strategy.state.to_dict_calls == source_reads_at_start + 1
  await manager.stop()


@pytest.mark.asyncio
async def test_boundary_capture_replaces_strategy_keys_and_preserves_manager_custom_state():
  class FakeState:
    def __init__(self):
      self.values: dict[str, object] = {
        "instrument_states": {"600000.SH": {"phase": "FRESH"}},
        "runtime_events": [{"event_key": "latest"}],
        GRID_BOOK_CUSTOM_STATE_KEY: {"revision": 1, "source": "strategy"},
      }
      self.to_dict_calls = 0

    def to_dict(self) -> dict[str, object]:
      self.to_dict_calls += 1
      return dict(self.values)

  class FakeStrategy:
    def __init__(self):
      self.queue = asyncio.Queue()
      self.state = FakeState()

    def subscribe_state(self):
      return self.queue

    def unsubscribe_state(self, queue):
      assert queue is self.queue

  strategy = FakeStrategy()
  manager = RuntimeStateManager(run_id="run-state-replace", persist_enabled=False)
  manager._state["custom"] = {
    "stale_strategy_key": {"must": "be removed"},
    T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY: {
      "diagnostic:1": {"event_key": "diagnostic:1"}
    },
    RUNTIME_RECONCILIATION_STATUS_KEY: "RECONCILE_REQUIRED",
    GRID_BOOK_CUSTOM_STATE_KEY: {"revision": 9, "source": "manager"},
    "auto_exit_plan_book": {"plan-1": {"status": "OPEN"}},
  }
  await manager.start()
  await manager.start_state_sync(strategy)
  source_reads_at_start = strategy.state.to_dict_calls
  assert source_reads_at_start == 1

  assert await manager.drain_strategy_state_changes()
  assert strategy.state.to_dict_calls == source_reads_at_start + 1
  assert manager.get_custom("instrument_states") == {
    "600000.SH": {"phase": "FRESH"}
  }
  assert manager.get_custom("runtime_events") == [{"event_key": "latest"}]
  assert manager.get_custom("stale_strategy_key") is None
  assert manager.get_custom(T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY) == {
    "diagnostic:1": {"event_key": "diagnostic:1"}
  }
  assert manager.get_custom(RUNTIME_RECONCILIATION_STATUS_KEY) == "RECONCILE_REQUIRED"
  assert manager.get_custom(GRID_BOOK_CUSTOM_STATE_KEY) == {
    "revision": 9,
    "source": "manager",
  }
  assert manager.get_custom("auto_exit_plan_book") == {
    "plan-1": {"status": "OPEN"}
  }

  strategy.state.values.pop("runtime_events")
  strategy.queue.put_nowait(
    SimpleNamespace(
      persist=True,
      changes={"instrument_states": strategy.state.values["instrument_states"]},
      key=None,
      value=None,
    )
  )
  assert await manager.drain_strategy_state_changes()
  assert strategy.state.to_dict_calls == source_reads_at_start + 2
  assert manager.get_custom("runtime_events") is None
  assert manager.get_custom(T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY) == {
    "diagnostic:1": {"event_key": "diagnostic:1"}
  }

  await manager.stop_state_sync(strategy)
  await manager.stop()


@pytest.mark.asyncio
async def test_state_sync_source_capture_failure_is_fail_closed_and_abort_clears_source():
  class FailingState:
    def __init__(self):
      self.fail = False

    def to_dict(self) -> dict[str, object]:
      if self.fail:
        raise RuntimeError("state source unavailable")
      return {}

  class FakeStrategy:
    def __init__(self):
      self.queue = asyncio.Queue()
      self.state = FailingState()
      self.unsubscribed = False

    def subscribe_state(self):
      return self.queue

    def unsubscribe_state(self, queue):
      assert queue is self.queue
      self.unsubscribed = True

  strategy = FakeStrategy()
  manager = RuntimeStateManager(run_id="run-source-failure", persist_enabled=False)
  await manager.start()
  await manager.start_state_sync(strategy)
  strategy.state.fail = True
  manager.force_save = AsyncMock(return_value=True)

  assert await manager.drain_strategy_state_changes() is False
  assert manager._state_sync_error is not None
  assert await manager.checkpoint_strategy_state_changes() is False
  manager.force_save.assert_not_awaited()

  await manager.abort_without_final_snapshot()
  assert strategy.unsubscribed is True
  assert manager._state_sync_strategy is None
  assert manager._state_queue is None
  assert manager._state_sync_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("projection_failure", ["raises", "not_mapping"])
async def test_state_sync_persistence_projection_failure_is_fail_closed(
  projection_failure: str,
) -> None:
  class FakeState:
    def to_dict(self) -> dict[str, object]:
      return {"instrument_states": {"600000.SH": {"phase": "HOT"}}}

  class FakeStrategy:
    def __init__(self) -> None:
      self.queue = asyncio.Queue()
      self.state = FakeState()

    def subscribe_state(self):
      return self.queue

    def unsubscribe_state(self, queue):
      assert queue is self.queue

    def persistence_state_snapshot(self):
      if projection_failure == "raises":
        raise RuntimeError("projection unavailable")
      return []

  strategy = FakeStrategy()
  manager = RuntimeStateManager(
    run_id=f"run-projection-failure-{projection_failure}",
    persist_enabled=False,
  )
  await manager.start()
  await manager.start_state_sync(strategy)

  assert await manager.drain_strategy_state_changes() is False
  assert manager._state_sync_error is not None
  assert manager._state_sync_durable_strategy_snapshot is None

  await manager.abort_without_final_snapshot()
  assert manager._state_sync_strategy is None
  assert manager._state_sync_durable_strategy_snapshot is None


@pytest.mark.asyncio
async def test_compact_projection_keeps_hot_memory_and_unifies_durable_checkpoint_paths(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as state_repository_module,
  )

  class FakeState:
    def __init__(self) -> None:
      samples = [
        {
          "sentinel": f"volatile-sample-{ordinal}",
          "price": 10.0 + ordinal,
          "bid_price": 9.99,
          "ask_price": 10.01,
          "cumulative_amount": 1_000.0 + ordinal,
          "cumulative_volume": 100.0 + ordinal,
          "continuity_generation": "stream:1",
          "source_time_ms": ordinal,
          "tick_ordinal": ordinal,
        }
        for ordinal in range(1, 1_001)
      ]
      self.values: dict[str, object] = {
        "instrument_states": {
          "600000.SH": {
            "opportunity": {
              "samples": samples,
              "candidate": {"candidate_id": "candidate-1"},
            },
            "pending_entry_intent_id": "intent-1",
            "entry_order_status": "AWAITING_APPROVAL",
            "entry_filled_volume": 100,
          }
        },
        "state_schema_version": 3,
      }

    def to_dict(self) -> dict[str, object]:
      return dict(self.values)

  class FakeStrategy:
    def __init__(self) -> None:
      self.queue = asyncio.Queue()
      self.state = FakeState()

    def subscribe_state(self):
      return self.queue

    def unsubscribe_state(self, queue):
      assert queue is self.queue

    def persistence_state_snapshot(self) -> dict[str, object]:
      snapshot = self.state.to_dict()
      raw_states = dict(snapshot["instrument_states"])
      raw_state = dict(raw_states["600000.SH"])
      opportunity = dict(raw_state["opportunity"])
      samples = list(opportunity.pop("samples"))
      opportunity.update(
        {
          "sample_window_persisted": False,
          "sample_window_restore_required": True,
          "sample_window_sample_count": len(samples),
        }
      )
      raw_state["opportunity"] = opportunity
      raw_states["600000.SH"] = raw_state
      snapshot["instrument_states"] = raw_states
      return snapshot

  saved_custom_states: list[dict] = []

  class FakeDb:
    async def commit(self) -> None:
      return None

  async def fake_get_async_db():
    yield FakeDb()

  class FakeStateRepository:
    def __init__(self, _db) -> None:
      pass

    async def upsert_state(self, **kwargs) -> bool:
      saved_custom_states.append(copy.deepcopy(kwargs["custom_state"]))
      return True

  class FakePositionRepository:
    def __init__(self, _db) -> None:
      pass

    async def replace_positions_snapshot(self, *_args, **_kwargs) -> None:
      return None

    async def update_existing_positions_snapshot(self, *_args, **_kwargs) -> None:
      return None

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )

  strategy = FakeStrategy()
  manager = RuntimeStateManager(
    run_id="run-compact-projection",
    persist_enabled=True,
    is_backtest=True,
  )
  manager.set_custom(
    T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY,
    {"diagnostic:existing": {"event_key": "diagnostic:existing"}},
  )
  await manager.start()
  await manager.start_state_sync(strategy)
  strategy.queue.put_nowait(
    SimpleNamespace(
      persist=True,
      changes={"instrument_states": strategy.state.values["instrument_states"]},
      key=None,
      value=None,
    )
  )

  assert await manager.drain_strategy_state_changes()
  full_opportunity = manager._state["custom"]["instrument_states"][
    "600000.SH"
  ]["opportunity"]
  payload = manager._checkpoint_state_payload()
  durable_opportunity = payload["custom"]["instrument_states"]["600000.SH"][
    "opportunity"
  ]

  assert len(full_opportunity["samples"]) == 1_000
  assert full_opportunity["samples"][0]["sentinel"] == "volatile-sample-1"
  assert "samples" not in durable_opportunity
  assert "volatile-sample-1" not in repr(payload)
  assert durable_opportunity["sample_window_sample_count"] == 1_000
  assert payload["custom"][T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY] == {
    "diagnostic:existing": {"event_key": "diagnostic:existing"}
  }

  assert await manager.save_snapshot() is True
  assert "samples" not in saved_custom_states[-1]["instrument_states"][
    "600000.SH"
  ]["opportunity"]
  assert "volatile-sample-1" not in repr(saved_custom_states[-1])
  assert len(manager._state["custom"]["instrument_states"]["600000.SH"]["opportunity"]["samples"]) == 1_000

  # A normal terminal teardown removes the source but deliberately retains the
  # compact projection through its final generic RuntimeState save.
  await manager.stop_state_sync(strategy)
  assert manager._state_sync_strategy is None
  assert manager._state_sync_durable_strategy_snapshot is not None
  manager._mark_dirty()
  assert await manager.save_snapshot() is True
  assert "samples" not in saved_custom_states[-1]["instrument_states"][
    "600000.SH"
  ]["opportunity"]

  prepared = await manager.prepare_checkpoint(
    trade_date="2026-08-21",
    session=None,
    boundary_source_time=datetime(2026, 8, 21, 15, 0),
    processed_watermark={"stream_id": "backtest", "sequence": 1_000},
    continuity_generation="stream:1",
    completeness={"complete": True},
    materialization_events=[
      {"event_key": "diagnostic:existing"},
      {"event_key": "diagnostic:new"},
    ],
  )
  assert prepared is not None
  assert "samples" not in prepared.state_payload["custom"]["instrument_states"][
    "600000.SH"
  ]["opportunity"]
  assert manager._checkpoint_state_fingerprint() == prepared.state_fingerprint

  prepared_restore = RuntimeStateManager(
    run_id=manager.run_id,
    persist_enabled=True,
    is_backtest=True,
  )
  prepared_restore._state["account"] = copy.deepcopy(manager._state["account"])
  prepared_restore._state["positions"] = copy.deepcopy(manager._state["positions"])
  prepared_restore._state["bucket_ledger"] = copy.deepcopy(manager._state["bucket_ledger"])
  prepared_restore._state["custom"] = copy.deepcopy(saved_custom_states[-1])
  prepared_restore._state["version"] = manager._state["version"]
  assert prepared_restore.latest_prepared_checkpoint() == prepared

  finalized = await manager.finalize_prepared_checkpoint(
    prepared_checkpoint_id=prepared.checkpoint_id,
    materialization_event_keys=["diagnostic:existing", "diagnostic:new"],
  )
  assert finalized is not None and finalized.complete
  assert "samples" not in finalized.state_payload["custom"]["instrument_states"][
    "600000.SH"
  ]["opportunity"]

  restored = RuntimeStateManager(
    run_id=manager.run_id,
    persist_enabled=True,
    is_backtest=True,
  )
  restored._state["account"] = copy.deepcopy(manager._state["account"])
  restored._state["positions"] = copy.deepcopy(manager._state["positions"])
  restored._state["bucket_ledger"] = copy.deepcopy(manager._state["bucket_ledger"])
  restored._state["custom"] = copy.deepcopy(saved_custom_states[-1])
  restored._state["version"] = manager._state["version"]
  assert restored.latest_complete_checkpoint() == finalized
  assert restored._restore_complete_checkpoint_payload(finalized)
  assert "samples" not in restored._checkpoint_state_payload()["custom"][
    "instrument_states"
  ]["600000.SH"]["opportunity"]

  await manager.stop()
  assert manager._state_sync_durable_strategy_snapshot is None


def test_checkpoint_payload_filters_checkpoint_history_before_deepcopy() -> None:
  class MustNotCopy:
    def __deepcopy__(self, _memo):
      raise AssertionError("checkpoint history must be filtered before deepcopy")

  manager = RuntimeStateManager(run_id="run-checkpoint-copy", persist_enabled=False)
  manager._state["custom"] = {
    "strategy_window": {"samples": [1, 2, 3]},
    "runtime_checkpoints": MustNotCopy(),
    "runtime_snapshot_attempt": MustNotCopy(),
  }

  payload = manager._checkpoint_state_payload()

  assert payload["custom"]["strategy_window"] == {"samples": [1, 2, 3]}
  assert "runtime_checkpoints" not in payload["custom"]
  assert "runtime_snapshot_attempt" not in payload["custom"]


def test_durable_trace_record_keeps_only_supplemental_and_rebuilds_publish_shape() -> None:
  from quantx_domain.trading.decision_trace import DecisionTrace

  timestamp = datetime(2026, 8, 24, 10, 1, tzinfo=timezone.utc)
  trace = DecisionTrace(
    trace_id="trace-supplemental",
    run_id="run-trace-supplemental",
    strategy_id="ashare_intraday_t_assistant",
    instrument_code="600000.SH",
    timestamp=timestamp,
    input_summary={"input_id": "input-1"},
    environment={"market_state": "NORMAL"},
    risk_caps={"allow_buy": True},
    position_profile={"profile": "BALANCED"},
    execution_profile={"quote_source": "replay"},
    output_summary={"trade_intent_count": 1},
    state_patch={"format": "CONTENT_ADDRESSED_RUNTIME_STATE_PATCH_V1"},
    trade_intents=[{"intent_id": "intent-1"}],
    order_draft={"draft_id": "draft-1"},
    order_request={"volume": 100},
    risk_decision={"allowed": True},
    broker_report={"order_id": "order-1"},
    tags=["strategy_output", "HOT_OUTPUT"],
    reason="HOT_TICK",
  )
  manager = RuntimeStateManager(
    run_id="run-trace-supplemental",
    persist_enabled=True,
  )
  published_traces: list[dict] = []
  manager._backtest_storage = SimpleNamespace(
    add_trace=lambda payload: published_traces.append(dict(payload))
  )

  record = manager._decision_trace_record_data(trace)
  supplemental = record["decision_trace"]

  assert set(supplemental) == {
    "format",
    "environment",
    "risk_caps",
    "position_profile",
    "execution_profile",
    "order_draft",
    "order_request",
    "risk_decision",
    "broker_report",
    "tags",
    "reason",
  }
  assert supplemental["format"] == "DECISION_TRACE_SUPPLEMENTAL_V1"
  for duplicated_field in {
    "input_summary",
    "output_summary",
    "state_patch",
    "trade_intents",
    "trace_id",
    "run_id",
    "strategy_id",
    "instrument_code",
    "timestamp",
  }:
    assert duplicated_field not in supplemental

  manager._publish_durable_decision_trace_record(record)

  expected = trace.to_dict()
  assert manager._decision_trace_logger.to_list() == [expected]
  assert published_traces == [expected]


@pytest.mark.asyncio
async def test_snapshot_commits_pending_decision_trace_with_tick_cas(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """One evaluated Tick keeps its CAS and audit in one transaction."""

  from quantx_domain.trading.decision_trace import DecisionTrace
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_decision_trace_repository as trace_repository_module,
  )
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as state_repository_module,
  )

  commits: list[str] = []
  state_versions: list[int] = []
  appended: list[dict] = []
  published_traces: list[dict] = []

  class FakeDb:
    async def commit(self) -> None:
      commits.append("commit")

  async def fake_get_async_db():
    yield FakeDb()

  class FakeStateRepository:
    def __init__(self, _db) -> None:
      pass

    async def upsert_state(self, **kwargs) -> bool:
      state_versions.append(kwargs["expected_version"])
      return True

  class FakePositionRepository:
    def __init__(self, _db) -> None:
      pass

    async def replace_positions_snapshot(self, *_args, **_kwargs) -> None:
      return None

  class FakeTraceRepository:
    def __init__(self, _db) -> None:
      pass

    async def append_traces(self, records, *, commit, flush):
      assert commit is False
      assert flush is False
      appended.extend(dict(record) for record in records)
      return []

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )
  monkeypatch.setattr(
    trace_repository_module,
    "StrategyDecisionTraceRepository",
    FakeTraceRepository,
  )

  manager = RuntimeStateManager(run_id="run-trace-atomic", persist_enabled=True)
  manager._backtest_storage = SimpleNamespace(
    add_trace=lambda trace: published_traces.append(dict(trace))
  )
  manager.record_decision_trace(
    DecisionTrace.from_decision(
      run_id="run-trace-atomic",
      strategy_id="ashare_intraday_t_assistant",
      instrument_code="600000.SH",
      trace_id="trace-atomic",
    )
  )

  assert await manager.save_snapshot() is True
  assert state_versions == [0]
  assert commits == ["commit"]
  assert len(appended) == 1
  assert appended[0]["trace_id"] == "trace-atomic"
  assert manager._pending_decision_trace_records == []
  assert [trace.trace_id for trace in manager._decision_trace_logger.records] == [
    "trace-atomic"
  ]
  assert [trace["trace_id"] for trace in published_traces] == ["trace-atomic"]


@pytest.mark.asyncio
async def test_failed_trace_append_keeps_exact_audit_for_snapshot_retry(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """A trace failure rolls back the CAS boundary and never drops its audit."""

  from quantx_domain.trading.decision_trace import DecisionTrace
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_decision_trace_repository as trace_repository_module,
  )
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as state_repository_module,
  )

  attempts: list[str] = []
  state_versions: list[int] = []
  published_traces: list[dict] = []
  failure = True

  class FakeDb:
    async def commit(self) -> None:
      raise AssertionError("trace append failure must not commit CAS state")

  async def fake_get_async_db():
    yield FakeDb()

  class FakeStateRepository:
    def __init__(self, _db) -> None:
      pass

    async def upsert_state(self, **kwargs) -> bool:
      state_versions.append(kwargs["expected_version"])
      return True

    async def get_state(self, _run_id):
      return None

  class FakePositionRepository:
    def __init__(self, _db) -> None:
      pass

    async def replace_positions_snapshot(self, *_args, **_kwargs) -> None:
      return None

  class FakeTraceRepository:
    def __init__(self, _db) -> None:
      pass

    async def append_traces(self, records, *, commit, flush):
      attempts.extend(str(record["id"]) for record in records)
      if failure:
        raise RuntimeError("trace storage unavailable")
      return []

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )
  monkeypatch.setattr(
    trace_repository_module,
    "StrategyDecisionTraceRepository",
    FakeTraceRepository,
  )

  manager = RuntimeStateManager(run_id="run-trace-retry", persist_enabled=True)
  manager._backtest_storage = SimpleNamespace(
    add_trace=lambda trace: published_traces.append(dict(trace))
  )
  manager.record_decision_trace(
    DecisionTrace.from_decision(
      run_id="run-trace-retry",
      strategy_id="ashare_intraday_t_assistant",
      instrument_code="600000.SH",
      trace_id="trace-retry",
    )
  )
  original_id = manager._pending_decision_trace_records[0]["id"]

  assert await manager.save_snapshot() is False
  assert manager.last_snapshot_failure_code == "PERSISTENCE_ERROR"
  assert [item["id"] for item in manager._pending_decision_trace_records] == [
    original_id
  ]
  assert state_versions == [0]
  assert attempts == [original_id]
  assert manager._decision_trace_logger.records == []
  assert published_traces == []

  failure = False
  # A new DB implementation lets the retry commit; re-use a transaction that
  # records the one final commit rather than weakening the previous failure.
  class SucceedingDb:
    async def commit(self) -> None:
      return None

  async def succeeding_get_async_db():
    yield SucceedingDb()

  monkeypatch.setattr(connection_module, "get_async_db", succeeding_get_async_db)
  assert await manager.save_snapshot() is True
  assert state_versions == [0, 0]
  assert attempts == [original_id, original_id]
  assert manager._pending_decision_trace_records == []
  assert [trace.trace_id for trace in manager._decision_trace_logger.records] == [
    "trace-retry"
  ]
  assert [trace["trace_id"] for trace in published_traces] == ["trace-retry"]


@pytest.mark.asyncio
async def test_commit_unknown_acknowledges_trace_only_after_snapshot_token_proves_commit(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """A connection loss after commit does not duplicate stable trace UUIDs."""

  from quantx_domain.trading.decision_trace import DecisionTrace
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_decision_trace_repository as trace_repository_module,
  )
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as state_repository_module,
  )

  authoritative = SimpleNamespace(
    version=0,
    cash=0.0,
    frozen_cash=0.0,
    total_asset=0.0,
    custom_state={},
  )
  appended: list[str] = []
  published_traces: list[dict] = []

  class CommitUnknownDb:
    async def commit(self) -> None:
      raise RuntimeError("connection lost after commit")

  async def fake_get_async_db():
    yield CommitUnknownDb()

  class FakeStateRepository:
    def __init__(self, _db) -> None:
      pass

    async def upsert_state(self, **kwargs) -> bool:
      authoritative.version = int(kwargs["expected_version"]) + 1
      authoritative.cash = kwargs["cash"]
      authoritative.frozen_cash = kwargs["frozen_cash"]
      authoritative.total_asset = kwargs["total_asset"]
      authoritative.custom_state = dict(kwargs["custom_state"])
      return True

    async def get_state(self, _run_id):
      return authoritative

  class FakePositionRepository:
    def __init__(self, _db) -> None:
      pass

    async def replace_positions_snapshot(self, *_args, **_kwargs) -> None:
      return None

    async def get_all_positions(self, _run_id):
      return []

  class FakeTraceRepository:
    def __init__(self, _db) -> None:
      pass

    async def append_traces(self, records, *, commit, flush):
      assert commit is False
      assert flush is False
      appended.extend(str(record["id"]) for record in records)
      return []

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )
  monkeypatch.setattr(
    trace_repository_module,
    "StrategyDecisionTraceRepository",
    FakeTraceRepository,
  )

  manager = RuntimeStateManager(
    run_id="run-trace-commit-unknown",
    persist_enabled=True,
  )
  manager._backtest_storage = SimpleNamespace(
    add_trace=lambda trace: published_traces.append(dict(trace))
  )
  manager.record_decision_trace(
    DecisionTrace.from_decision(
      run_id="run-trace-commit-unknown",
      strategy_id="ashare_intraday_t_assistant",
      instrument_code="600000.SH",
      trace_id="trace-commit-unknown",
    )
  )
  trace_id = manager._pending_decision_trace_records[0]["id"]

  assert await manager.save_snapshot() is True
  assert authoritative.version == 1
  assert appended == [trace_id]
  assert manager._pending_decision_trace_records == []
  # A returned commit error is not publication permission.  The authoritative
  # token reconciliation proves the same transaction committed, then publishes
  # the trace exactly once to each non-authoritative sink.
  assert [trace.trace_id for trace in manager._decision_trace_logger.records] == [
    "trace-commit-unknown"
  ]
  assert [trace["trace_id"] for trace in published_traces] == [
    "trace-commit-unknown"
  ]


@pytest.mark.asyncio
async def test_cas_conflict_discards_only_captured_losing_trace_before_any_append(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """A concurrent winner cannot make a losing output auditable later."""

  from quantx_domain.trading.decision_trace import DecisionTrace
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_decision_trace_repository as trace_repository_module,
  )
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as state_repository_module,
  )

  authoritative = SimpleNamespace(
    version=1,
    cash=0.0,
    frozen_cash=0.0,
    total_asset=0.0,
    custom_state={"winner": "external"},
  )
  expected_versions: list[int] = []
  appended_ids: list[str] = []
  published_traces: list[dict] = []

  class FakeDb:
    async def commit(self) -> None:
      return None

  async def fake_get_async_db():
    yield FakeDb()

  class FakeStateRepository:
    def __init__(self, _db) -> None:
      pass

    async def upsert_state(self, **kwargs) -> bool:
      expected_version = int(kwargs["expected_version"])
      expected_versions.append(expected_version)
      if expected_version == 0:
        # The first save has already captured the losing record. Simulate a
        # later output arriving while the failed CAS yielded to the loop.
        manager.record_decision_trace(
          DecisionTrace.from_decision(
            run_id="run-trace-cas-conflict",
            strategy_id="ashare_intraday_t_assistant",
            instrument_code="000001.SZ",
            trace_id="trace-created-after-cas-capture",
          )
        )
        return False
      assert expected_version == 1
      authoritative.version = 2
      authoritative.custom_state = dict(kwargs["custom_state"])
      return True

    async def get_state(self, _run_id):
      return authoritative

  class FakePositionRepository:
    def __init__(self, _db) -> None:
      pass

    async def replace_positions_snapshot(self, *_args, **_kwargs) -> None:
      return None

    async def get_all_positions(self, _run_id):
      return []

  class FakeTraceRepository:
    def __init__(self, _db) -> None:
      pass

    async def append_traces(self, records, *, commit, flush):
      assert commit is False
      assert flush is False
      appended_ids.extend(str(record["id"]) for record in records)
      return []

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    state_repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )
  monkeypatch.setattr(
    trace_repository_module,
    "StrategyDecisionTraceRepository",
    FakeTraceRepository,
  )

  manager = RuntimeStateManager(
    run_id="run-trace-cas-conflict",
    persist_enabled=True,
  )
  manager._backtest_storage = SimpleNamespace(
    add_trace=lambda trace: published_traces.append(dict(trace))
  )
  manager.record_decision_trace(
    DecisionTrace.from_decision(
      run_id="run-trace-cas-conflict",
      strategy_id="ashare_intraday_t_assistant",
      instrument_code="600000.SH",
      trace_id="trace-losing-cas-output",
    )
  )
  losing_id = manager._pending_decision_trace_records[0]["id"]

  assert await manager.save_snapshot() is False
  assert manager.last_snapshot_failure_code == "CAS_CONFLICT"
  assert appended_ids == []
  assert manager._decision_trace_logger.records == []
  assert published_traces == []
  assert len(manager._pending_decision_trace_records) == 1
  successor_id = manager._pending_decision_trace_records[0]["id"]
  assert successor_id != losing_id

  # The next snapshot is based on the adopted winner and writes only the
  # output captured after the loser had already failed.
  assert await manager.save_snapshot() is True
  assert expected_versions == [0, 1]
  assert appended_ids == [successor_id]
  assert losing_id not in appended_ids
  assert manager._pending_decision_trace_records == []
  assert [trace.trace_id for trace in manager._decision_trace_logger.records] == [
    "trace-created-after-cas-capture"
  ]
  assert [trace["trace_id"] for trace in published_traces] == [
    "trace-created-after-cas-capture"
  ]


@pytest.mark.asyncio
async def test_decision_trace_idempotent_replay_accepts_identical_content() -> None:
  """A UTC-aware input replays against the same PostgreSQL-naive value."""

  from quantx_infrastructure.repositories.strategy_decision_trace_repository import (
    StrategyDecisionTraceRepository,
  )

  payload = {
    "id": "trace-idempotent",
    "trace_id": "source-trace-id",
    "strategy_run_id": "run-idempotent",
    "strategy_id": "ashare_intraday_t_assistant",
    "instrument_code": "600000.SH",
    "decided_at": datetime(
      2026,
      8,
      24,
      9,
      30,
      tzinfo=timezone(timedelta(hours=8)),
    ),
    "input_summary": {"price": 10.0},
    "output_summary": {"decision": "HOLD"},
    "trade_intents": [],
    "state_patch": {"set": {"candidate_status": "NONE"}},
    "decision_trace": {"reason": "NO_TRADE_INTENT"},
  }

  class ScalarResult:
    def __init__(self, values) -> None:
      self.values = values

    def scalars(self):
      return self

    def all(self):
      return list(self.values)

  existing = SimpleNamespace(
    **{
      **payload,
      # PostgreSQL's TIMESTAMP WITHOUT TIME ZONE restores UTC-naive values.
      "decided_at": datetime(2026, 8, 24, 1, 30),
    }
  )

  class FakeDb:
    def __init__(self) -> None:
      self.calls = []

    async def execute(self, statement, params=None):
      self.calls.append((statement, params))
      return ScalarResult([] if len(self.calls) == 1 else [existing])

  db = FakeDb()
  records = await StrategyDecisionTraceRepository(db).append_traces(
    [payload],
    commit=False,
    flush=False,
  )

  assert [record.id for record in records] == [payload["id"]]
  assert len(db.calls) == 2
  from sqlalchemy.dialects import postgresql

  statement, parameter_batch = db.calls[0]
  sql = str(statement.compile(dialect=postgresql.dialect()))
  assert "ON CONFLICT (id) DO NOTHING" in sql
  assert "RETURNING strategy_decision_traces.id" in sql
  assert parameter_batch is None
  assert "VALUES (" in sql
  assert "trace-idempotent" not in sql
  replay_sql = str(db.calls[1][0].compile(dialect=postgresql.dialect()))
  assert replay_sql.startswith("SELECT ")


@pytest.mark.asyncio
async def test_decision_trace_append_fresh_batch_uses_one_multi_values_execute() -> None:
  """Fresh trace batches bind one multi-values INSERT without executemany."""

  from quantx_infrastructure.repositories.strategy_decision_trace_repository import (
    StrategyDecisionTraceRepository,
  )

  payloads = [
    {
      "id": f"trace-parameterized-{index}",
      "trace_id": f"source-trace-{index}",
      "strategy_run_id": "run-parameterized",
      "strategy_id": "ashare_intraday_t_assistant",
      "instrument_code": "600000.SH",
      "decided_at": datetime(
        2026,
        8,
        24,
        9,
        30 + index,
        tzinfo=timezone(timedelta(hours=8)),
      ),
      "input_summary": {"price": 10.0 + index},
      "output_summary": {"decision": "HOLD"},
      "trade_intents": [],
      "state_patch": {"set": {"ordinal": index}},
      "decision_trace": {"reason": "NO_TRADE_INTENT"},
    }
    for index in range(3)
  ]

  class ScalarResult:
    def __init__(self, values) -> None:
      self.values = values

    def scalars(self):
      return self

    def all(self):
      return list(self.values)

  class FakeDb:
    def __init__(self) -> None:
      self.calls = []

    async def execute(self, statement, params=None):
      self.calls.append((statement, params))
      return ScalarResult([item["id"] for item in payloads])

  db = FakeDb()
  records = await StrategyDecisionTraceRepository(db).append_traces(
    payloads,
    commit=False,
    flush=False,
  )

  assert [record.id for record in records] == [item["id"] for item in payloads]
  assert len(db.calls) == 1
  statement, parameter_batch = db.calls[0]
  assert parameter_batch is None

  from sqlalchemy.dialects import postgresql

  sql = str(statement.compile(dialect=postgresql.dialect()))
  assert sql.count("VALUES (") == 1
  assert sql.count("), (") == len(payloads) - 1
  assert "trace-parameterized-0" not in sql
  assert "ON CONFLICT (id) DO NOTHING" in sql
  assert "RETURNING strategy_decision_traces.id" in sql


@pytest.mark.asyncio
async def test_decision_trace_append_600_rows_chunks_without_intermediate_commit() -> None:
  from quantx_infrastructure.repositories.strategy_decision_trace_repository import (
    StrategyDecisionTraceRepository,
  )

  payloads = [
    {
      "id": f"trace-chunked-{index}",
      "trace_id": f"source-chunked-{index}",
      "strategy_run_id": "run-chunked",
      "strategy_id": "ashare_intraday_t_assistant",
      "instrument_code": "600000.SH",
      "decided_at": datetime(2026, 8, 24, 9, 30),
      "input_summary": {"ordinal": index},
      "output_summary": {"decision": "HOLD"},
      "trade_intents": [],
      "state_patch": {"set": {"ordinal": index}},
      "decision_trace": {"reason": "NO_TRADE_INTENT"},
    }
    for index in range(600)
  ]

  class ScalarResult:
    def __init__(self, values) -> None:
      self.values = values

    def scalars(self):
      return self

    def all(self):
      return list(self.values)

  class FakeDb:
    def __init__(self) -> None:
      self.calls = []
      self.commit_calls = 0
      self.flush_calls = 0

    async def execute(self, statement, params=None):
      self.calls.append((statement, params))
      chunk_index = len(self.calls) - 1
      start = chunk_index * 256
      return ScalarResult(
        [item["id"] for item in payloads[start : start + 256]]
      )

    async def commit(self) -> None:
      self.commit_calls += 1

    async def flush(self) -> None:
      self.flush_calls += 1

  db = FakeDb()
  records = await StrategyDecisionTraceRepository(db).append_traces(payloads)

  assert [record.id for record in records] == [item["id"] for item in payloads]
  assert len(db.calls) == 3
  assert [params for _statement, params in db.calls] == [None, None, None]
  assert db.commit_calls == 1
  assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_decision_trace_replay_across_chunks_uses_one_final_select() -> None:
  from quantx_infrastructure.repositories.strategy_decision_trace_repository import (
    StrategyDecisionTraceRepository,
  )

  payloads = [
    {
      "id": f"trace-replay-chunked-{index}",
      "trace_id": f"source-replay-chunked-{index}",
      "strategy_run_id": "run-replay-chunked",
      "strategy_id": "ashare_intraday_t_assistant",
      "instrument_code": "600000.SH",
      "decided_at": datetime(2026, 8, 24, 9, 30),
      "input_summary": {"ordinal": index},
      "output_summary": {"decision": "HOLD"},
      "trade_intents": [],
      "state_patch": {"set": {"ordinal": index}},
      "decision_trace": {"reason": "NO_TRADE_INTENT"},
    }
    for index in range(600)
  ]
  replayed = [
    SimpleNamespace(**payload)
    for payload in payloads[256:512]
  ]

  class ScalarResult:
    def __init__(self, values) -> None:
      self.values = values

    def scalars(self):
      return self

    def all(self):
      return list(self.values)

  class FakeDb:
    def __init__(self) -> None:
      self.calls = []

    async def execute(self, statement, params=None):
      self.calls.append((statement, params))
      call_index = len(self.calls) - 1
      if call_index == 1:
        return ScalarResult([])
      if call_index == 3:
        return ScalarResult(replayed)
      start = call_index * 256
      return ScalarResult(
        [item["id"] for item in payloads[start : start + 256]]
      )

  db = FakeDb()
  records = await StrategyDecisionTraceRepository(db).append_traces(
    payloads,
    commit=False,
    flush=False,
  )

  assert [record.id for record in records] == [item["id"] for item in payloads]
  assert len(db.calls) == 4
  assert [params for _statement, params in db.calls] == [
    None,
    None,
    None,
    None,
  ]
  from sqlalchemy.dialects import postgresql

  assert all(
    "INSERT INTO strategy_decision_traces"
    in str(statement.compile(dialect=postgresql.dialect()))
    for statement, _params in db.calls[:3]
  )
  assert str(db.calls[3][0].compile(dialect=postgresql.dialect())).startswith(
    "SELECT "
  )


@pytest.mark.asyncio
async def test_create_trace_normalizes_aware_decided_at_at_repository_boundary() -> None:
  """The legacy single-row entrypoint obeys the same timestamp contract."""

  from quantx_infrastructure.repositories.strategy_decision_trace_repository import (
    StrategyDecisionTraceRepository,
  )

  payload = {
    "id": "trace-aware-create",
    "trace_id": "source-trace-aware-create",
    "strategy_run_id": "run-aware-create",
    "strategy_id": "ashare_intraday_t_assistant",
    "instrument_code": "600000.SH",
    "decided_at": datetime(
      2026,
      8,
      24,
      9,
      30,
      tzinfo=timezone(timedelta(hours=8)),
    ),
    "input_summary": {},
    "output_summary": {},
    "trade_intents": [],
    "state_patch": {},
    "decision_trace": {},
  }

  class FakeDb:
    def add(self, record) -> None:
      self.record = record

    async def commit(self) -> None:
      return None

    async def refresh(self, _record) -> None:
      return None

  db = FakeDb()
  record = await StrategyDecisionTraceRepository(db).create_trace(payload)

  assert record.decided_at == datetime(2026, 8, 24, 1, 30)
  assert record.decided_at.tzinfo is None


@pytest.mark.asyncio
async def test_decision_trace_idempotent_replay_rejects_different_content() -> None:
  """The same UUID with changed audit facts is a hard persistence failure."""

  from quantx_infrastructure.repositories.strategy_decision_trace_repository import (
    StrategyDecisionTraceRepository,
  )

  payload = {
    "id": "trace-conflict",
    "trace_id": "source-trace-id",
    "strategy_run_id": "run-conflict",
    "strategy_id": "ashare_intraday_t_assistant",
    "instrument_code": "600000.SH",
    "decided_at": datetime(
      2026,
      8,
      24,
      9,
      30,
      tzinfo=timezone(timedelta(hours=8)),
    ),
    "input_summary": {"price": 10.0},
    "output_summary": {"decision": "HOLD"},
    "trade_intents": [],
    "state_patch": {"set": {"candidate_status": "NONE"}},
    "decision_trace": {"reason": "NO_TRADE_INTENT"},
  }

  class ScalarResult:
    def __init__(self, values) -> None:
      self.values = values

    def scalars(self):
      return self

    def all(self):
      return list(self.values)

  existing = SimpleNamespace(
    **{
      **payload,
      "decided_at": datetime(2026, 8, 24, 1, 30),
      "decision_trace": {"reason": "DIFFERENT_AUDIT_FACT"},
    }
  )

  class FakeDb:
    def __init__(self) -> None:
      self.calls = []

    async def execute(self, statement, params=None):
      self.calls.append((statement, params))
      return ScalarResult([] if len(self.calls) == 1 else [existing])

  db = FakeDb()
  with pytest.raises(ValueError, match="idempotency conflict"):
    await StrategyDecisionTraceRepository(db).append_traces(
      [payload],
      commit=False,
      flush=False,
    )
  assert len(db.calls) == 2
  assert db.calls[0][1] is None
  from sqlalchemy.dialects import postgresql

  assert str(db.calls[1][0].compile(dialect=postgresql.dialect())).startswith(
    "SELECT "
  )
