from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_engine.command_processor as command_processor


class _SessionContext:
  async def __aenter__(self):
    return object()

  async def __aexit__(self, exc_type, exc, traceback):
    return False


@pytest.mark.asyncio
async def test_limit_up_creation_rejects_active_same_instrument(monkeypatch):
  strategy = SimpleNamespace(
    id=42,
    class_name="AshareLimitUpBoardStrategy",
    file_path="strategy.py",
  )
  active_run = SimpleNamespace(
    id="existing-run",
    instruments=["300001.SZ"],
    parameters={"account_id": "account-1"},
  )
  run_strategy = AsyncMock()
  manager = SimpleNamespace(get_run=lambda _run_id: None, run_strategy=run_strategy)
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", _SessionContext)
  monkeypatch.setattr(
    command_processor,
    "StrategyRepository",
    lambda _db: SimpleNamespace(find_by_id=AsyncMock(return_value=strategy)),
  )
  monkeypatch.setattr(
    command_processor,
    "StrategyRunRepository",
    lambda _db: SimpleNamespace(
      find_active_runs_by_strategy_class=AsyncMock(return_value=[active_run])
    ),
  )
  monkeypatch.setattr(command_processor, "strategy_manager", manager)

  with pytest.raises(
    ValueError,
    match="ACTIVE_LIMIT_UP_INSTANCE_EXISTS:existing-run",
  ):
    await command_processor._strategy_create(
      {
        "run_id": "new-run",
        "strategy_id": 42,
        "mode": "paper",
        "instruments": ["300001.SZ"],
        "parameters": {"account_id": "account-1"},
      }
    )

  run_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_limit_up_backtests_can_coexist(monkeypatch):
  strategy = SimpleNamespace(
    id=42,
    class_name="AshareLimitUpBoardStrategy",
    file_path="strategy.py",
  )
  run_strategy = AsyncMock(return_value="new-run")
  manager = SimpleNamespace(get_run=lambda _run_id: None, run_strategy=run_strategy)
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", _SessionContext)
  monkeypatch.setattr(
    command_processor,
    "StrategyRepository",
    lambda _db: SimpleNamespace(find_by_id=AsyncMock(return_value=strategy)),
  )
  monkeypatch.setattr(command_processor, "strategy_manager", manager)
  monkeypatch.setattr(
    command_processor.strategy_registry,
    "get_strategy_class",
    lambda *_args: SimpleNamespace,
  )

  result = await command_processor._strategy_create(
    {
      "run_id": "new-run",
      "strategy_id": 42,
      "mode": "backtest",
      "instruments": ["300001.SZ"],
      "parameters": {},
      "auto_start": False,
    }
  )

  assert result["run_id"] == "new-run"
  run_strategy.assert_awaited_once()
