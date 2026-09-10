"""Atomic minute score cache with isolated RULE_ONLY / SHADOW / ACTIVE behavior.

The supervisor supplies a frozen, registry-authorized binding. This component
cannot register a model, change execution mode, or touch orders and ExitPlans.
"""

from dataclasses import asdict, dataclass, replace
from time import perf_counter_ns

from quantx_application.t_trade_v3.model_features import (
  FEATURE_SCHEMA_VERSION,
  TModelFeatureBar,
)
from quantx_application.t_trade_v3.model_minute_window import TModelMinuteOutcome
from quantx_application.t_trade_v3.model_score import TModelScore
from quantx_domain.trading.t_assistant_execution import (
  TModelRuntimeBinding,
  stable_manifest_hash,
)
from quantx_infrastructure.services.t_model_cpu_artifact import TCpuArtifact


@dataclass(frozen=True)
class TModelAuthorization:
  artifact_sha256: str
  policy_compatibility_hash: str
  registry_stage: str
  registry_authorization_revision: int
  gate_conclusion: str
  runtime_binding: TModelRuntimeBinding


@dataclass(frozen=True)
class TModelBatchResult:
  revision: int
  manifest_hash: str
  entry_blocked: bool
  reason: str
  execution_rule_order: tuple[str, ...]
  active_scores: tuple[TModelScore, ...]
  shadow_scores: tuple[TModelScore, ...]
  unavailable: tuple[TModelMinuteOutcome, ...] = ()
  model_as_of_ms: int | None = None


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
    self._bound_config = (mode, policy_hash, max_age_ms, inference_budget_ms)
    self._bound_artifact, self._bound_authorization = artifact, authorization
    self.revision = 0
    self.latest = TModelBatchResult(0, "", mode == "ACTIVE", "COLD", (), (), ())
    self._cache_key = None
    self._cached_artifact = None

  def evaluate(
    self,
    bars: tuple[TModelFeatureBar, ...],
    *,
    model_as_of_ms: int,
    rule_order: tuple[str, ...],
    unavailable: tuple[TModelMinuteOutcome, ...] = (),
    input_manifest_hash: str | None = None,
  ) -> TModelBatchResult:
    if self._bound_config[0] == "RULE_ONLY":
      self._cache_key = None
      self._cached_artifact = None
      self.latest = TModelBatchResult(
        self.revision, "", False, "MODEL_OFF", rule_order, (), ()
      )
      return self.latest
    started = perf_counter_ns()
    frozen_config = self._bound_config
    try:
      artifact, auth = self.artifact, self.authorization
      if artifact is None or auth is None:
        raise ValueError("T_MODEL_BINDING_MISSING")
      def validate_before_publish():
        # Inference hooks must never publish under a different execution binding.
        # Registry authority still needs an authoritative external reader.
        if (
          self.artifact is not artifact or self.authorization != auth
          or frozen_config != (self.mode, self.policy_hash, self.max_age_ms, self.budget_ms)
        ):
          raise ValueError("T_MODEL_BINDING_CHANGED_DURING_INFERENCE")

      if artifact is not self._bound_artifact or auth != self._bound_authorization:
        raise ValueError("T_MODEL_FROZEN_BINDING_CHANGED")
      validate_before_publish()
      binding = TModelRuntimeBinding.from_mapping(auth.runtime_binding.to_dict())
      if (
        binding.model_id != artifact.model_id or binding.model_version != artifact.model_version
        or binding.artifact_manifest_sha256 != auth.artifact_sha256
        or binding.portfolio_policy_compatibility_hash != auth.policy_compatibility_hash
        or binding.registry_stage != auth.registry_stage
        or binding.registry_authorization_revision != auth.registry_authorization_revision
        or binding.feature_schema_version != FEATURE_SCHEMA_VERSION
        or binding.label_spec_version != artifact.label_spec_version
        or binding.calibration_version != artifact.calibration_version
      ):
        raise ValueError("T_MODEL_FULL_BINDING_MISMATCH")
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
      planned = tuple(bars) + tuple(unavailable)
      if (not planned or len({bar.instrument_code for bar in planned}) != len(planned)
        or len({bar.feature_bar_id for bar in bars}) != len(bars)):
        raise ValueError("T_MODEL_BATCH_SYMBOL_SET_INVALID")
      if any(item.status != "UNAVAILABLE" or item.feature_bar is not None or not item.reason for item in unavailable):
        raise ValueError("T_MODEL_UNAVAILABLE_OUTCOME_INVALID")
      if type(model_as_of_ms) is not int or any(model_as_of_ms < item.interval_end_ms or model_as_of_ms - item.interval_end_ms > self.max_age_ms for item in planned):
        raise ValueError("T_MODEL_SCORE_STALE")
      if len({(item.interval_start_ms, item.interval_end_ms) for item in planned}) != 1:
        raise ValueError("T_MODEL_BATCH_COORDINATE_INVALID")
      if len({(
        bar.interval_start_ms, bar.interval_end_ms, bar.market_session,
        bar.stream_id, bar.continuity_generation,
      ) for bar in bars}) > 1 or any(
        type(bar.interval_start_ms) is not int
        or bar.interval_start_ms % 60000
        or bar.interval_end_ms != bar.interval_start_ms + 60000
        for bar in planned
      ):
        raise ValueError("T_MODEL_BATCH_COORDINATE_INVALID")
      ordered_bars = sorted(bars, key=lambda bar: bar.instrument_code)
      cache_key = stable_manifest_hash({
        "authorization": asdict(auth), "policy_hash": self.policy_hash,
        "mode": self.mode, "max_age_ms": self.max_age_ms, "budget_ms": self.budget_ms,
        "bars": [asdict(bar) for bar in ordered_bars], "model_as_of_ms": model_as_of_ms,
        "unavailable": [asdict(item) for item in sorted(unavailable, key=lambda item: item.instrument_code)],
        "input_manifest_hash": input_manifest_hash,
      })
      if cache_key == self._cache_key and artifact is self._cached_artifact:
        if (perf_counter_ns() - started) / 1_000_000 > self.budget_ms:
          raise ValueError("T_MODEL_INFERENCE_BUDGET_EXCEEDED")
        validate_before_publish()
        self.latest = replace(self.latest, execution_rule_order=rule_order)
        return self.latest
      scores = []
      revision = self.revision + 1
      binding_hash = binding.binding_hash
      for bar in ordered_bars:
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
          "unavailable": [asdict(item) for item in sorted(unavailable, key=lambda item: item.instrument_code)],
          "input_manifest_hash": input_manifest_hash,
        }
      )
      result = TModelBatchResult(
        revision,
        digest,
        self.mode == "ACTIVE" and not scores,
        "VALID",
        rule_order,
        tuple(scores) if self.mode == "ACTIVE" else (),
        tuple(scores) if self.mode == "SHADOW" else (),
        tuple(sorted(unavailable, key=lambda item: item.instrument_code)),
        model_as_of_ms,
      )
      validate_before_publish()
      self.revision, self.latest = revision, result
      self._cache_key, self._cached_artifact = cache_key, artifact
    except Exception:
      # No partial publish, stale-cache reuse or implicit ACTIVE -> RULE_ONLY.
      self._cache_key, self._cached_artifact = None, None
      self.latest = TModelBatchResult(
        self.revision,
        "",
        frozen_config[0] == "ACTIVE",
        "MODEL_BATCH_UNAVAILABLE",
        rule_order,
        (),
        (),
      )
    return self.latest
