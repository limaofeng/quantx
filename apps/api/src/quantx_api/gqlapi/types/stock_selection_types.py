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
