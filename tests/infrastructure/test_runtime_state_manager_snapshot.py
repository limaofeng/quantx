from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from quantx_infrastructure.core.runtime_state_manager import (
  RuntimeStateManager,
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
