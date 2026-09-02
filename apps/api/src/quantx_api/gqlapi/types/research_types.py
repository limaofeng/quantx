"""GraphQL projections for offline research results."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

import strawberry
from strawberry.scalars import JSON

from quantx_api.research_artifacts import (
  ResearchRunDetailRecord,
  ResearchRunRecord,
)
from quantx_api.research_run_index import (
  ResearchLifecycleArtifactRecord,
  ResearchLifecycleRunRecord,
  ResearchLifecycleTrainingRecord,
)

from .indicator_research_types import IndicatorReportReference
from .stock_selection_types import (
  StockSelectionResolvedBackend,
  StockSelectionTrainingBackend,
  StockSelectionTrainingConclusion,
  StockSelectionTrainingPhase,
)


@strawberry.enum(description="统一研究运行生命周期阶段")
class ResearchLifecycleRunStage(Enum):
  RESEARCH = "RESEARCH"
  DEVELOPMENT = "DEVELOPMENT"
  FINAL_EVALUATION = "FINAL_EVALUATION"


@strawberry.enum(description="统一研究运行状态")
class ResearchLifecycleRunStatus(Enum):
  QUEUED = "QUEUED"
  RUNNING = "RUNNING"
  SUCCEEDED = "SUCCEEDED"
  FAILED = "FAILED"
  CANCELLED = "CANCELLED"


@strawberry.enum(description="统一研究运行目标")
class ResearchLifecycleRunTarget(Enum):
  RESEARCH_EVIDENCE = "RESEARCH_EVIDENCE"
  TRAINING_RUN = "TRAINING_RUN"


@strawberry.input(description="统一研究运行索引筛选条件")
class ResearchLifecycleRunFilter:
  study_id: Optional[str] = None
  stages: Optional[list[ResearchLifecycleRunStage]] = None
  statuses: Optional[list[ResearchLifecycleRunStatus]] = None
  date_from: Optional[date] = None
  date_to: Optional[date] = None
  search: Optional[str] = None


@strawberry.type(description="离线研究产物的安全摘要")
class ResearchLifecycleArtifactSummary:
  key: str
  version: str
  event_count: Optional[int]
  elapsed_seconds: Optional[float]
  config_hash: Optional[str]
  has_metrics: bool
  artifact_errors: list[str]

  @staticmethod
  def from_record(record: ResearchLifecycleArtifactRecord) -> "ResearchLifecycleArtifactSummary":
    return ResearchLifecycleArtifactSummary(
      key=record.key,
      version=record.version,
      event_count=record.event_count,
      elapsed_seconds=record.elapsed_seconds,
      config_hash=record.config_hash,
      has_metrics=record.has_metrics,
      artifact_errors=list(record.artifact_errors),
    )


@strawberry.type(description="次日概率训练运行的安全摘要")
class ResearchLifecycleTrainingSummary:
  run_key: Optional[str]
  dataset_version: Optional[str]
  requested_backend: Optional[StockSelectionTrainingBackend]
  resolved_backend: Optional[StockSelectionResolvedBackend]
  phase: StockSelectionTrainingPhase
  completed_units: int
  total_units: int
  conclusion: Optional[StockSelectionTrainingConclusion]
  registerable: bool
  can_start_final: bool
  queue_reason: Optional[str]
  error_code: Optional[str]
  error_message: Optional[str]

  @staticmethod
  def from_record(record: ResearchLifecycleTrainingRecord) -> "ResearchLifecycleTrainingSummary":
    return ResearchLifecycleTrainingSummary(
      run_key=record.run_key,
      dataset_version=record.dataset_version,
      requested_backend=(
        StockSelectionTrainingBackend(record.requested_backend)
        if record.requested_backend is not None
        else None
      ),
      resolved_backend=(
        StockSelectionResolvedBackend(record.resolved_backend)
        if record.resolved_backend is not None
        else None
      ),
      phase=StockSelectionTrainingPhase(record.phase),
      completed_units=record.completed_units,
      total_units=record.total_units,
      conclusion=(
        StockSelectionTrainingConclusion(record.conclusion)
        if record.conclusion is not None
        else None
      ),
      registerable=record.registerable,
      can_start_final=record.can_start_final,
      queue_reason=record.queue_reason,
      error_code=record.error_code,
      error_message=record.error_message,
    )


@strawberry.type(description="统一研究生命周期运行")
class ResearchLifecycleRun:
  id: str
  run_id: str
  study_id: str
  stage: ResearchLifecycleRunStage
  status: ResearchLifecycleRunStatus
  requested_at: Optional[datetime]
  started_at: Optional[datetime]
  completed_at: Optional[datetime]
  updated_at: Optional[datetime]
  target: ResearchLifecycleRunTarget
  artifact: Optional[ResearchLifecycleArtifactSummary]
  training: Optional[ResearchLifecycleTrainingSummary]

  @staticmethod
  def from_record(record: ResearchLifecycleRunRecord) -> "ResearchLifecycleRun":
    return ResearchLifecycleRun(
      id=record.id,
      run_id=record.run_id,
      study_id=record.study_id,
      stage=ResearchLifecycleRunStage(record.stage),
      status=ResearchLifecycleRunStatus(record.status),
      requested_at=record.requested_at,
      started_at=record.started_at,
      completed_at=record.completed_at,
      updated_at=record.updated_at,
      target=ResearchLifecycleRunTarget(record.target),
      artifact=(
        ResearchLifecycleArtifactSummary.from_record(record.artifact)
        if record.artifact is not None
        else None
      ),
      training=(
        ResearchLifecycleTrainingSummary.from_record(record.training)
        if record.training is not None
        else None
      ),
    )


@strawberry.type(description="统一研究生命周期运行连接")
class ResearchLifecycleRunConnection:
  items: list[ResearchLifecycleRun]
  total: int
  limit: int
  offset: int


@strawberry.type(description="一次已完成的离线研究运行")
class ResearchRunSummary:
  key: str = strawberry.field(description="不透明且稳定的运行标识")
  run_id: str = strawberry.field(description="研究运行 ID")
  study_id: str = strawberry.field(description="研究类型 ID")
  version: str = strawberry.field(description="研究定义版本")
  status: str = strawberry.field(description="success、failed 或 failed_preflight")
  started_at: Optional[datetime] = strawberry.field(description="开始时间")
  completed_at: Optional[datetime] = strawberry.field(description="完成时间")
  event_count: Optional[int] = strawberry.field(description="有效事件数量")
  elapsed_seconds: Optional[float] = strawberry.field(description="运行耗时（秒）")
  config_hash: Optional[str] = strawberry.field(description="研究配置内容指纹")
  has_metrics: bool = strawberry.field(
    description="是否存在满足路径与大小边界的指标产物；格式在详情读取时校验"
  )
  artifact_errors: list[str] = strawberry.field(description="产物读取告警")

  @staticmethod
  def from_record(record: ResearchRunRecord) -> "ResearchRunSummary":
    return ResearchRunSummary(
      key=record.key,
      run_id=record.run_id,
      study_id=record.study_id,
      version=record.version,
      status=record.status,
      started_at=record.started_at,
      completed_at=record.completed_at,
      event_count=record.event_count,
      elapsed_seconds=record.elapsed_seconds,
      config_hash=record.config_hash,
      has_metrics=record.has_metrics,
      artifact_errors=list(record.artifact_errors),
    )


@strawberry.type(description="离线研究运行分页结果")
class ResearchRunPage:
  items: list[ResearchRunSummary]
  total: int
  limit: int
  offset: int


@strawberry.type(description="一次离线研究运行的安全统计投影")
class ResearchRunDetail:
  summary: ResearchRunSummary
  data_quality: Optional[JSON] = strawberry.field(description="白名单化的数据质量指标")
  analysis_sample_count: Optional[int] = strawberry.field(
    description="进入对照与回归分析的全量有效样本数"
  )
  event_curve: JSON = strawberry.field(description="不同持有期的事件收益曲线")
  interaction_heatmap: JSON = strawberry.field(
    description="成交量冲击与价格位置的分组统计"
  )
  comparison: JSON = strawberry.field(
    description="异常放量相对正常成交量的日期配对对照估计"
  )
  comparison_sensitivity: JSON = strawberry.field(
    description="不同事件冷却期下的对照估计敏感性"
  )
  regressions: JSON = strawberry.field(description="面板回归结果")
  robustness: JSON = strawberry.field(description="稳健性检验结果")
  warnings: list[str] = strawberry.field(description="研究方法与结果告警")
  artifact_errors: list[str] = strawberry.field(description="产物读取告警")
  indicator_reports: list[IndicatorReportReference] = strawberry.field(
    description="本次运行生成的单指标及联合报告"
  )
  selection_metrics: Optional[JSON] = strawberry.field(
    description="次日概率模型经严格白名单校验的训练与冻结测试证据"
  )

  @staticmethod
  def from_record(record: ResearchRunDetailRecord) -> "ResearchRunDetail":
    return ResearchRunDetail(
      summary=ResearchRunSummary.from_record(record.summary),
      data_quality=record.data_quality,
      analysis_sample_count=record.analysis_sample_count,
      event_curve=record.event_curve,
      interaction_heatmap=record.interaction_heatmap,
      comparison=record.comparison,
      comparison_sensitivity=record.comparison_sensitivity,
      regressions=record.regressions,
      robustness=record.robustness,
      warnings=record.warnings,
      artifact_errors=list(record.artifact_errors),
      indicator_reports=[
        IndicatorReportReference(**item) for item in record.indicator_reports
      ],
      selection_metrics=record.selection_metrics,
    )
