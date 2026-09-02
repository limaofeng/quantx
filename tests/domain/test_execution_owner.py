import pytest
import quantx_contracts
from quantx_domain.strategies.base import (
  ManualCommandIntentOrigin,
  StrategyRunIntentOrigin,
  TradeIntentOriginType,
)
from quantx_domain.trading.execution_owner import (
  ExecutionEnvironment,
  ExecutionOwnerRef,
  ExecutionOwnerType,
)


def test_owner_type_is_the_frozen_closed_set() -> None:
  assert tuple(item.value for item in ExecutionOwnerType) == (
    "STRATEGY_RUN",
    "T_ASSISTANT_EXECUTION",
    "ENTRY_PLAN",
    "BOARD_ASSISTANT_EXECUTION",
    "EXIT_PLAN",
    "MANUAL_COMMAND",
  )
  assert TradeIntentOriginType is ExecutionOwnerType
  assert quantx_contracts.ExecutionOwnerType is ExecutionOwnerType


def test_execution_environment_is_explicit_and_closed() -> None:
  assert tuple(item.value for item in ExecutionEnvironment) == (
    "PAPER",
    "LIVE",
    "BACKTEST",
  )
  assert quantx_contracts.ExecutionEnvironment is ExecutionEnvironment


def test_owner_ref_validates_and_serializes_canonical_identity() -> None:
  owner = ExecutionOwnerRef("ENTRY_PLAN", "plan-1")

  assert owner.owner_type is ExecutionOwnerType.ENTRY_PLAN
  assert owner.to_dict() == {
    "owner_type": "ENTRY_PLAN",
    "owner_id": "plan-1",
  }
  assert ExecutionOwnerRef.from_mapping(owner.to_dict()) == owner
  assert quantx_contracts.ExecutionOwnerRef is ExecutionOwnerRef


@pytest.mark.parametrize(
  ("owner_type", "owner_id", "reason"),
  [
    ("UNKNOWN", "owner-1", "OWNER_TYPE_INVALID"),
    ("STRATEGY_RUN", "", "OWNER_ID_MISSING"),
    ("STRATEGY_RUN", " run-1", "OWNER_ID_INVALID"),
    ("STRATEGY_RUN", "run-1\n", "OWNER_ID_INVALID"),
    ("STRATEGY_RUN", "x" * 129, "OWNER_ID_INVALID"),
  ],
)
def test_owner_ref_rejects_invalid_identity(
  owner_type: str,
  owner_id: str,
  reason: str,
) -> None:
  with pytest.raises(ValueError, match=f"^{reason}$"):
    ExecutionOwnerRef(owner_type, owner_id)


def test_owner_ref_rejects_target_conflict() -> None:
  owner = ExecutionOwnerRef.strategy_run("run-1")

  assert owner.require_matches("STRATEGY_RUN", "run-1") is owner
  with pytest.raises(ValueError, match="^OWNER_CONFLICT$"):
    owner.require_matches("STRATEGY_RUN", "run-2")
  with pytest.raises(ValueError, match="^OWNER_CONFLICT$"):
    owner.require_matches("UNKNOWN", "run-1")


def test_existing_origins_project_the_strong_owner_ref() -> None:
  strategy = StrategyRunIntentOrigin(run_id="run-1", strategy_id="strategy-1")
  manual = ManualCommandIntentOrigin(command_id="command-1", action_type="BUY")

  assert strategy.execution_ref == ExecutionOwnerRef.strategy_run("run-1")
  assert manual.execution_ref == ExecutionOwnerRef.manual_command("command-1")
