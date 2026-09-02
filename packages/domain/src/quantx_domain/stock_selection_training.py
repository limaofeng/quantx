"""Pure rules for auditable next-day stock-selection training runs.

The research application owns I/O and model implementations.  This module is
deliberately small and dependency free so that a caller can validate a
training request before it starts a worker process.  Values returned by the
helpers are ordinary dataclasses/enums and can therefore be projected into a
database model without importing the database layer here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence


class _StringEnum(str, Enum):
  """Enum whose JSON representation is its public protocol value."""

  def __str__(self) -> str:
    return self.value


class RunKind(_StringEnum):
  DEVELOPMENT = "DEVELOPMENT"
  FINAL_EVALUATION = "FINAL_EVALUATION"


class RequestedBackend(_StringEnum):
  AUTO = "AUTO"
  CPU = "CPU"
  GPU_REQUIRED = "GPU_REQUIRED"


class ResolvedBackend(_StringEnum):
  CPU = "CPU"
  LIGHTGBM_OPENCL_GPU = "LIGHTGBM_OPENCL_GPU"


class RunStatus(_StringEnum):
  QUEUED = "QUEUED"
  RUNNING = "RUNNING"
  SUCCEEDED = "SUCCEEDED"
  FAILED = "FAILED"
  CANCELLED = "CANCELLED"


class TrainingPhase(_StringEnum):
  PREFLIGHT = "PREFLIGHT"
  DATASET_BUILD = "DATASET_BUILD"
  WALK_FORWARD = "WALK_FORWARD"
  FINAL_FIT = "FINAL_FIT"
  CALIBRATION = "CALIBRATION"
  FROZEN_TEST = "FROZEN_TEST"
  ARTIFACT_PUBLISH = "ARTIFACT_PUBLISH"


class GpuQualificationStatus(_StringEnum):
  CPU_AVAILABLE = "CPU_AVAILABLE"
  GPU_UNAVAILABLE_BUILD = "GPU_UNAVAILABLE_BUILD"
  GPU_UNAVAILABLE_RUNTIME = "GPU_UNAVAILABLE_RUNTIME"
  GPU_INSUFFICIENT_MEMORY = "GPU_INSUFFICIENT_MEMORY"
  GPU_UNQUALIFIED = "GPU_UNQUALIFIED"
  GPU_AVAILABLE = "GPU_AVAILABLE"


class GateConclusion(_StringEnum):
  BLOCKED = "BLOCKED"
  SHADOW_ELIGIBLE = "SHADOW_ELIGIBLE"
  ACTIVE_ELIGIBLE = "ACTIVE_ELIGIBLE"


def _json_safe(value: Any) -> Any:
  """Return a finite, deterministic JSON value or raise a useful error."""

  if isinstance(value, Enum):
    return _json_safe(value.value)
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, Mapping):
    return {
      str(key): _json_safe(item)
      for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }
  if isinstance(value, (list, tuple)):
    return [_json_safe(item) for item in value]
  if isinstance(value, set):
    return [_json_safe(item) for item in sorted(value, key=str)]
  if isinstance(value, float):
    if not math.isfinite(value):
      raise ValueError("规范 JSON 不允许 NaN 或 Infinity")
    return value
  if isinstance(value, (str, int, bool)) or value is None:
    return value
  # numpy scalar support without making domain depend on numpy.
  item = getattr(value, "item", None)
  if callable(item):
    converted = item()
    if converted is not value:
      return _json_safe(converted)
  raise TypeError(f"值无法编码为规范 JSON: {type(value).__name__}")


def canonical_json(value: Any) -> str:
  """Serialize a value using the protocol's stable JSON rules."""

  return json.dumps(
    _json_safe(value),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  )


def stable_json_sha256(value: Any) -> str:
  """Hash a canonical JSON value using UTF-8 SHA-256."""

  return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _as_period(value: Any) -> str:
  """Normalize month-like values while retaining lexical chronological order."""

  if hasattr(value, "strftime"):
    # datetime/date and pandas Period-like values both support strftime in
    # supported versions.  Period's format is YYYY-MM.
    try:
      formatted = value.strftime("%Y-%m")
      if formatted:
        return str(formatted)
    except (AttributeError, TypeError, ValueError):
      pass
  text = str(value)
  match = re.match(r"^(\d{4})[-/]?(\d{1,2})", text)
  if not match:
    raise ValueError(f"无法解析训练月份: {value!r}")
  year = int(match.group(1))
  month = int(match.group(2))
  if not 1 <= month <= 12:
    raise ValueError(f"训练月份必须是 1..12: {value!r}")
  return f"{year:04d}-{month:02d}"


def _require_contiguous_months(months: Sequence[str]) -> None:
  for previous, current in zip(months, months[1:]):
    previous_year, previous_month = (int(item) for item in previous.split("-"))
    expected_year = previous_year + (1 if previous_month == 12 else 0)
    expected_month = 1 if previous_month == 12 else previous_month + 1
    if (int(current[:4]), int(current[5:])) != (expected_year, expected_month):
      raise ValueError("训练月份必须覆盖连续自然月，不能缺月")


def _ordered_months(months: Iterable[Any]) -> tuple[str, ...]:
  parsed = tuple(_as_period(month) for month in months)
  if len(set(parsed)) != len(parsed):
    raise ValueError("时间切分输入包含重复月份")
  normalized = tuple(sorted(parsed))
  if not normalized:
    raise ValueError("训练月份不能为空")
  _require_contiguous_months(normalized)
  return normalized


@dataclass(frozen=True)
class WalkForwardWindow:
  """One 30/6/1 month walk-forward window."""

  train_months: tuple[str, ...]
  calibration_months: tuple[str, ...]
  validation_month: str

  def __post_init__(self) -> None:
    train = tuple(_as_period(item) for item in self.train_months)
    calibration = tuple(_as_period(item) for item in self.calibration_months)
    validation = _as_period(self.validation_month)
    if not train or not calibration:
      raise ValueError("walk-forward 必须包含训练和校准月份")
    if tuple(sorted(train)) != train or tuple(sorted(calibration)) != calibration:
      raise ValueError("walk-forward 月份必须按时间排序")
    if set(train) & set(calibration) or validation in set(train) | set(calibration):
      raise ValueError("walk-forward 训练、校准和验证区间不得重叠")
    if train[-1] >= calibration[0] or calibration[-1] >= validation:
      raise ValueError("walk-forward 区间必须严格按时间先后")
    object.__setattr__(self, "train_months", train)
    object.__setattr__(self, "calibration_months", calibration)
    object.__setattr__(self, "validation_month", validation)


@dataclass(frozen=True)
class TrainingTimeSplit:
  """Immutable development/frozen split and its walk-forward folds."""

  development_months: tuple[str, ...]
  frozen_test_months: tuple[str, ...]
  folds: tuple[WalkForwardWindow, ...]
  minimum_training_months: int = 30
  calibration_months: int = 6
  validation_months: int = 1
  frozen_test_size: int = 12

  def __post_init__(self) -> None:
    if (
      self.minimum_training_months != 30
      or self.calibration_months != 6
      or self.validation_months != 1
      or self.frozen_test_size != 12
    ):
      raise ValueError("训练时间切分固定为 30/6/1/12 月")
    development = tuple(_as_period(item) for item in self.development_months)
    frozen = tuple(_as_period(item) for item in self.frozen_test_months)
    if not development or not frozen:
      raise ValueError("开发区间和冻结区间不能为空")
    if tuple(sorted(development)) != development or tuple(sorted(frozen)) != frozen:
      raise ValueError("时间切分必须按时间排序")
    _require_contiguous_months(development)
    _require_contiguous_months(frozen)
    _require_contiguous_months(development + frozen)
    if set(development) & set(frozen) or development[-1] >= frozen[0]:
      raise ValueError("开发区间和冻结区间不得重叠且必须严格先后")
    if len(frozen) != self.frozen_test_size:
      raise ValueError("冻结测试区间必须固定为 12 个月")
    if len(development) < self.minimum_training_months + self.calibration_months + 1:
      raise ValueError("开发区间不足以形成训练、校准和验证")
    previous: str | None = None
    for fold in self.folds:
      if previous is not None and fold.validation_month <= previous:
        raise ValueError("fold 验证月份必须严格递增")
      if not set(fold.train_months) | set(fold.calibration_months) | {
        fold.validation_month
      } <= set(development):
        raise ValueError("fold 不能访问开发区间之外的数据")
      previous = fold.validation_month
    if not self.folds:
      raise ValueError("walk-forward 至少需要一个验证 fold")
    object.__setattr__(self, "development_months", development)
    object.__setattr__(self, "frozen_test_months", frozen)
    object.__setattr__(self, "folds", tuple(self.folds))

  @property
  def all_months(self) -> tuple[str, ...]:
    return self.development_months + self.frozen_test_months

  def as_dict(self) -> dict[str, Any]:
    return {
      "development_months": list(self.development_months),
      "frozen_test_months": list(self.frozen_test_months),
      "folds": [
        {
          "train_months": list(fold.train_months),
          "calibration_months": list(fold.calibration_months),
          "validation_month": fold.validation_month,
        }
        for fold in self.folds
      ],
      "minimum_training_months": self.minimum_training_months,
      "calibration_months": self.calibration_months,
      "validation_months": self.validation_months,
      "frozen_test_size": self.frozen_test_size,
    }


def build_training_time_split(
  months: Sequence[Any],
  *,
  frozen_test_months: int = 12,
  minimum_training_months: int = 30,
  calibration_months: int = 6,
  validation_months: int = 1,
) -> TrainingTimeSplit:
  """Build the only supported 30/6/1/12 chronological split."""

  if frozen_test_months != 12:
    raise ValueError("冻结测试月份固定为 12")
  if minimum_training_months != 30:
    raise ValueError("最小训练月份固定为 30")
  if calibration_months != 6:
    raise ValueError("校准月份固定为 6")
  if validation_months != 1:
    raise ValueError("验证月份固定为 1")
  ordered = _ordered_months(months)
  minimum_development = minimum_training_months + calibration_months + validation_months
  if len(ordered) < minimum_development + frozen_test_months:
    raise ValueError(
      "有效样本不足固定切分：30 月训练 + 6 月校准 + 1 月验证 + 12 月冻结测试（至少 49 个月）"
    )
  frozen = ordered[-frozen_test_months:]
  development = ordered[:-frozen_test_months]
  folds: list[WalkForwardWindow] = []
  first_validation = minimum_training_months + calibration_months
  for validation_index in range(first_validation, len(development), validation_months):
    validation_slice = development[validation_index : validation_index + validation_months]
    if len(validation_slice) != validation_months:
      break
    calibration_start = validation_index - calibration_months
    folds.append(
      WalkForwardWindow(
        train_months=development[:calibration_start],
        calibration_months=development[calibration_start:validation_index],
        validation_month=validation_slice[0],
      )
    )
  return TrainingTimeSplit(
    development_months=development,
    frozen_test_months=frozen,
    folds=tuple(folds),
    minimum_training_months=minimum_training_months,
    calibration_months=calibration_months,
    validation_months=validation_months,
    frozen_test_size=frozen_test_months,
  )


def validate_time_split(split: TrainingTimeSplit | Mapping[str, Any]) -> TrainingTimeSplit:
  """Reconstruct and validate a serialized split."""

  if isinstance(split, TrainingTimeSplit):
    return split
  folds = tuple(
    WalkForwardWindow(
      tuple(item["train_months"]),
      tuple(item["calibration_months"]),
      item["validation_month"],
    )
    for item in split.get("folds", ())
  )
  return TrainingTimeSplit(
    tuple(split["development_months"]),
    tuple(split["frozen_test_months"]),
    folds,
    int(split.get("minimum_training_months", 30)),
    int(split.get("calibration_months", 6)),
    int(split.get("validation_months", 1)),
    int(split.get("frozen_test_size", 12)),
  )


_STATUS_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
  RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
  RunStatus.RUNNING: frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
  ),
  RunStatus.SUCCEEDED: frozenset(),
  RunStatus.FAILED: frozenset(),
  RunStatus.CANCELLED: frozenset(),
}


def can_transition_status(current: RunStatus | str, target: RunStatus | str) -> bool:
  try:
    current_value = RunStatus(current)
    target_value = RunStatus(target)
  except (TypeError, ValueError):
    return False
  return target_value in _STATUS_TRANSITIONS[current_value]


def transition_run_status(
  current: RunStatus | str, target: RunStatus | str
) -> RunStatus:
  target_value = RunStatus(target)
  if not can_transition_status(current, target_value):
    raise ValueError(f"非法运行状态转换: {current} -> {target_value}")
  return target_value


_PHASE_ORDER = tuple(TrainingPhase)


def phase_index(phase: TrainingPhase | str) -> int:
  return _PHASE_ORDER.index(TrainingPhase(phase))


def advance_phase(
  current: TrainingPhase | str, target: TrainingPhase | str
) -> TrainingPhase:
  current_value = TrainingPhase(current)
  target_value = TrainingPhase(target)
  if phase_index(target_value) < phase_index(current_value):
    raise ValueError(f"训练阶段不得回退: {current_value} -> {target_value}")
  return target_value


@dataclass(frozen=True)
class Progress:
  phase: TrainingPhase
  completed_units: int
  total_units: int
  message: str = ""

  def __post_init__(self) -> None:
    if self.completed_units < 0 or self.total_units < 0:
      raise ValueError("进度单位不能为负数")
    if self.completed_units > self.total_units:
      raise ValueError("completed_units 不能超过 total_units")


def validate_progress(previous: Progress | None, current: Progress) -> Progress:
  if previous is not None:
    if phase_index(current.phase) < phase_index(previous.phase):
      raise ValueError("训练阶段不得回退")
    if current.completed_units < previous.completed_units:
      raise ValueError("训练进度不得回退")
    if current.total_units != previous.total_units:
      raise ValueError("同一运行的 total_units 不得改变")
  return current


@dataclass(frozen=True)
class BackendDecision:
  requested_backend: RequestedBackend
  resolved_backend: ResolvedBackend
  reason: str
  qualification_status: GpuQualificationStatus | None = None

  def as_dict(self) -> dict[str, str | None]:
    return {
      "requested_backend": self.requested_backend.value,
      "resolved_backend": self.resolved_backend.value,
      "reason": self.reason,
      "qualification_status": (
        self.qualification_status.value if self.qualification_status else None
      ),
    }


class BackendResolutionError(ValueError):
  """Raised when GPU_REQUIRED cannot be honored without a CPU fallback."""


DEFAULT_GPU_MAX_MEMORY_FRACTION = 0.80
DEFAULT_GPU_MIN_ACCELERATION = 0.20


def _qualification_value(qualification: Any, name: str, default: Any = None) -> Any:
  if isinstance(qualification, Mapping):
    return qualification.get(name, default)
  return getattr(qualification, name, default)


def resolve_backend(
  requested_backend: RequestedBackend | str,
  qualification: Mapping[str, Any] | Any | None = None,
  *,
  sample_count: int = 0,
  estimated_memory_fraction: float | None = None,
  minimum_sample_count: int | None = None,
  minimum_acceleration: float = DEFAULT_GPU_MIN_ACCELERATION,
  memory_budget_fraction: float = DEFAULT_GPU_MAX_MEMORY_FRACTION,
) -> BackendDecision:
  """Resolve a requested backend using a previously verified GPU certificate.

  CPU is unconditional and intentionally never inspects or initializes GPU
  state.  AUTO and GPU_REQUIRED use exactly the same qualification gates;
  their only difference is whether a failed gate becomes a CPU decision or an
  exception.
  """

  requested = RequestedBackend(requested_backend)
  if requested is RequestedBackend.CPU:
    return BackendDecision(requested, ResolvedBackend.CPU, "显式请求 CPU")

  # The capability certificate has one canonical field.  In particular, do
  # not silently consume historical aliases here: a queued run must carry the
  # exact certificate shape that was reviewed by the preflight gate.
  status_raw = _qualification_value(qualification, "status")
  try:
    status = GpuQualificationStatus(status_raw)
  except (TypeError, ValueError):
    status = GpuQualificationStatus.GPU_UNQUALIFIED

  reason: str | None = None
  if status is not GpuQualificationStatus.GPU_AVAILABLE:
    reason = f"GPU 资格状态为 {status.value}"
  elif estimated_memory_fraction is not None:
    try:
      memory = float(estimated_memory_fraction)
    except (TypeError, ValueError):
      memory = math.inf
    if not math.isfinite(memory) or memory > memory_budget_fraction:
      reason = "预估显存超过 80% 预算"
  if reason is None:
    threshold = minimum_sample_count
    if threshold is None:
      threshold = _qualification_value(qualification, "minimum_sample_count")
    try:
      enough_samples = (
        not isinstance(threshold, bool)
        and isinstance(threshold, int)
        and threshold >= 1
        and not isinstance(sample_count, bool)
        and int(sample_count) >= threshold
      )
    except (TypeError, ValueError):
      enough_samples = False
    if not enough_samples:
      reason = "样本数未达到 GPU 资格证据阈值"
  if reason is None:
    measured = _qualification_value(qualification, "acceleration", None)
    try:
      enough_acceleration = (
        not isinstance(measured, bool)
        and math.isfinite(float(measured))
        and float(measured) >= float(minimum_acceleration)
      )
    except (TypeError, ValueError):
      enough_acceleration = False
    if not enough_acceleration:
      reason = "GPU 端到端加速不足 20%"

  if reason is not None:
    if requested is RequestedBackend.GPU_REQUIRED:
      raise BackendResolutionError(reason)
    return BackendDecision(requested, ResolvedBackend.CPU, reason, status)
  return BackendDecision(
    requested,
    ResolvedBackend.LIGHTGBM_OPENCL_GPU,
    "GPU 资格、显存、样本规模和加速门禁均通过",
    status,
  )


@dataclass(frozen=True)
class GateEvidence:
  """Evidence projected into the three-valued publication conclusion."""

  brier_skill_positive: bool
  ece_within_limit: bool
  top20_lift_ci_lower_positive: bool
  historical_universe_complete: bool
  # These are deliberately explicit.  A caller that omits artifact/data
  # validation must fail closed instead of inheriting a compatibility default.
  artifact_valid: bool = False
  data_quality_valid: bool = False
  unbiased_frozen_evidence: bool = False


def gate_conclusion(
  evidence: GateEvidence | Mapping[str, Any],
  *,
  frozen_test_access_count: int = 1,
) -> GateConclusion:
  """Return BLOCKED, SHADOW_ELIGIBLE or ACTIVE_ELIGIBLE.

  Effect/data/artifact failures are BLOCKED.  Missing historical-universe
  evidence is still a valid research result, but can only be shadow eligible.
  Any repeated frozen-test access invalidates unbiased evidence and therefore
  can never be ACTIVE_ELIGIBLE.
  """

  def value(name: str, default: bool = False) -> bool:
    if isinstance(evidence, Mapping):
      return bool(evidence.get(name, default))
    return bool(getattr(evidence, name, default))

  if frozen_test_access_count < 1:
    unbiased = False
  else:
    unbiased = value("unbiased_frozen_evidence") and frozen_test_access_count == 1
  basic = all(
    value(name)
    for name in (
      "brier_skill_positive",
      "ece_within_limit",
      "top20_lift_ci_lower_positive",
      "artifact_valid",
      "data_quality_valid",
    )
  )
  if not basic:
    return GateConclusion.BLOCKED
  if not unbiased or not value("historical_universe_complete"):
    return GateConclusion.SHADOW_ELIGIBLE
  return GateConclusion.ACTIVE_ELIGIBLE


__all__ = [
  "BackendDecision",
  "BackendResolutionError",
  "DEFAULT_GPU_MAX_MEMORY_FRACTION",
  "DEFAULT_GPU_MIN_ACCELERATION",
  "GateConclusion",
  "GateEvidence",
  "GpuQualificationStatus",
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
