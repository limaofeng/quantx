from __future__ import annotations

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.trading.t_trade_candidate_outcome import (
  CandidateOutcomeDefinition,
  CandidateOutcomeState,
  start_candidate_outcome,
)


def _definition(**overrides):
  values = {
    "candidate_id": "candidate-1",
    "candidate_fingerprint": "a" * 64,
    "strategy_run_id": "run-1",
    "instrument_code": "600000.SH",
    "source_time_ms": 1_000_000,
    "tick_ordinal": 10,
    "continuity_generation": "generation-1",
    "reference_price": 10.0,
    "policy_version": "policy-1",
    "feature_schema_version": "1",
    "horizons_seconds": (60,),
  }
  values.update(overrides)
  return CandidateOutcomeDefinition(**values)


def test_legacy_strategy_run_becomes_explicit_owner_witness():
  state = start_candidate_outcome(_definition())

  restored = CandidateOutcomeState.from_dict(state.to_dict())

  assert restored.definition.execution_ref == ExecutionOwnerRef.strategy_run("run-1")
  assert restored.definition.strategy_run_id == "run-1"
  assert restored.definition.execution_environment is None


def test_paper_t_assistant_owner_round_trips_without_strategy_run_witness():
  owner = ExecutionOwnerRef(
    ExecutionOwnerType.T_ASSISTANT_EXECUTION,
    "execution-1",
  )
  state = start_candidate_outcome(
    _definition(
      strategy_run_id=None,
      execution_ref=owner,
      execution_environment=ExecutionEnvironment.PAPER,
    )
  )

  restored = CandidateOutcomeState.from_dict(state.to_dict())

  assert restored.definition.execution_ref == owner
  assert restored.definition.execution_environment is ExecutionEnvironment.PAPER
  assert restored.definition.strategy_run_id is None


@pytest.mark.parametrize(
  ("strategy_run_id", "environment", "message"),
  [
    ("execution-1", ExecutionEnvironment.PAPER, "不得携带 strategy_run_id"),
    (None, ExecutionEnvironment.LIVE, "只能属于 PAPER"),
  ],
)
def test_t_assistant_owner_rejects_run_witness_and_non_paper_namespace(
  strategy_run_id,
  environment,
  message,
):
  with pytest.raises(ValueError, match=message):
    _definition(
      strategy_run_id=strategy_run_id,
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.T_ASSISTANT_EXECUTION,
        "execution-1",
      ),
      execution_environment=environment,
    )
