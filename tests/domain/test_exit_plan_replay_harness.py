from quantx_domain.enums import StrategyInstrumentScope
from quantx_domain.strategies.ashare_exit_plan_replay_harness import (
  AshareExitPlanReplayHarnessStrategy,
)


def test_exit_plan_replay_harness_is_single_instrument_tick_only() -> None:
  assert (
    AshareExitPlanReplayHarnessStrategy.INSTRUMENT_SCOPE
    == StrategyInstrumentScope.SINGLE
  )
  assert AshareExitPlanReplayHarnessStrategy.get_data_requirements() == {
    "use_tick_data": True,
    "periods": [],
  }
  schema = AshareExitPlanReplayHarnessStrategy.get_parameter_schema()
  assert schema.additionalProperties is True
