from __future__ import annotations

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

  async def fake_get_async_db():
    yield object()

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
  return state_repo_calls, state_repo_results


@pytest.mark.asyncio
async def test_snapshot_advances_optimistic_lock_version(snapshot_dependencies):
  calls, results = snapshot_dependencies
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


@pytest.mark.asyncio
async def test_snapshot_keeps_dirty_state_after_version_conflict(
  snapshot_dependencies,
):
  calls, results = snapshot_dependencies
  results.append(False)
  manager = RuntimeStateManager(run_id="run-2", persist_enabled=True)
  manager._state["version"] = 4
  manager._dirty = True

  assert await manager.save_snapshot() is False

  assert calls == [4]
  assert manager._state["version"] == 4
  assert manager._dirty is True
