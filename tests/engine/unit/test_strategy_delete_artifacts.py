from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import quantx_engine.command_processor as command_processor


class _SessionContext:
  async def __aenter__(self):
    return object()

  async def __aexit__(self, exc_type, exc, traceback):
    return False


@pytest.mark.asyncio
async def test_delete_strategy_cleans_backtest_artifacts_after_database_delete(
  monkeypatch,
) -> None:
  backtest = SimpleNamespace(
    id="backtest-1",
    result_path="backtests/run-1/v1/manifest.json",
  )
  backtest_repo = SimpleNamespace(
    get_backtests_by_run=AsyncMock(return_value=[backtest])
  )
  run_repo = SimpleNamespace(delete_run=AsyncMock(return_value=True))
  cleanup = Mock()
  manager = SimpleNamespace(get_run=lambda _run_id: None)

  monkeypatch.setattr(command_processor, "AsyncSessionLocal", _SessionContext)
  monkeypatch.setattr(
    command_processor, "BacktestRepository", lambda _db: backtest_repo
  )
  monkeypatch.setattr(command_processor, "StrategyRunRepository", lambda _db: run_repo)
  monkeypatch.setattr(command_processor, "delete_backtest_artifacts", cleanup)
  monkeypatch.setattr(command_processor, "strategy_manager", manager)

  result = await command_processor._delete_strategy("run-1")

  assert result == {"success": True}
  backtest_repo.get_backtests_by_run.assert_awaited_once_with("run-1")
  run_repo.delete_run.assert_awaited_once_with("run-1")
  cleanup.assert_called_once_with(
    "backtest-1",
    "backtests/run-1/v1/manifest.json",
  )
