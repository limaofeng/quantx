"""Application boundaries for stateful T-trade opportunity V3."""

from .account_facts import (
  T_TRADE_ACCOUNT_SNAPSHOT_STALE,
  T_TRADE_ACCOUNT_SNAPSHOT_TOO_LARGE,
  TTradeAccountFacts,
  compute_t_trade_account_facts,
)
from .contracts import (
  D1ProfileReadReason,
  D1ProfileReadRequest,
  D1ProfileReadResult,
  EvaluationMaterializationResult,
  EvaluationMaterializationStatus,
  IntentEmissionGateInput,
  IntentEmissionGateResult,
  PostCasEvaluationInput,
  SignalPolicyChangePlan,
  SignalPolicyChangeRequest,
  SignalPolicyConfigSnapshot,
)
from .ports import (
  D1ReferenceProfilePort,
  OpportunityEvaluationMaterializerPort,
  ProfilePayload,
)
from .use_cases import (
  EvaluateIntentEmissionGate,
  EvaluationMaterializationError,
  MaterializeEvaluationAfterCAS,
  ReadD1ReferenceProfile,
  SignalPolicyChangePlanner,
  normalize_signal_policy,
)

__all__ = [
  "D1ProfileReadReason",
  "D1ProfileReadRequest",
  "D1ProfileReadResult",
  "D1ReferenceProfilePort",
  "EvaluationMaterializationError",
  "EvaluationMaterializationResult",
  "EvaluationMaterializationStatus",
  "EvaluateIntentEmissionGate",
  "IntentEmissionGateInput",
  "IntentEmissionGateResult",
  "MaterializeEvaluationAfterCAS",
  "OpportunityEvaluationMaterializerPort",
  "PostCasEvaluationInput",
  "ProfilePayload",
  "ReadD1ReferenceProfile",
  "SignalPolicyChangePlan",
  "SignalPolicyChangePlanner",
  "SignalPolicyChangeRequest",
  "SignalPolicyConfigSnapshot",
  "TTradeAccountFacts",
  "T_TRADE_ACCOUNT_SNAPSHOT_STALE",
  "T_TRADE_ACCOUNT_SNAPSHOT_TOO_LARGE",
  "compute_t_trade_account_facts",
  "normalize_signal_policy",
]
