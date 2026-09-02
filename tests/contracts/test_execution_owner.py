from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest
from quantx_contracts import (
  ExecutionEnvironment,
  ExecutionOwnerRef,
  ExecutionOwnerType,
)


def test_owner_enums_are_closed_and_exact() -> None:
  assert tuple(item.value for item in ExecutionOwnerType) == (
    "STRATEGY_RUN",
    "T_ASSISTANT_EXECUTION",
    "ENTRY_PLAN",
    "BOARD_ASSISTANT_EXECUTION",
    "EXIT_PLAN",
    "MANUAL_COMMAND",
  )
  assert tuple(item.value for item in ExecutionEnvironment) == (
    "PAPER",
    "LIVE",
    "BACKTEST",
  )


def test_owner_ref_is_a_frozen_slots_value_object() -> None:
  assert is_dataclass(ExecutionOwnerRef)
  assert ExecutionOwnerRef.__slots__ == ("owner_type", "owner_id")
  assert tuple(item.name for item in fields(ExecutionOwnerRef)) == (
    "owner_type",
    "owner_id",
  )

  owner = ExecutionOwnerRef("ENTRY_PLAN", "plan-1")
  assert owner.owner_type is ExecutionOwnerType.ENTRY_PLAN
  assert owner.owner_id == "plan-1"
  with pytest.raises(FrozenInstanceError):
    owner.owner_id = "plan-2"


def test_owner_ref_mapping_factories_and_projection() -> None:
  owner = ExecutionOwnerRef.from_mapping(
    {"owner_type": "EXIT_PLAN", "owner_id": "plan-1", "ignored": True}
  )
  assert owner == ExecutionOwnerRef(ExecutionOwnerType.EXIT_PLAN, "plan-1")
  assert owner.to_dict() == {
    "owner_type": "EXIT_PLAN",
    "owner_id": "plan-1",
  }
  assert ExecutionOwnerRef.strategy_run("run-1").to_dict() == {
    "owner_type": "STRATEGY_RUN",
    "owner_id": "run-1",
  }
  assert ExecutionOwnerRef.manual_command("command-1").to_dict() == {
    "owner_type": "MANUAL_COMMAND",
    "owner_id": "command-1",
  }


@pytest.mark.parametrize(
  ("owner_type", "owner_id", "reason"),
  [
    ("UNKNOWN", "owner-1", "OWNER_TYPE_INVALID"),
    (None, "owner-1", "OWNER_TYPE_INVALID"),
    ("STRATEGY_RUN", None, "OWNER_ID_MISSING"),
    ("STRATEGY_RUN", "", "OWNER_ID_MISSING"),
    ("STRATEGY_RUN", " run-1", "OWNER_ID_INVALID"),
    ("STRATEGY_RUN", "run-1 ", "OWNER_ID_INVALID"),
    ("STRATEGY_RUN", "run-1\n", "OWNER_ID_INVALID"),
    ("STRATEGY_RUN", "run-1\x7f", "OWNER_ID_INVALID"),
    ("STRATEGY_RUN", 123, "OWNER_ID_INVALID"),
    ("STRATEGY_RUN", "x" * 129, "OWNER_ID_INVALID"),
  ],
)
def test_owner_ref_rejects_invalid_identity(
  owner_type: object,
  owner_id: object,
  reason: str,
) -> None:
  with pytest.raises(ValueError, match=f"^{reason}$"):
    ExecutionOwnerRef(owner_type, owner_id)


@pytest.mark.parametrize(
  "value",
  [
    {},
    {"owner_type": "STRATEGY_RUN"},
    {"owner_type": "UNKNOWN", "owner_id": "run-1"},
    {"owner_type": "STRATEGY_RUN", "owner_id": " run-1"},
  ],
)
def test_from_mapping_applies_the_same_validation(value: object) -> None:
  with pytest.raises(ValueError):
    ExecutionOwnerRef.from_mapping(value)


def test_require_matches_is_exact_and_fail_closed() -> None:
  owner = ExecutionOwnerRef.strategy_run("run-1")
  assert owner.require_matches("STRATEGY_RUN", "run-1") is owner

  for owner_type, owner_id in (
    ("STRATEGY_RUN", "run-2"),
    ("MANUAL_COMMAND", "run-1"),
    ("UNKNOWN", "run-1"),
    ("STRATEGY_RUN", " run-1"),
  ):
    with pytest.raises(ValueError, match="^OWNER_CONFLICT$"):
      owner.require_matches(owner_type, owner_id)
