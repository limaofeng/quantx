from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from quantx_domain.strategies.base import StrategyRunMode
from quantx_engine.strategy_executor import ExecutionStatus
from quantx_engine.strategy_manager import StrategyManager
from quantx_infrastructure.core.data.adapter import DataAdapter, DataMode
from quantx_infrastructure.core.data.adapter_manager import adapter_manager
from quantx_infrastructure.core.data.historical import HistoricalDataAdapter


class ProbeArchiveAdapter:
  def __init__(self) -> None:
    self.disconnect_calls = 0
    self.is_connected = True

  async def disconnect(self) -> None:
    self.disconnect_calls += 1
    self.is_connected = False


class ProbeHistoricalAdapter(HistoricalDataAdapter):
  def __init__(self) -> None:
    DataAdapter.__init__(self, DataMode.HISTORICAL)
    self.market_data_service = None
    self.current_time = None
    self.replay_tasks = {}
    self.connect_calls = 0
    self.disconnect_calls = 0

  async def connect(self) -> bool:
    self.connect_calls += 1
    self.is_connected = True
    return True

  async def disconnect(self) -> None:
    self.disconnect_calls += 1
    self.is_connected = False


def _bare_manager() -> StrategyManager:
  manager = object.__new__(StrategyManager)
  manager._canonical_archive_adapters = {}
  manager._shutdown_in_progress = False
  manager.logger = SimpleNamespace(info=lambda *_args, **_kwargs: None, error=lambda *_args, **_kwargs: None, exception=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None)
  return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["normal", "error", "cancelled"])
async def test_terminal_callback_releases_archive_adapter_once(terminal: str) -> None:
  manager = _bare_manager()
  adapter = ProbeArchiveAdapter()
  manager._canonical_archive_adapters["archive-run"] = adapter
  updates: list[tuple[str, str]] = []
  manager._update_runtime_status = (  # type: ignore[method-assign]
    lambda run_id, status, *_args: updates.append((run_id, status)) or _done()
  )
  manager.executor = SimpleNamespace(get=lambda _run_id: None)

  if terminal == "normal":
    async def complete() -> None:
      return None

    task = asyncio.create_task(complete())
    await task
  elif terminal == "error":
    async def fail() -> None:
      raise RuntimeError("expected")

    task = asyncio.create_task(fail())
    with pytest.raises(RuntimeError, match="expected"):
      await task
  else:
    task = asyncio.create_task(asyncio.sleep(60))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task

  await manager._on_run_task_done("archive-run", task, executor=manager.executor)
  await manager._release_canonical_archive_adapter("archive-run")

  assert adapter.disconnect_calls == 1
  assert manager._canonical_archive_adapters == {}


async def _done() -> None:
  return None


@pytest.mark.asyncio
async def test_explicit_stop_releases_archive_adapter_idempotently() -> None:
  manager = _bare_manager()
  adapter = ProbeArchiveAdapter()
  manager._canonical_archive_adapters["archive-run"] = adapter
  manager._update_runtime_status = lambda *_args, **_kwargs: _done()  # type: ignore[method-assign]
  runtime = SimpleNamespace(
    context=SimpleNamespace(
      mode=StrategyRunMode.BACKTEST,
      parameters={"t_trade_replay": True},
    ),
    metrics=None,
  )

  async def stop(_run_id: str, *, force: bool) -> bool:
    assert force is True
    return True

  manager.executor = SimpleNamespace(get=lambda _run_id: runtime, stop=stop)

  assert await manager.stop_strategy("archive-run", force=True) is True
  assert await manager.stop_strategy("archive-run", force=True) is True
  assert adapter.disconnect_calls == 1


@pytest.mark.asyncio
async def test_startup_failure_closes_isolated_adapter_without_touching_shared_refcount() -> None:
  manager = _bare_manager()
  adapter = ProbeHistoricalAdapter()
  adapter_manager.reset()
  runtime = SimpleNamespace(
    run_id="archive-run",
    context=SimpleNamespace(
      mode=StrategyRunMode.BACKTEST,
      parameters={"t_trade_replay": True},
      backtest_id=None,
    ),
    status=ExecutionStatus.PENDING,
    error_message=None,
  )

  async def select_archive(_runtime):
    return adapter

  async def data_ready(_runtime, *, canonical_archive_adapter=None):
    assert canonical_archive_adapter is adapter

  async def failed_start(_run_id: str, **_kwargs) -> bool:
    selected = await adapter_manager.get_adapter_for_mode(StrategyRunMode.BACKTEST)
    assert selected is adapter
    assert await adapter_manager.ensure_adapter_connected_for_mode(
      StrategyRunMode.BACKTEST, selected
    )
    await adapter_manager.release_adapter_for_mode("backtest")
    return False

  manager._canonical_archive_adapter_for_runtime = select_archive  # type: ignore[method-assign]
  manager._ensure_backtest_data_available = data_ready  # type: ignore[method-assign]
  manager._update_runtime_status = lambda *_args, **_kwargs: _done()  # type: ignore[method-assign]
  manager.executor = SimpleNamespace(get=lambda _run_id: runtime, start=failed_start)
  try:
    assert await manager.start_strategy("archive-run") is False
    assert adapter.connect_calls == 1
    assert adapter.disconnect_calls == 1
    assert adapter_manager.get_adapter_stats()["historical_refs"] == 0
    assert manager._canonical_archive_adapters == {}
  finally:
    adapter_manager.reset()


@pytest.mark.asyncio
async def test_isolated_lease_never_changes_normal_influx_refcount() -> None:
  shared = ProbeHistoricalAdapter()
  isolated = ProbeHistoricalAdapter()
  adapter_manager.reset()
  adapter_manager._historical_adapter = shared
  try:
    normal = await adapter_manager.get_adapter_for_mode(StrategyRunMode.BACKTEST)
    assert normal is shared
    assert adapter_manager.get_adapter_stats()["historical_refs"] == 1
    async with adapter_manager.isolated_backtest_adapter(isolated):
      selected = await adapter_manager.get_adapter_for_mode(StrategyRunMode.BACKTEST)
      assert selected is isolated
      assert await adapter_manager.ensure_adapter_connected_for_mode(
        StrategyRunMode.BACKTEST, selected
      )
      await adapter_manager.release_adapter_for_mode("backtest")
      assert adapter_manager.get_adapter_stats()["historical_refs"] == 1
    await adapter_manager.release_adapter_for_mode("backtest")
    assert adapter_manager.get_adapter_stats()["historical_refs"] == 0
    assert shared.disconnect_calls == 1
    assert isolated.disconnect_calls == 1
  finally:
    adapter_manager.reset()
