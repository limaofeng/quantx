"""Terminal replay evidence must survive cancellation, failure and rerun."""

import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from quantx_domain.strategies.base import StrategyContext, StrategyRunMode
from quantx_engine import strategy_manager as manager_module
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_engine.strategy_manager import StrategyManager
from quantx_infrastructure.core.backtest_result_storage import BacktestResultStorage
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager
from quantx_infrastructure.models.enums import StrategyRunStatus


@pytest.fixture
def replay_runtime(monkeypatch):
  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id="replay-evidence",
    name="replay-evidence",
    strategy_id=1,
    strategy_class=object,
    context=StrategyContext(
      run_id="replay-evidence",
      mode=StrategyRunMode.BACKTEST,
      instruments=["600000.SH"],
      parameters={"t_trade_replay": True, "account_id": "test-account"},
      backtest_id="bt",
      backtest_version=2,
    ),
  )
  calls = []

  def operation(name, result=None):
    async def run(*_args, **_kwargs):
      calls.append(name)
      return result

    return AsyncMock(side_effect=run)

  runtime.status = ExecutionStatus.RUNNING
  runtime.strategy = SimpleNamespace(
    logger=logging.getLogger("replay-evidence-test"),
    state=SimpleNamespace(to_dict=lambda: {}),
    stop=operation("strategy-stop"),
  )
  manager = RuntimeStateManager(run_id=runtime.run_id, persist_enabled=False)
  monkeypatch.setattr(manager, "stop_state_sync", operation("state-sync-stop"))
  monkeypatch.setattr(manager, "stop", operation("snapshot-stop"))
  monkeypatch.setattr(
    manager, "finalize_backtest", operation("archive", "manifest.json")
  )
  runtime.state_manager = manager
  runtime.broker = SimpleNamespace(
    orders={},
    is_connected=False,
    disconnect=operation("disconnect"),
  )
  monkeypatch.setattr(executor, "_coordinate_terminal_session_checkpoint", AsyncMock())
  monkeypatch.setattr(
    executor, "_coordinate_backtest_terminal_checkpoint", operation("checkpoint")
  )
  monkeypatch.setattr(
    executor, "_flush_t_trade_opportunity_diagnostics", operation("diagnostics")
  )
  monkeypatch.setattr(executor, "_finalize_t_trade_candidate_outcomes", AsyncMock())
  executor.runs[runtime.run_id] = runtime
  yield executor, runtime, manager, calls
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["CANCELLED", "ERROR"])
async def test_terminal_replay_seals_after_checkpoint_before_release(
  replay_runtime, terminal
):
  executor, runtime, manager, calls = replay_runtime
  if terminal == "CANCELLED":
    assert await executor.stop(runtime.run_id) is True
    assert runtime.status == ExecutionStatus.STOPPED
  else:
    runtime.status = ExecutionStatus.ERROR
    await executor._ensure_terminal_cleanup(runtime)
    assert runtime.status == ExecutionStatus.ERROR
    assert runtime._terminal_cleanup_complete is True

  assert calls == [
    "strategy-stop",
    "checkpoint",
    "diagnostics",
    "state-sync-stop",
    "snapshot-stop",
    "archive",
    "disconnect",
  ]
  manager.finalize_backtest.assert_awaited_once_with(
    opportunity_account_id="test-account"
  )
  executor._coordinate_backtest_terminal_checkpoint.assert_awaited_once_with(
    runtime, cause=terminal
  )


@pytest.mark.asyncio
async def test_failed_archive_retains_owner_and_can_retry(replay_runtime):
  executor, runtime, manager, _calls = replay_runtime
  manager.finalize_backtest.side_effect = [
    RuntimeError("archive failed"),
    "manifest.json",
  ]
  runtime.status = ExecutionStatus.ERROR
  await executor._ensure_terminal_cleanup(runtime)
  assert runtime._terminal_cleanup_complete is False
  runtime.broker.disconnect.assert_not_awaited()
  assert executor.get(runtime.run_id) is runtime

  await executor._ensure_terminal_cleanup(runtime)
  assert runtime._terminal_cleanup_complete is True
  assert manager.finalize_backtest.await_count == 2
  runtime.broker.disconnect.assert_awaited_once()
  assert runtime.status == ExecutionStatus.ERROR


@pytest.mark.asyncio
async def test_stop_cannot_report_success_when_archive_fails(replay_runtime):
  executor, runtime, manager, _calls = replay_runtime
  manager.finalize_backtest.side_effect = RuntimeError("archive failed")
  assert await executor.stop(runtime.run_id) is False
  assert runtime.status == ExecutionStatus.ERROR
  assert runtime._terminal_cleanup_complete is False
  runtime.broker.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsealed_checkpoint_cannot_publish_terminal_archive(replay_runtime):
  executor, runtime, manager, _calls = replay_runtime
  executor._coordinate_backtest_terminal_checkpoint.side_effect = RuntimeError(
    "checkpoint blocked"
  )
  runtime.status = ExecutionStatus.ERROR
  await executor._ensure_terminal_cleanup(runtime)
  assert runtime._terminal_cleanup_complete is False
  manager.finalize_backtest.assert_not_awaited()
  runtime.broker.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_stopped_runtime_checks_archive_before_it_can_be_deleted(replay_runtime):
  executor, runtime, manager, _calls = replay_runtime
  runtime.status = ExecutionStatus.STOPPED
  manager.finalize_backtest.side_effect = RuntimeError("archive failed")
  assert await executor.delete(runtime.run_id) is False
  assert executor.get(runtime.run_id) is runtime
  manager.finalize_backtest.side_effect = None
  assert await executor.delete(runtime.run_id) is True
  assert executor.get(runtime.run_id) is None


@pytest.mark.asyncio
async def test_never_started_replay_can_stop_without_inventing_archive(replay_runtime):
  executor, runtime, manager, _calls = replay_runtime
  runtime.status = ExecutionStatus.ERROR
  runtime.strategy = None
  runtime.state_manager = None
  assert await executor.stop(runtime.run_id) is True
  manager.finalize_backtest.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["STOP", "ERROR"])
async def test_process_shutdown_preserves_recovery_without_sealing_version(
  replay_runtime, terminal
):
  executor, runtime, manager, _calls = replay_runtime
  executor._shutdown_event.set()
  if terminal == "STOP":
    assert await executor.stop(runtime.run_id) is True
  else:
    await executor._ensure_terminal_cleanup(runtime)
  manager.stop.assert_awaited_once()
  manager.finalize_backtest.assert_not_awaited()


@pytest.mark.asyncio
async def test_natural_completion_can_still_seal_during_engine_shutdown(replay_runtime):
  executor, runtime, manager, _calls = replay_runtime
  executor._shutdown_event.set()
  assert await executor._archive_t_trade_replay_evidence(runtime) == "manifest.json"
  manager.finalize_backtest.assert_awaited_once_with(
    opportunity_account_id="test-account"
  )


def _manager():
  # No singleton initialization, background runtime or database connection.
  return object.__new__(StrategyManager)


def _backtest(tmp_path, status):
  return SimpleNamespace(
    id="bt",
    version=2,
    status=status,
    strategy_run_id="replay-evidence",
    result_path=str(tmp_path / "replay-evidence/v2/manifest.json"),
    created_at=datetime(2026, 8, 31, 9, 30),
  )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["ERROR", "CANCELLED"])
@pytest.mark.parametrize("fact_index", range(4))
async def test_missing_terminal_archive_never_allows_removing_durable_facts(
  tmp_path,
  terminal,
  fact_index,
):
  manager = _manager()
  db = SimpleNamespace(
    scalar=AsyncMock(side_effect=[None] * fact_index + ["durable-fact"])
  )
  with pytest.raises(ValueError, match="证据尚未归档"):
    await manager._assert_replay_evidence_archived(
      db,
      run_id="replay-evidence",
      latest=_backtest(tmp_path, terminal),
      account_id="test-account",
    )
  assert db.scalar.await_count == fact_index + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["ERROR", "CANCELLED"])
async def test_empty_prestart_failure_can_rerun_without_fake_archive(
  tmp_path, terminal
):
  manager = _manager()
  db = SimpleNamespace(scalar=AsyncMock(return_value=None))
  latest = _backtest(tmp_path, terminal)
  await manager._assert_replay_evidence_archived(
    db,
    run_id="replay-evidence",
    latest=latest,
    account_id="test-account",
  )
  assert db.scalar.await_count == 4
  decision_query = db.scalar.await_args.args[0]
  assert latest.created_at in decision_query.compile().params.values()
  assert not (tmp_path / "replay-evidence/v2/manifest.json").exists()


@pytest.mark.asyncio
async def test_completed_version_requires_archive_even_without_hot_facts(tmp_path):
  db = SimpleNamespace(scalar=AsyncMock(return_value=None))
  with pytest.raises(ValueError, match="归档缺失"):
    await _manager()._assert_replay_evidence_archived(
      db,
      run_id="replay-evidence",
      latest=_backtest(tmp_path, "COMPLETED"),
      account_id="test-account",
    )
  db.scalar.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["COMPLETED", "ERROR", "CANCELLED"])
async def test_sealed_terminal_version_allows_rerun(tmp_path, terminal):
  storage = BacktestResultStorage("bt", str(tmp_path), "replay-evidence", 2)

  async def records():
    yield {
      "strategy_run_id": "replay-evidence",
      "account_id": "test-account",
      "record_kind": "MATERIAL",
      "event_type": "CANDIDATE_SUPPRESSED",
    }

  await storage.archive_opportunity_evaluations(records(), account_id="test-account")
  await storage.flush()
  db = SimpleNamespace(scalar=AsyncMock())
  await _manager()._assert_replay_evidence_archived(
    db,
    run_id="replay-evidence",
    latest=_backtest(tmp_path, terminal),
    account_id="test-account",
  )
  db.scalar.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_succeeds", [False, True])
async def test_rerun_cleanup_and_archive_guard_precede_any_destructive_reset(
  tmp_path,
  monkeypatch,
  cleanup_succeeds,
):
  from quantx_infrastructure.repositories import backtest_repository

  manager = _manager()
  calls = []

  async def cleanup(_run_id):
    calls.append("cleanup")
    return cleanup_succeeds

  async def check_facts(_statement):
    calls.append("archive-guard")
    return "unarchived-event"

  manager.executor = SimpleNamespace(
    get=MagicMock(return_value=SimpleNamespace(status=ExecutionStatus.ERROR)),
    delete=AsyncMock(side_effect=cleanup),
  )
  db = SimpleNamespace(scalar=AsyncMock(side_effect=check_facts), commit=AsyncMock())

  async def database():
    yield db

  run_repo = SimpleNamespace(
    find_run_by_id=AsyncMock(
      return_value=SimpleNamespace(
        mode=StrategyRunMode.BACKTEST,
        status=StrategyRunStatus.ERROR,
        parameters={"t_trade_replay": True, "account_id": "test-account"},
        instruments=["600000.SH"],
      )
    )
  )
  backtest_repo = SimpleNamespace(
    get_backtests_by_run=AsyncMock(return_value=[_backtest(tmp_path, "ERROR")]),
    create_backtest=AsyncMock(),
  )
  monkeypatch.setattr(manager_module, "get_async_db", database)
  monkeypatch.setattr(manager_module, "StrategyRunRepository", lambda _db: run_repo)
  monkeypatch.setattr(
    backtest_repository, "BacktestRepository", lambda _db: backtest_repo
  )
  reset = AsyncMock()
  monkeypatch.setattr(
    manager_module.TTradeOpportunityEvaluationRepository,
    "reset_for_backtest_rerun",
    reset,
  )

  with pytest.raises(ValueError, match="禁止"):
    await manager.rerun_backtest_version("replay-evidence")
  assert calls == (["cleanup", "archive-guard"] if cleanup_succeeds else ["cleanup"])
  reset.assert_not_awaited()
  backtest_repo.create_backtest.assert_not_awaited()
  db.commit.assert_not_awaited()
