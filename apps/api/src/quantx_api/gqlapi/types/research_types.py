"""GraphQL projections for offline research results."""

from datetime import datetime
from typing import Optional

import strawberry
from strawberry.scalars import JSON

from quantx_api.research_artifacts import (
  ResearchRunDetailRecord,
  ResearchRunRecord,
)


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
    )
