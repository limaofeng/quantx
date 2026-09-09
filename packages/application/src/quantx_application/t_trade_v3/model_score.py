"""Account-free, version-bound three-class T model score contract."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class TModelScore:
  score_id: str
  instrument_code: str
  score_cache_revision: int
  model_runtime_binding_hash: str
  model_as_of_ms: int
  source_feature_bar_id: str
  source_bar_end_ms: int
  feature_window_hash: str
  horizon_ms: int
  p_target_first: float
  p_stop_first: float
  p_no_touch: float
  score_status: str
  feature_coverage: float
  out_of_distribution_status: str
  feature_schema_version: int
  label_spec_version: str
  model_id: str
  model_version: str
  model_type: str
  artifact_manifest_sha256: str
  model_authorization_revision: int
  calibration_version: str

  def __post_init__(self):
    if any(
      type(x) not in (float, int) or not isfinite(x) or not 0 <= x <= 1
      for x in self.probabilities
    ):
      raise ValueError("T_MODEL_PROBABILITY_INVALID")
    if abs(sum(self.probabilities) - 1) > 1e-9:
      raise ValueError("T_MODEL_PROBABILITY_SUM_INVALID")
    if (
      self.model_as_of_ms < self.source_bar_end_ms
      or self.score_cache_revision < 1
      or self.model_authorization_revision < 1
      or self.horizon_ms <= 0
      or self.score_status != "VALID"
      or not 0 <= self.feature_coverage <= 1
      or self.out_of_distribution_status not in {"IN_DISTRIBUTION", "WARN", "BLOCK"}
    ):
      raise ValueError("T_MODEL_SCORE_SCOPE_INVALID")
    for digest in (
      self.score_id,
      self.model_runtime_binding_hash,
      self.feature_window_hash,
      self.artifact_manifest_sha256,
    ):
      if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
      ):
        raise ValueError("T_MODEL_SCORE_IDENTITY_INVALID")

  @property
  def probabilities(self):
    return self.p_target_first, self.p_stop_first, self.p_no_touch
