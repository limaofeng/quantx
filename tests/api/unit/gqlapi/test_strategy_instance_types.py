from datetime import datetime, timedelta
from types import SimpleNamespace

from quantx_api.gqlapi.types.strategy_types import StrategyInstance
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus


def test_strategy_instance_serializes_legacy_database_times_with_timezone():
  run = SimpleNamespace(
    id="run-1",
    name="打板策略实例",
    strategy_id=1,
    strategy=None,
    instruments=["000001.SZ"],
    parameters={},
    status=StrategyRunStatus.COMPLETED,
    mode=StrategyRunMode.BACKTEST,
    created_at=datetime(2026, 5, 11, 22, 25, 51, 377371),
    updated_at=datetime(2026, 5, 11, 22, 26, 1, 123456),
  )
  last_decision_at = datetime(2026, 5, 11, 22, 25, 59)

  result = StrategyInstance.from_run(
    run,
    last_decision_at=last_decision_at,
  )

  assert result.created_at.utcoffset() == timedelta(hours=8)
  assert result.updated_at.utcoffset() == timedelta(hours=8)
  assert result.last_decision_at is not None
  assert result.last_decision_at.utcoffset() == timedelta(hours=8)
  assert result.created_at.isoformat().endswith("+08:00")
