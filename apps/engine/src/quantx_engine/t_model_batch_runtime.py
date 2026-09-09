"""Atomic minute score cache with isolated RULE_ONLY / SHADOW / ACTIVE behavior.

The supervisor supplies a frozen, registry-authorized binding. This component
cannot register a model, change execution mode, or touch orders and ExitPlans.
"""

from dataclasses import asdict, dataclass
from time import perf_counter_ns

from quantx_application.t_trade_v3.model_features import TModelFeatureBar
from quantx_application.t_trade_v3.model_score import TModelScore
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.services.t_model_cpu_artifact import TCpuArtifact


@dataclass(frozen=True)
class TModelAuthorization:
  artifact_sha256: str
  policy_compatibility_hash: str
  registry_stage: str
  registry_authorization_revision: int
  gate_conclusion: str


@dataclass(frozen=True)
class TModelBatchResult:
  revision: int
  manifest_hash: str
  entry_blocked: bool
  reason: str
  execution_rule_order: tuple[str, ...]
  active_scores: tuple[TModelScore, ...]
  shadow_scores: tuple[TModelScore, ...]


class TModelBatchRuntime:
  def __init__(
    self,
    *,
    mode: str,
    artifact: TCpuArtifact | None,
    authorization: TModelAuthorization | None,
    policy_hash: str,
    max_age_ms: int,
    inference_budget_ms: int,
  ):
    if mode not in {"RULE_ONLY", "SHADOW", "ACTIVE"} or any(
      type(v) is not int or v <= 0 for v in (max_age_ms, inference_budget_ms)
    ):
      raise ValueError("T_MODEL_RUNTIME_CONFIG_INVALID")
    if mode == "RULE_ONLY" and (artifact is not None or authorization is not None):
      raise ValueError("T_MODEL_RULE_ONLY_BINDING_FORBIDDEN")
    self.mode, self.artifact, self.authorization = mode, artifact, authorization
    self.policy_hash, self.max_age_ms, self.budget_ms = (
      policy_hash,
      max_age_ms,
      inference_budget_ms,
    )
    self.revision = 0
    self.latest = TModelBatchResult(0, "", mode == "ACTIVE", "COLD", (), (), ())

  def evaluate(
    self,
    bars: tuple[TModelFeatureBar, ...],
    *,
    model_as_of_ms: int,
    rule_order: tuple[str, ...],
  ) -> TModelBatchResult:
    if self.mode == "RULE_ONLY":
      self.latest = TModelBatchResult(
        self.revision, "", False, "MODEL_OFF", rule_order, (), ()
      )
      return self.latest
    started = perf_counter_ns()
    try:
      artifact, auth = self.artifact, self.authorization
      if artifact is None or auth is None:
        raise ValueError("T_MODEL_BINDING_MISSING")
      allowed_gates = (
        {"SHADOW_ELIGIBLE", "ACTIVE_ELIGIBLE"}
        if self.mode == "SHADOW"
        else {"ACTIVE_ELIGIBLE"}
      )
      if (
        auth.artifact_sha256 != artifact.sha256
        or auth.policy_compatibility_hash != self.policy_hash
        or artifact.policy_compatibility_hash != self.policy_hash
        or type(auth.registry_authorization_revision) is not int
        or auth.registry_authorization_revision < 1
        or auth.registry_stage != self.mode
        or auth.gate_conclusion not in allowed_gates
      ):
        raise ValueError("T_MODEL_AUTHORIZATION_INVALID")
      if not bars or len({bar.instrument_code for bar in bars}) != len(bars):
        raise ValueError("T_MODEL_BATCH_SYMBOL_SET_INVALID")
      scores = []
      revision = self.revision + 1
      binding_hash = stable_manifest_hash(asdict(auth))
      for bar in sorted(bars, key=lambda b: b.instrument_code):
        if model_as_of_ms - bar.interval_end_ms > self.max_age_ms:
          raise ValueError("T_MODEL_SCORE_STALE")
        score = artifact.score(bar, model_as_of_ms=model_as_of_ms)
        if (
          self.mode == "ACTIVE"
          and score.out_of_distribution_status != "IN_DISTRIBUTION"
        ):
          raise ValueError("T_MODEL_OOD_BLOCK")
        identity = {
          "binding": binding_hash,
          "bar": bar.feature_bar_id,
          "revision": revision,
          "as_of_ms": model_as_of_ms,
        }
        scores.append(
          TModelScore(
            stable_manifest_hash(identity),
            bar.instrument_code,
            revision,
            binding_hash,
            model_as_of_ms,
            bar.feature_bar_id,
            bar.interval_end_ms,
            stable_manifest_hash({"feature_bar_ids": [bar.feature_bar_id]}),
            artifact.horizon_ms,
            *score.probabilities,
            "VALID",
            bar.feature_coverage,
            score.out_of_distribution_status,
            bar.feature_schema_version,
            artifact.label_spec_version,
            artifact.model_id,
            artifact.model_version,
            artifact.model_type,
            artifact.sha256,
            auth.registry_authorization_revision,
            artifact.calibration_version,
          )
        )
      if (perf_counter_ns() - started) / 1_000_000 > self.budget_ms:
        raise ValueError("T_MODEL_INFERENCE_BUDGET_EXCEEDED")
      digest = stable_manifest_hash(
        {
          "revision": revision,
          "artifact": artifact.sha256,
          "authorization_revision": auth.registry_authorization_revision,
          "features": [score.source_feature_bar_id for score in scores],
          "model_as_of_ms": model_as_of_ms,
          "probabilities": [score.probabilities for score in scores],
        }
      )
      result = TModelBatchResult(
        revision,
        digest,
        False,
        "VALID",
        rule_order,
        tuple(scores) if self.mode == "ACTIVE" else (),
        tuple(scores) if self.mode == "SHADOW" else (),
      )
      self.revision, self.latest = revision, result
    except Exception:
      # No partial publish, stale-cache reuse or implicit ACTIVE -> RULE_ONLY.
      self.latest = TModelBatchResult(
        self.revision,
        "",
        self.mode == "ACTIVE",
        "MODEL_BATCH_UNAVAILABLE",
        rule_order,
        (),
        (),
      )
    return self.latest
