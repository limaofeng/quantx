import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import quantx_engine.command_processor as command_processor
from quantx_domain.strategies.base import (
  RuntimeStatePatch,
  StrategyRunMode,
  StrategyStateProxy,
)
from quantx_engine.strategy_executor import ExecutionStatus, StrategyExecutor
from quantx_engine.t_trade_coordination import t_trade_account_coordination_lock
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


@pytest.mark.asyncio
async def test_external_import_command_holds_account_lock_against_tick_path(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  entered = asyncio.Event()
  release = asyncio.Event()

  class Service:
    async def import_external_entry(self, *_args, account_coordination_held=False):
      assert account_coordination_held is True
      entered.set()
      await release.wait()
      return {"success": True, "code": "EXTERNAL_ENTRY_IMPORTED"}

  async def serialize(_run_id, action):
    return await action()

  monkeypatch.setattr(command_processor, "TTradeService", lambda _manager: Service())
  monkeypatch.setattr(
    command_processor,
    "strategy_manager",
    SimpleNamespace(
      executor=SimpleNamespace(execute_serialized_t_external_import=serialize)
    ),
  )
  task = asyncio.create_task(
    command_processor._dispatch(
      "T_TRADE_IMPORT_EXTERNAL_ENTRY",
      {
        "run_id": "run-1",
        "account_id": "account-1",
        "order_id": "101",
      },
    )
  )
  await entered.wait()
  lock = t_trade_account_coordination_lock("account-1")
  assert lock.locked() is True
  assert lock.try_acquire() is False
  release.set()

  assert await task == {"success": True, "code": "EXTERNAL_ENTRY_IMPORTED"}
  assert lock.locked() is False


def test_external_hot_patch_is_not_applied_when_version_adoption_fails() -> None:
  apply_patch = Mock()
  adopt = Mock(side_effect=RuntimeError("runtime version changed"))
  on_change = Mock()
  initial = {
    "instrument_states": {"600000.SH": {"status": "OBSERVING"}},
    "marker": "before",
  }
  state = StrategyStateProxy(on_change, initial=initial)
  runtime = SimpleNamespace(
    strategy=SimpleNamespace(state=state),
    state_manager=SimpleNamespace(adopt_external_durable_state_version=adopt),
    status=ExecutionStatus.RUNNING,
  )
  executor = SimpleNamespace(
    runs={"run-1": runtime},
    _apply_runtime_state_patch=apply_patch,
  )

  with pytest.raises(RuntimeError, match="runtime version changed"):
    StrategyExecutor.publish_external_durable_state(
      executor,
      "run-1",
      RuntimeStatePatch(set={"instrument_states": {}}),
      durable_state_version=5,
      durable_custom_state={"instrument_states": {}},
    )

  adopt.assert_called_once()
  assert adopt.call_args.args == (5,)
  assert callable(adopt.call_args.kwargs["publish"])
  assert state.to_dict() == initial
  on_change.assert_not_called()
  apply_patch.assert_not_called()
  assert runtime.status == ExecutionStatus.ERROR
  assert runtime.error_message == "EXTERNAL_IMPORT_STATE_PUBLICATION_FAILED"


@pytest.mark.parametrize("publish_fails", [False, True])
def test_external_import_adopts_exact_durable_image_or_stops_runtime(publish_fails):
  manager = RuntimeStateManager(run_id="run-1", persist_enabled=False)
  manager._state["version"] = 4
  manager._state["custom"] = {"stale": True}
  manager._state_sync_durable_strategy_snapshot = {"stale": True}
  state = StrategyStateProxy(Mock(), initial={"stale": True})
  durable = {
    "instrument_states": {"600000.SH": {"batch_id": "batch-external"}},
    "runtime_events": [{"type": "T_TRADE_EXTERNAL_ENTRY_IMPORTED"}],
  }
  runtime = SimpleNamespace(
    strategy=SimpleNamespace(state=state),
    state_manager=manager,
    status=ExecutionStatus.RUNNING,
    error_message=None,
  )
  if publish_fails:
    runtime.strategy.state = SimpleNamespace(
      replace=Mock(side_effect=RuntimeError("publication failed"))
    )
  executor = SimpleNamespace(runs={"run-1": runtime})

  def publish():
    StrategyExecutor.publish_external_durable_state(
      executor,
      "run-1",
      RuntimeStatePatch(set={"instrument_states": {}}),
      durable_state_version=5,
      durable_custom_state=durable,
    )

  if publish_fails:
    with pytest.raises(RuntimeError, match="publication failed"):
      publish()
    assert runtime.status == ExecutionStatus.ERROR
    assert manager._state["version"] == 4
    assert manager.get_custom_state() == {"stale": True}
  else:
    publish()
    assert runtime.status == ExecutionStatus.RUNNING
    assert state.to_dict() == durable
    assert manager._state["version"] == 5
    assert manager.get_custom_state() == durable
    assert manager._durable_custom_state_projection() == durable
    durable["instrument_states"].clear()
    assert manager.get_custom_state()["instrument_states"]


@pytest.mark.asyncio
async def test_external_import_restart_restores_committed_image(monkeypatch):
  from unittest.mock import AsyncMock

  from quantx_infrastructure.database import connection
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repositories,
  )

  durable = {
    "instrument_states": {"600000.SH": {"batch_id": "batch-external"}},
    "runtime_events": [{"type": "T_TRADE_EXTERNAL_ENTRY_IMPORTED"}],
  }
  record = SimpleNamespace(
    version=5, custom_state=durable, cash=1000, frozen_cash=0, total_asset=1000
  )

  async def sessions():
    yield object()

  monkeypatch.setattr(connection, "get_async_db", sessions)
  monkeypatch.setattr(
    repositories, "StrategyRunStateRepository",
    lambda _db: SimpleNamespace(get_state=AsyncMock(return_value=record)),
  )
  monkeypatch.setattr(
    repositories, "StrategyRunPositionRepository",
    lambda _db: SimpleNamespace(get_all_positions=AsyncMock(return_value=[])),
  )
  restarted = RuntimeStateManager(run_id="run-1", persist_enabled=True)
  await restarted.restore()
  assert restarted._state["version"] == 5
  assert restarted.get_strategy_custom_state() == durable


@pytest.mark.asyncio
async def test_failed_publication_fences_checkpoint_force_save_and_repeated_stop(monkeypatch):
  from quantx_infrastructure.database import connection

  database_access = Mock(side_effect=AssertionError("stale generation touched database"))
  monkeypatch.setattr(connection, "get_async_db", database_access)
  manager = RuntimeStateManager(run_id="run-fenced", persist_enabled=True)
  manager._state["version"] = 4
  manager._state["custom"] = {"old": True}
  manager._state_sync_durable_strategy_snapshot = {"old": True}
  with pytest.raises(RuntimeError, match="next runtime CAS"):
    manager.adopt_external_durable_state_version(
      6, custom_state={"imported": True}, publish=Mock(),
    )
  for _ in range(2):
    manager.update_custom_state({"later": True})
    assert await manager.checkpoint_strategy_state_changes() is False
    assert await manager.force_save() is False
    assert await manager.save_snapshot() is False
    with pytest.raises(RuntimeError, match="禁止保存最终快照"):
      await manager.stop()
  database_access.assert_not_called()
  assert manager._state["version"] == 4
  assert manager._external_publication_failed


@pytest.mark.asyncio
async def test_waiting_snapshot_observes_publication_fence_after_lock_acquisition(monkeypatch):
  from quantx_infrastructure.database import connection

  database_access = Mock(side_effect=AssertionError("fenced writer touched database"))
  monkeypatch.setattr(connection, "get_async_db", database_access)
  manager = RuntimeStateManager(run_id="run-waiting-fenced", persist_enabled=True)
  manager.update_custom_state({"old": True})
  await manager._snapshot_lock.acquire()
  saving = asyncio.create_task(manager.force_save())
  await asyncio.sleep(0)
  assert not saving.done()
  manager.fence_external_durable_publication()
  manager._snapshot_lock.release()
  assert await saving is False
  database_access.assert_not_called()


@pytest.mark.asyncio
async def test_external_import_is_ordered_after_prior_market_work_and_blocks_interleave() -> None:
  """Exercise the real dual-queue barrier used by the production import path."""

  runtime = SimpleNamespace(
    context=SimpleNamespace(mode=StrategyRunMode.PAPER),
    strategy=object(),
    status=ExecutionStatus.RUNNING,
    event_task=SimpleNamespace(done=lambda: False),
    event_queue=asyncio.Queue(),
    market_event_queue=asyncio.Queue(),
    _event_queue_wakeup=asyncio.Event(),
  )
  executor = SimpleNamespace(
    runs={"run-1": runtime},
    _put_runtime_control_event=StrategyExecutor._put_runtime_control_event,
  )
  hot_state = {"prior_tick": False, "later_tick": False}
  import_entered = asyncio.Event()
  release_import = asyncio.Event()

  async def import_action():
    assert hot_state == {"prior_tick": True, "later_tick": False}
    import_entered.set()
    await release_import.wait()
    assert hot_state["later_tick"] is False
    return "imported"

  await runtime.market_event_queue.put(("tick", "prior"))
  import_task = asyncio.create_task(
    StrategyExecutor.execute_serialized_t_external_import(
      executor,
      "run-1",
      import_action,
    )
  )
  await asyncio.sleep(0)
  assert runtime.event_queue.empty()

  assert await runtime.market_event_queue.get() == ("tick", "prior")
  hot_state["prior_tick"] = True
  runtime.market_event_queue.task_done()
  event_type, data = await asyncio.wait_for(runtime.event_queue.get(), timeout=1)
  assert event_type == "t_trade_external_import"

  action_task = asyncio.create_task(data["action"]())
  await import_entered.wait()
  await runtime.market_event_queue.put(("tick", "later"))
  await asyncio.sleep(0)
  assert hot_state["later_tick"] is False

  release_import.set()
  result = await action_task
  data["future"].set_result(result)
  runtime.event_queue.task_done()
  assert await import_task == "imported"

  assert await runtime.market_event_queue.get() == ("tick", "later")
  hot_state["later_tick"] = True
  runtime.market_event_queue.task_done()
