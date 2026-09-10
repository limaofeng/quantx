"""Account-free, version-bound three-class T model score contract."""

from dataclasses import asdict, dataclass
from math import isfinite

from .t_assistant_execution import TModelRuntimeBinding


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


@dataclass(frozen=True)
class TModelSnapshotView:
  binding: TModelRuntimeBinding
  revision: int
  status: str
  reason: str
  model_as_of_ms: int | None
  manifest_hash: str
  max_age_ms: int | None
  scores: tuple[TModelScore, ...]
  unavailable: tuple[tuple[str, str], ...]

  def validate_for_snapshot(self, *, mode, binding_hash, feature_schema_version, as_of_ms, instrument_codes):
    binding = self.binding
    codes = tuple(score.instrument_code for score in self.scores) + tuple(code for code, _ in self.unavailable)
    if (
      mode != binding.registry_stage or binding_hash != binding.binding_hash
      or feature_schema_version != binding.feature_schema_version
      or type(self.revision) is not int or self.revision < 0
      or (self.max_age_ms is not None and (type(self.max_age_ms) is not int or self.max_age_ms < 1))
      or not isinstance(self.reason, str) or not self.reason
      or len(set(codes)) != len(codes) or set(codes) != set(instrument_codes)
      or any(not reason for _, reason in self.unavailable)
      or self.status not in {"VALID", "UNAVAILABLE"}
    ):
      raise ValueError("T_MODEL_SNAPSHOT_IDENTITY_INVALID")
    if self.status == "UNAVAILABLE":
      if self.scores or self.manifest_hash or self.model_as_of_ms is not None:
        raise ValueError("T_MODEL_SNAPSHOT_UNAVAILABLE_HAS_SCORES")
      return
    if (
      self.revision < 1 or type(self.model_as_of_ms) is not int or self.max_age_ms is None
      or not 0 <= as_of_ms - self.model_as_of_ms <= self.max_age_ms
      or not isinstance(self.manifest_hash, str) or len(self.manifest_hash) != 64
      or any(c not in "0123456789abcdef" for c in self.manifest_hash)
    ):
      raise ValueError("T_MODEL_SNAPSHOT_VISIBILITY_INVALID")
    for score in self.scores:
      if (
        score.model_runtime_binding_hash != binding.binding_hash
        or score.score_cache_revision != self.revision
        or score.model_as_of_ms != self.model_as_of_ms or score.source_bar_end_ms > as_of_ms
        or score.model_authorization_revision != binding.registry_authorization_revision
        or score.artifact_manifest_sha256 != binding.artifact_manifest_sha256
        or score.model_id != binding.model_id or score.model_version != binding.model_version
        or score.feature_schema_version != binding.feature_schema_version
        or score.label_spec_version != binding.label_spec_version
        or score.calibration_version != binding.calibration_version
      ):
        raise ValueError("T_MODEL_SNAPSHOT_SCORE_MISMATCH")

  def to_dict(self):
    return asdict(self)
