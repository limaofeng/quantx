"""GraphQL projections for read-only probability selection evidence."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

import strawberry
from strawberry.scalars import JSON


@strawberry.enum(description="人工控制的概率模型发布阶段")
class StockSelectionModelStage(Enum):
  CANDIDATE = "CANDIDATE"
  SHADOW = "SHADOW"
  ACTIVE = "ACTIVE"
  SUSPENDED = "SUSPENDED"
  RETIRED = "RETIRED"


@strawberry.enum(description="只读候选层级")
class StockCandidateLevel(Enum):
  A = "A"
  B = "B"


@strawberry.type(description="次日上涨概率模型版本及发布证据")
class StockSelectionModel:
  model_version: str
  run_key: str
  selected_family: str
  artifact_manifest_sha256: str
  stage: StockSelectionModelStage
  indicator_version: str
  factor_set_version: str
  factor_set_hash: str
  label_version: str
  calibrator_version: str
  training_start: date
  training_end: date
  calibration_start: date
  calibration_end: date
  test_start: date
  test_end: date
  historical_universe_complete: bool
  effect_gate_passed: bool
  metrics: JSON
  gates: JSON
  evidence: JSON
  approved_by: str
  approved_at: Optional[datetime]
  state_version: int
  created_at: datetime
  updated_at: datetime

  @staticmethod
  def from_record(record) -> "StockSelectionModel":
    return StockSelectionModel(
      model_version=record.model_version,
      run_key=record.run_key,
      selected_family=record.selected_family,
      artifact_manifest_sha256=record.artifact_manifest_sha256,
      stage=StockSelectionModelStage(record.stage),
      indicator_version=record.indicator_version,
      factor_set_version=record.factor_set_version,
      factor_set_hash=record.factor_set_hash,
      label_version=record.label_version,
      calibrator_version=record.calibrator_version,
      training_start=record.training_start,
      training_end=record.training_end,
      calibration_start=record.calibration_start,
      calibration_end=record.calibration_end,
      test_start=record.test_start,
      test_end=record.test_end,
      historical_universe_complete=record.historical_universe_complete,
      effect_gate_passed=record.effect_gate_passed,
      metrics=record.metrics,
      gates=record.gates,
      evidence=record.evidence,
      approved_by=record.approved_by,
      approved_at=record.approved_at,
      state_version=record.state_version,
      created_at=record.created_at,
      updated_at=record.updated_at,
    )


@strawberry.input(description="概率候选只读查询")
class StockProbabilityCandidateInput:
  as_of: Optional[date] = None
  model_version: Optional[str] = None
  levels: Optional[list[StockCandidateLevel]] = None
  minimum_probability: Optional[float] = None
  search: Optional[str] = None
  limit: int = 50
  offset: int = 0


@strawberry.type(description="概率校准桶的历史支持")
class StockProbabilityCalibrationBucket:
  index: int
  sample_count: int
  realized_rate: Optional[float]


@strawberry.type(description="只读次日上涨概率候选")
class StockProbabilityCandidate:
  code: str
  name: str
  as_of: date
  target_date: date
  cutoff_at: datetime
  model_version: str
  label_version: str
  indicator_version: str
  factor_set_version: str
  factor_set_hash: str
  calibrator_version: str
  candidate_rule_version: str
  prediction_run_key: str
  factor_snapshot_sha256: Optional[str]
  stage: StockSelectionModelStage
  is_shadow: bool
  calibrated_probability: float
  raw_score: float
  logistic_probability: float
  lightgbm_probability: float
  rank: int
  confidence: float
  candidate_level: StockCandidateLevel
  factor_completeness: float
  ood_fit: float
  reasons: list[str]
  risks: list[str]
  calibration_bucket: StockProbabilityCalibrationBucket


@strawberry.type(description="概率候选分页结果")
class StockProbabilityCandidatePage:
  items: list[StockProbabilityCandidate]
  total: int
  limit: int
  offset: int
  as_of: Optional[date]
  target_date: Optional[date]
  active_model_version: Optional[str]
  showing_shadow: bool
  warnings: list[str]


@strawberry.type(description="一次模型日推理运行")
class StockPredictionRunStatus:
  run_key: str
  model_version: str
  stage: StockSelectionModelStage
  as_of: date
  target_date: date
  cutoff_at: datetime
  status: str
  started_at: datetime
  completed_at: Optional[datetime]
  prediction_count: int
  candidate_count: int
  factor_snapshot_sha256: Optional[str]
  candidate_rule_version: str
  error_code: Optional[str]
  error_message: Optional[str]
  warnings: list[str]

  @staticmethod
  def from_record(record) -> "StockPredictionRunStatus":
    return StockPredictionRunStatus(
      run_key=record.run_key,
      model_version=record.model_version,
      stage=StockSelectionModelStage(record.model_stage),
      as_of=record.as_of_date,
      target_date=record.target_date,
      cutoff_at=record.cutoff_at,
      status=record.status,
      started_at=record.started_at,
      completed_at=record.completed_at,
      prediction_count=record.prediction_count,
      candidate_count=record.candidate_count,
      factor_snapshot_sha256=record.factor_snapshot_sha256,
      candidate_rule_version=record.candidate_rule_version,
      error_code=record.error_code,
      error_message=record.error_message,
      warnings=list(record.warnings or []),
    )


@strawberry.type(description="指定日期的模型日推理状态")
class StockPredictionRunStatusPage:
  as_of: Optional[date]
  runs: list[StockPredictionRunStatus]
  has_active_model: bool
  warnings: list[str]


# ---------------------------------------------------------------------------
# Web initiated training projections
# ---------------------------------------------------------------------------
#
# Training is a research-only workflow.  These types deliberately keep the
# user-editable request small and typed while allowing the read-only evidence
# sections to evolve behind a bounded JSON projection.  In particular, no
# filesystem source reference or raw worker exception is part of this public
# contract.


@strawberry.enum(description="次日概率训练运行类型")
class StockSelectionTrainingRunKind(Enum):
  DEVELOPMENT = "DEVELOPMENT"
  FINAL_EVALUATION = "FINAL_EVALUATION"


@strawberry.enum(description="次日概率训练运行状态")
class StockSelectionTrainingRunStatus(Enum):
  QUEUED = "QUEUED"
  RUNNING = "RUNNING"
  SUCCEEDED = "SUCCEEDED"
  FAILED = "FAILED"
  CANCELLED = "CANCELLED"


@strawberry.enum(description="次日概率训练阶段")
class StockSelectionTrainingPhase(Enum):
  PREFLIGHT = "PREFLIGHT"
  DATASET_BUILD = "DATASET_BUILD"
  WALK_FORWARD = "WALK_FORWARD"
  FINAL_FIT = "FINAL_FIT"
  CALIBRATION = "CALIBRATION"
  FROZEN_TEST = "FROZEN_TEST"
  ARTIFACT_PUBLISH = "ARTIFACT_PUBLISH"


@strawberry.enum(description="次日概率训练请求后端")
class StockSelectionTrainingBackend(Enum):
  AUTO = "AUTO"
  CPU = "CPU"
  GPU_REQUIRED = "GPU_REQUIRED"


@strawberry.enum(description="次日概率训练解析后的后端")
class StockSelectionResolvedBackend(Enum):
  CPU = "CPU"
  LIGHTGBM_OPENCL_GPU = "LIGHTGBM_OPENCL_GPU"


@strawberry.enum(description="次日概率训练 GPU 资格状态")
class StockSelectionTrainingGpuStatus(Enum):
  CPU_AVAILABLE = "CPU_AVAILABLE"
  GPU_UNAVAILABLE_BUILD = "GPU_UNAVAILABLE_BUILD"
  GPU_UNAVAILABLE_RUNTIME = "GPU_UNAVAILABLE_RUNTIME"
  GPU_INSUFFICIENT_MEMORY = "GPU_INSUFFICIENT_MEMORY"
  GPU_UNQUALIFIED = "GPU_UNQUALIFIED"
  GPU_AVAILABLE = "GPU_AVAILABLE"


@strawberry.enum(description="次日概率训练发布结论")
class StockSelectionTrainingConclusion(Enum):
  BLOCKED = "BLOCKED"
  SHADOW_ELIGIBLE = "SHADOW_ELIGIBLE"
  ACTIVE_ELIGIBLE = "ACTIVE_ELIGIBLE"


@strawberry.enum(description="次日概率训练股票范围")
class StockSelectionTrainingUniverseKind(Enum):
  ORDINARY_A_SHARE = "ORDINARY_A_SHARE"
  CERTIFIED_INDEX = "CERTIFIED_INDEX"
  EXPLICIT = "EXPLICIT"


@strawberry.input(description="经过认证的次日概率训练股票范围")
class StockSelectionTrainingUniverseInput:
  kind: StockSelectionTrainingUniverseKind = StockSelectionTrainingUniverseKind.ORDINARY_A_SHARE
  stock_codes: Optional[list[str]] = None
  index_code: Optional[str] = None
  benchmark_code: str = "000300.SH"
  minimum_listing_days: int = 252


@strawberry.input(description="次日概率训练请求；模型参数使用系统冻结预设")
class StockSelectionTrainingInput:
  dataset_version: str
  date_start: Optional[date] = None
  date_end: Optional[date] = None
  universe: Optional[StockSelectionTrainingUniverseInput] = None
  requested_backend: StockSelectionTrainingBackend = StockSelectionTrainingBackend.CPU
  bootstrap_samples: int = 2000
  worker_batch_size: int = 100
  random_seed: int = 20260901
  note: str = ""


@strawberry.type(description="次日概率训练资源估算")
class StockSelectionTrainingResourceEstimate:
  memory_mib: int
  disk_mib: int
  gpu_memory_mib: int
  estimated_minutes: int
  duration_level: str
  sample_count: int
  stock_count: int
  trading_day_count: int
  fold_count: int


@strawberry.type(description="次日概率训练逐月 walk-forward fold")
class StockSelectionTrainingFold:
  train_start: date
  train_end: date
  calibration_start: date
  calibration_end: date
  validation_start: date
  validation_end: date
  validation_month: date


@strawberry.type(description="次日概率训练能力与脱敏环境摘要")
class StockSelectionTrainingCapabilities:
  cpu_available: bool
  gpu_status: StockSelectionTrainingGpuStatus
  fresh: bool
  updated_at: Optional[datetime]
  available_memory_mib: Optional[int]
  environment_requirement_hash: Optional[str]
  qualification: JSON
  environment_summary: JSON


@strawberry.type(description="独立 Trainer 最近一次调度决定，不作为执行授权")
class StockSelectionTrainerDispatch:
  state: str
  status: Optional[str]
  reason: Optional[str]


@strawberry.type(description="独立 Trainer 服务心跳、准入及调度状态")
class StockSelectionTrainerStatus:
  service: str
  phase: Optional[str]
  admission: str
  resource_reason: Optional[str]
  fresh: bool
  updated_at: Optional[datetime]
  training: StockSelectionTrainerDispatch
  preparation: StockSelectionTrainerDispatch


@strawberry.type(description="已认证的次日概率训练数据集版本")
class StockSelectionDatasetVersion:
  dataset_version: str
  status: str
  source_kind: str
  date_start: date
  date_end: date
  universe_spec: JSON
  indicator_version: str
  factor_set_version: str
  factor_set_hash: str
  label_version: str
  manifest_sha256: str
  sample_count: int
  stock_count: int
  trading_day_count: int
  quality_summary: JSON
  created_at: datetime


@strawberry.type(description="次日概率训练预检证据")
class StockSelectionTrainingPreview:
  preview_fingerprint: str
  dataset_version: str
  requested_backend: StockSelectionTrainingBackend
  resolved_backend: StockSelectionResolvedBackend
  folds: list[StockSelectionTrainingFold]
  coverage: JSON
  leakage: JSON
  resource_estimate: StockSelectionTrainingResourceEstimate
  shadow_reasons: list[str]
  blockers: list[str]
  warnings: list[str]
  capability: JSON
  spec_hash: str
  coordinate_hash: str
  can_submit: bool


@strawberry.type(description="次日概率训练运行及安全证据投影")
class StockSelectionTrainingRun:
  run_id: str
  run_key: Optional[str]
  run_kind: StockSelectionTrainingRunKind
  parent_run_id: Optional[str]
  status: StockSelectionTrainingRunStatus
  phase: StockSelectionTrainingPhase
  completed_units: int
  total_units: int
  requested_at: datetime
  started_at: Optional[datetime]
  completed_at: Optional[datetime]
  cancel_requested_at: Optional[datetime]
  state_version: int
  dataset_version: Optional[str]
  requested_backend: Optional[StockSelectionTrainingBackend]
  resolved_backend: Optional[StockSelectionResolvedBackend]
  spec_hash: Optional[str]
  environment_requirement_hash: Optional[str]
  coordinate_hash: Optional[str]
  experiment_group_hash: Optional[str]
  artifact_manifest_sha256: Optional[str]
  environment_evidence: JSON
  metrics_summary: JSON
  gate_summary: JSON
  conclusion: Optional[StockSelectionTrainingConclusion]
  registerable: bool
  queue_reason: Optional[str]
  error_code: Optional[str]
  error_message: Optional[str]


@strawberry.type(description="次日概率训练运行分页")
class StockSelectionTrainingRunPage:
  items: list[StockSelectionTrainingRun]
  total: int
  limit: int
  offset: int


@strawberry.type(description="同坐标次日概率最终评估对比")
class StockSelectionTrainingComparison:
  comparable: bool
  mismatch_fields: list[str]
  mismatched_fields: JSON
  runs: list[StockSelectionTrainingRun]
  metrics: JSON
  gates: JSON
