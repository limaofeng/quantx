from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.gqlapi.resolvers import strategies as strategies_module
from quantx_api.gqlapi.resolvers.strategies import StrategyResolver
from quantx_api.gqlapi.types.strategy_types import (
  StrategyInstanceParameterUpdateInput,
)
from quantx_domain.strategies.ashare_dynamic_balance_dual_bucket import (
  AshareDynamicBalanceDualBucketStrategy,
)


def _parameters() -> dict:
  return {
    "_parameter_version": "1",
    "account_id": "ACCOUNT-1",
    "instrument_code": "600519.SH",
    "stockCodes": ["600519.SH"],
  }


def test_mobile_parameter_projection_is_explicit_and_versioned():
  projection = StrategyResolver._mobile_parameter_projection(
    instance_id="run-1",
    strategy_class=AshareDynamicBalanceDualBucketStrategy,
    parameters=_parameters(),
  )

  assert projection.instance_id == "run-1"
  assert projection.config_version == "1"
  assert projection.editable is True
  assert {item.key for item in projection.parameters} == {
    "cash_buffer_pct",
    "downtrend_grid_buy_block",
    "min_expected_profit_bps",
    "rebalance_threshold_pct",
  }
  assert "max_position_pct" not in {item.key for item in projection.parameters}
  assert all(item.apply_immediately is False for item in projection.parameters)
  assert {item.risk_level for item in projection.parameters} <= {"LOW", "MEDIUM"}


@pytest.mark.parametrize(
  ("key", "value", "message"),
  [
    ("cash_buffer_pct", True, "必须是数值"),
    ("cash_buffer_pct", 0.205, "不符合服务端步长"),
    ("cash_buffer_pct", 0.81, "大于服务端最大值"),
    ("downtrend_grid_buy_block", 1, "必须是布尔值"),
  ],
)
def test_mobile_parameter_value_validation_is_strongly_typed(
  key: str,
  value,
  message: str,
):
  prop = StrategyResolver._mobile_parameter_properties(
    AshareDynamicBalanceDualBucketStrategy
  )[key]

  with pytest.raises(ValueError, match=message):
    StrategyResolver._validate_mobile_parameter_value(key, value, prop)


class _FakeRunRepository:
  def __init__(self, run) -> None:
    self.run = run
    self.update_run = AsyncMock(side_effect=self._update)

  async def find_run_by_id_for_update(self, _run_id: str):
    return self.run

  async def find_run_by_id(self, _run_id: str):
    return self.run

  async def _update(self, _run_id: str, values: dict):
    self.run.parameters = values["parameters"]
    return self.run


def _run() -> SimpleNamespace:
  return SimpleNamespace(
    id="run-1",
    parameters=_parameters(),
    strategy=SimpleNamespace(class_name="ignored", file_path="ignored"),
    instruments=["600519.SH"],
    status=SimpleNamespace(value="running"),
    mode=SimpleNamespace(value="paper"),
  )


async def _fake_db():
  yield object()


@pytest.mark.asyncio
async def test_native_mobile_update_uses_allowlist_version_lock_and_draft(monkeypatch):
  run = _run()
  repo = _FakeRunRepository(run)
  expected = object()
  engine_request = AsyncMock(return_value={})
  monkeypatch.setattr(strategies_module, "get_async_db", _fake_db)
  monkeypatch.setattr(strategies_module, "StrategyRunRepository", lambda _db: repo)
  monkeypatch.setattr(
    strategies_module.strategy_registry,
    "get_strategy_class",
    lambda *_args: AshareDynamicBalanceDualBucketStrategy,
  )
  monkeypatch.setattr(StrategyResolver, "_engine_request", engine_request)
  monkeypatch.setattr(
    StrategyResolver,
    "_instance_from_run_model",
    AsyncMock(return_value=expected),
  )

  actual = await StrategyResolver.update_strategy_instance_parameters(
    "run-1",
    StrategyInstanceParameterUpdateInput(
      parameters={"cash_buffer_pct": 0.30},
      expected_version="1",
    ),
    mobile_only=True,
  )

  assert actual is expected
  assert run.parameters["_parameter_version"] == "1"
  assert run.parameters["_mobile_config_version"] == "2"
  assert run.parameters["_parameter_draft"]["cash_buffer_pct"] == 0.30
  assert "cash_buffer_pct" not in run.parameters
  assert engine_request.await_args.kwargs["idempotency_key"].endswith(":2")


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("parameters", "expected_version", "message"),
  [
    ({"max_position_pct": 0.5}, "1", "allowlist"),
    ({"cash_buffer_pct": 0.3}, "2", "VERSION_CONFLICT"),
    ({"cash_buffer_pct": 0.3}, None, "expectedVersion"),
  ],
)
async def test_native_mobile_update_rejects_unknown_or_stale_changes(
  monkeypatch,
  parameters: dict,
  expected_version: str | None,
  message: str,
):
  run = _run()
  repo = _FakeRunRepository(run)
  monkeypatch.setattr(strategies_module, "get_async_db", _fake_db)
  monkeypatch.setattr(strategies_module, "StrategyRunRepository", lambda _db: repo)
  monkeypatch.setattr(
    strategies_module.strategy_registry,
    "get_strategy_class",
    lambda *_args: AshareDynamicBalanceDualBucketStrategy,
  )

  with pytest.raises(ValueError, match=message):
    await StrategyResolver.update_strategy_instance_parameters(
      "run-1",
      StrategyInstanceParameterUpdateInput(
        parameters=parameters,
        expected_version=expected_version,
      ),
      mobile_only=True,
    )

  repo.update_run.assert_not_awaited()
