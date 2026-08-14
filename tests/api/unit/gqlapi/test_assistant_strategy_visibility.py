from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_api.gqlapi.resolvers.strategies as strategies_module
from quantx_api.gqlapi.resolvers.strategies import StrategyResolver


async def _fake_db():
  yield object()


@pytest.mark.asyncio
async def test_strategy_definitions_hide_assistant_managed_by_default(monkeypatch):
  models = [
    SimpleNamespace(class_name="OrdinaryStrategy", id=1),
    SimpleNamespace(class_name="AshareLimitUpBoardStrategy", id=2),
    SimpleNamespace(class_name="AshareIntradayTAssistantStrategy", id=3),
  ]
  repository = SimpleNamespace(get_all_strategies=AsyncMock(return_value=models))
  monkeypatch.setattr(strategies_module, "get_async_db", _fake_db)
  monkeypatch.setattr(
    strategies_module,
    "StrategyRepository",
    lambda _db: repository,
  )
  monkeypatch.setattr(
    strategies_module.StrategyDefinition,
    "from_strategy",
    lambda model: model.id,
  )

  assert await StrategyResolver.get_strategy_definitions() == [1]
  assert await StrategyResolver.get_strategy_definitions(
    include_assistant_managed=True
  ) == [1, 2, 3]


@pytest.mark.asyncio
async def test_strategy_instances_hide_assistant_managed_by_default(monkeypatch):
  runs = [
    SimpleNamespace(
      id="ordinary",
      strategy_id=1,
      strategy=SimpleNamespace(name="普通策略", class_name="OrdinaryStrategy"),
      status="running",
      instruments=["600000.SH"],
    ),
    SimpleNamespace(
      id="board",
      strategy_id=2,
      strategy=SimpleNamespace(
        name="A股单标的打板策略",
        class_name="AshareLimitUpBoardStrategy",
      ),
      status="running",
      instruments=["300001.SZ"],
    ),
  ]
  repository = SimpleNamespace(find_all_strategy_runs=AsyncMock(return_value=runs))
  monkeypatch.setattr(strategies_module, "get_async_db", _fake_db)
  monkeypatch.setattr(
    strategies_module,
    "StrategyRunRepository",
    lambda _db: repository,
  )
  project = AsyncMock(side_effect=lambda _db, run: run.id)
  monkeypatch.setattr(StrategyResolver, "_instance_from_run_model", project)

  assert await StrategyResolver.get_strategy_instances() == ["ordinary"]
  assert await StrategyResolver.get_strategy_instances(
    include_assistant_managed=True
  ) == ["ordinary", "board"]
