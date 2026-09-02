"""Pure QuantX trading domain.

This package must remain free of database, filesystem, network, FastAPI,
Prefect, and QMT dependencies. Applications adapt persistence models into
these values.
"""

from .stock_selection_training import (
  BackendDecision,
  BackendResolutionError,
  GateConclusion,
  GateEvidence,
  Progress,
  RequestedBackend,
  ResolvedBackend,
  RunKind,
  RunStatus,
  TrainingPhase,
  TrainingTimeSplit,
  WalkForwardWindow,
  advance_phase,
  build_training_time_split,
  can_transition_status,
  canonical_json,
  gate_conclusion,
  phase_index,
  resolve_backend,
  stable_json_sha256,
  transition_run_status,
  validate_progress,
  validate_time_split,
)
from .strategies.base import (
  StrategyBase,
  StrategyInput,
  StrategyOutput,
  TradeIntent,
)

__all__ = [
  "StrategyBase",
  "StrategyInput",
  "StrategyOutput",
  "TradeIntent",
  "BackendDecision",
  "BackendResolutionError",
  "GateConclusion",
  "GateEvidence",
  "Progress",
  "RequestedBackend",
  "ResolvedBackend",
  "RunKind",
  "RunStatus",
  "TrainingPhase",
  "TrainingTimeSplit",
  "WalkForwardWindow",
  "advance_phase",
  "build_training_time_split",
  "can_transition_status",
  "canonical_json",
  "gate_conclusion",
  "phase_index",
  "resolve_backend",
  "stable_json_sha256",
  "transition_run_status",
  "validate_progress",
  "validate_time_split",
]
