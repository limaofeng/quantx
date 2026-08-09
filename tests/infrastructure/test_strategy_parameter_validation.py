"""Production validation for strategy-owned configuration."""

import pytest
from quantx_domain.strategies.ashare_limit_up_board import (
  AshareLimitUpBoardStrategy,
)
from quantx_infrastructure.models.parameter_schema import (
  validate_strategy_configuration,
)

pytestmark = pytest.mark.unit


def test_board_configuration_merges_defaults_and_preserves_runtime_fields():
  parameters = validate_strategy_configuration(
    AshareLimitUpBoardStrategy,
    {
      "instrument_code": "000001.SZ",
      "target_position_pct": 0.08,
    },
  )

  assert parameters["instrument_code"] == "000001.SZ"
  assert parameters["target_position_pct"] == pytest.approx(0.08)
  assert parameters["entry_execution_mode"] == "MANUAL_CONFIRM"
  assert parameters["auto_approve_manual_intents"] is True


@pytest.mark.parametrize(
  "parameters",
  [
    {"target_position_pct": 0.31},
    {"target_position_pct": True},
    {"entry_execution_mode": "UNSAFE"},
    {"entry_start_time": "14:51", "entry_end_time": "14:50"},
  ],
)
def test_board_configuration_rejects_invalid_values(parameters):
  with pytest.raises(ValueError):
    validate_strategy_configuration(AshareLimitUpBoardStrategy, parameters)
