from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_engine.strategy_manager as strategy_manager_module
from quantx_domain.strategies.base import StrategyContext
from quantx_engine.strategy_executor import StrategyRuntime
from quantx_engine.strategy_manager import StrategyManager
from quantx_infrastructure.models.enums import StrategyRunMode


@pytest.mark.asyncio
async def test_runtime_completion_converges_replay_projection(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  StrategyManager._instance = None
  manager = StrategyManager()
  run_id = "replay-complete"
  current_time = datetime(2024, 1, 2, 15, 0)
  context = StrategyContext(
    run_id=run_id,
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True, "account_id": "account-1"},
    current_time=current_time,
  )
  manager.executor.runs[run_id] = StrategyRuntime(
    run_id=run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )
  repository = SimpleNamespace(update_run=AsyncMock())
  update_projection = AsyncMock()

  async def fake_get_async_db():
    yield object()

  monkeypatch.setattr(strategy_manager_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    strategy_manager_module,
    "StrategyRunRepository",
    lambda _db: repository,
  )
  monkeypatch.setattr(
    strategy_manager_module.t_trade_replay_projection_service,
    "update",
    update_projection,
  )

  await manager._update_runtime_status(run_id, "COMPLETED")

  repository.update_run.assert_awaited_once()
  update_projection.assert_awaited_once()
  kwargs = update_projection.await_args.kwargs
  assert kwargs["run_id"] == run_id
  assert kwargs["account_id"] == "account-1"
  assert kwargs["status"] == "COMPLETED"
  assert kwargs["progress_pct"] == 100.0
  assert kwargs["processed_until"] == current_time
  assert kwargs["kind"].value == "RESULT_READY"
  manager.executor.runs.clear()
  StrategyManager._instance = None
