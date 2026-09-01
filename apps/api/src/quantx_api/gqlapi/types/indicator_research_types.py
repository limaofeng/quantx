"""Typed public indicator statistics; research remains read-only."""

from datetime import datetime
from typing import Optional

import strawberry
from strawberry.scalars import JSON

from .stock_screening_types import StockIndicatorConditionInput, StockScreenUniverse


@strawberry.input(description="一个单指标或当前条件交集的报告查询")
class StockIndicatorReportRequestInput:
  request_id: str
  kind: str
  indicator_ids: list[str]
  conditions: Optional[list[StockIndicatorConditionInput]] = None
  universe: StockScreenUniverse = StockScreenUniverse.STOCK
  exclude_st: bool = True
  include_industries: Optional[list[str]] = None
  exclude_industries: Optional[list[str]] = None

  def as_request(self) -> dict:
    return {
      "request_id": self.request_id,
      "kind": self.kind,
      "indicator_ids": self.indicator_ids,
      "conditions": [
        {
          "indicator_id": item.indicator_id,
          "operator": item.operator,
          "value": item.value,
          "value_to": item.value_to,
        }
        for item in self.conditions or []
      ],
      "universe": {
        "instrument_type": self.universe.value,
        "exclude_st": self.exclude_st,
        "include_industries": self.include_industries or [],
        "exclude_industries": self.exclude_industries or [],
      },
    }


@strawberry.type(description="可快捷打开的指标报告及研究数据边界")
class IndicatorReportReference:
  run_key: str
  report_id: str
  kind: str
  indicator_ids: list[str]
  study_id: str
  version: str
  run_id: str
  completed_at: Optional[datetime]
  data_start: Optional[str]
  data_end: Optional[str]
  config_hash: Optional[str]
  warnings: list[str]
  match_status: Optional[str] = None
  match_reason: Optional[str] = None


@strawberry.type(description="匹配状态与离线研究配置；不启动研究任务")
class StockIndicatorReportMatch:
  request_id: str
  status: str
  reason: Optional[str]
  reports: list[IndicatorReportReference]
  config_json: JSON
  command: str
  blockers: list[str]

  @staticmethod
  def from_record(record: dict) -> "StockIndicatorReportMatch":
    return StockIndicatorReportMatch(
      **{
        **record,
        "reports": [IndicatorReportReference(**item) for item in record["reports"]],
      }
    )


@strawberry.type(description="一个分组、观察周期及收益口径的描述统计与日期配对推断")
class IndicatorReportRow:
  group: str
  horizon: int
  return_basis: str
  period: str
  sample_count: int
  stock_count: int
  date_count: int
  up_rate: Optional[float]
  mean_return: Optional[float]
  median_return: Optional[float]
  date_equal_up_rate: Optional[float]
  date_equal_mean_return: Optional[float]
  baseline_up_rate: Optional[float]
  up_rate_lift: Optional[float]
  mean_return_lift: Optional[float]
  ci_low: Optional[float]
  ci_high: Optional[float]
  p_value: Optional[float]
  q_value: Optional[float]
  mean_ci_low: Optional[float]
  mean_ci_high: Optional[float]
  mean_p_value: Optional[float]
  mean_q_value: Optional[float]
  inference_status: str


@strawberry.type(description="单指标或联合指标的安全结构化研究报告")
class IndicatorReportDetail:
  reference: IndicatorReportReference
  indicator_version: str
  universe: JSON
  horizons: list[int]
  return_bases: list[str]
  conditions: JSON
  definitions: JSON
  coverage: JSON
  distribution: JSON
  rows: list[IndicatorReportRow]
  warnings: list[str]
  artifact_errors: list[str]
  config_json: Optional[JSON]

  @staticmethod
  def from_record(record: dict) -> "IndicatorReportDetail":
    return IndicatorReportDetail(
      **{
        **record,
        "reference": IndicatorReportReference(**record["reference"]),
        "rows": [IndicatorReportRow(**item) for item in record["rows"]],
      }
    )
