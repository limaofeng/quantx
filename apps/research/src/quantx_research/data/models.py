"""研究数据层的稳定输入输出模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import pandas as pd

AdjustmentMode = Literal["none", "front", "back", "point_in_time"]

CANONICAL_BAR_COLUMNS = (
  "stock_code",
  "time",
  "open",
  "high",
  "low",
  "close",
  "volume",
  "amount",
  "suspend_flag",
)

INSTRUMENT_COLUMNS = (
  "stock_code",
  "instrument_type",
  "name",
  "market",
  "open_date",
  "expire_date",
)

FACTOR_COLUMNS = ("stock_code", "time", "dr")


@dataclass(frozen=True, slots=True)
class SymbolCoverage:
  """单个标的在请求区间内的数据覆盖和异常计数。"""

  stock_code: str
  rows: int
  first_time: datetime | None
  last_time: datetime | None
  valid_rows: int = 0
  first_valid_time: datetime | None = None
  last_valid_time: datetime | None = None
  duplicate_rows: int = 0
  missing_price_rows: int = 0
  invalid_ohlc_rows: int = 0
  negative_volume_rows: int = 0
  zero_volume_rows: int = 0
  suspended_rows: int = 0
  adjustment_valid: bool = True
  has_instrument_metadata: bool = True
  has_start_coverage: bool = False
  has_end_coverage: bool = False
  has_minimum_observations: bool = False

  def to_dict(self) -> dict[str, Any]:
    payload = asdict(self)
    payload["first_time"] = _datetime_to_iso(self.first_time)
    payload["last_time"] = _datetime_to_iso(self.last_time)
    payload["first_valid_time"] = _datetime_to_iso(self.first_valid_time)
    payload["last_valid_time"] = _datetime_to_iso(self.last_valid_time)
    return payload

  @classmethod
  def from_dict(cls, payload: dict[str, Any]) -> "SymbolCoverage":
    values = dict(payload)
    values["first_time"] = _iso_to_datetime(values.get("first_time"))
    values["last_time"] = _iso_to_datetime(values.get("last_time"))
    values["first_valid_time"] = _iso_to_datetime(values.get("first_valid_time"))
    values["last_valid_time"] = _iso_to_datetime(values.get("last_valid_time"))
    return cls(**values)


@dataclass(frozen=True, slots=True)
class DataQualityReport:
  """一次数据集构建的覆盖率与质量报告。"""

  requested_start: datetime
  requested_end: datetime
  requested_codes: tuple[str, ...]
  loaded_codes: tuple[str, ...]
  missing_codes: tuple[str, ...]
  row_count: int
  duplicate_rows: int
  missing_price_rows: int
  invalid_ohlc_rows: int
  negative_volume_rows: int
  zero_volume_rows: int
  suspended_rows: int
  valid_row_count: int = 0
  invalid_adjustment_codes: tuple[str, ...] = ()
  missing_metadata_codes: tuple[str, ...] = ()
  insufficient_history_codes: tuple[str, ...] = ()
  coverage: tuple[SymbolCoverage, ...] = ()
  warnings: tuple[str, ...] = ()

  @property
  def is_usable(self) -> bool:
    """至少有一只具备历史、元数据和有效复权的标的可进入研究。"""
    has_usable_symbol = any(
      item.valid_rows > 0
      and item.has_minimum_observations
      and item.has_instrument_metadata
      and item.adjustment_valid
      for item in self.coverage
    )
    return bool(self.loaded_codes) and self.valid_row_count > 0 and has_usable_symbol

  def to_dict(self) -> dict[str, Any]:
    payload = asdict(self)
    payload["requested_start"] = self.requested_start.isoformat()
    payload["requested_end"] = self.requested_end.isoformat()
    payload["coverage"] = [item.to_dict() for item in self.coverage]
    payload["is_usable"] = self.is_usable
    return payload

  @classmethod
  def from_dict(cls, payload: dict[str, Any]) -> "DataQualityReport":
    values = dict(payload)
    values.pop("is_usable", None)
    values["requested_start"] = _iso_to_datetime(values["requested_start"])
    values["requested_end"] = _iso_to_datetime(values["requested_end"])
    for key in (
      "requested_codes",
      "loaded_codes",
      "missing_codes",
      "invalid_adjustment_codes",
      "missing_metadata_codes",
      "insufficient_history_codes",
      "warnings",
    ):
      values[key] = tuple(values.get(key, ()))
    values["coverage"] = tuple(
      SymbolCoverage.from_dict(item) for item in values.get("coverage", ())
    )
    return cls(**values)


@dataclass(frozen=True, slots=True)
class DividendFactorCoverageReport:
  """Database-backed proof that sparse factor windows were authoritatively synced."""

  requested_start: datetime
  requested_end: datetime
  requested_codes: tuple[str, ...]
  covered_codes: tuple[str, ...]
  uncovered_codes: tuple[str, ...]
  evidence_request_ids: tuple[str, ...] = ()
  latest_completed_at: datetime | None = None
  invalid_evidence_count: int = 0
  warnings: tuple[str, ...] = ()

  @property
  def is_complete(self) -> bool:
    return bool(self.requested_codes) and not self.uncovered_codes

  @property
  def coverage_ratio(self) -> float:
    return (
      len(self.covered_codes) / len(self.requested_codes)
      if self.requested_codes
      else 0.0
    )

  def to_dict(self) -> dict[str, Any]:
    payload = asdict(self)
    payload["requested_start"] = self.requested_start.isoformat()
    payload["requested_end"] = self.requested_end.isoformat()
    payload["latest_completed_at"] = _datetime_to_iso(self.latest_completed_at)
    payload["is_complete"] = self.is_complete
    payload["coverage_ratio"] = self.coverage_ratio
    return payload

  @classmethod
  def from_dict(cls, payload: dict[str, Any]) -> "DividendFactorCoverageReport":
    values = dict(payload)
    values.pop("is_complete", None)
    values.pop("coverage_ratio", None)
    values["requested_start"] = _iso_to_datetime(values["requested_start"])
    values["requested_end"] = _iso_to_datetime(values["requested_end"])
    values["latest_completed_at"] = _iso_to_datetime(values.get("latest_completed_at"))
    for key in (
      "requested_codes",
      "covered_codes",
      "uncovered_codes",
      "evidence_request_ids",
      "warnings",
    ):
      values[key] = tuple(values.get(key, ()))
    return cls(**values)


@dataclass(slots=True)
class ResearchDataset:
  """供研究运行器消费的标准化数据集。"""

  panel: pd.DataFrame
  benchmark: pd.DataFrame
  quality: DataQualityReport
  instruments: pd.DataFrame = field(default_factory=pd.DataFrame)
  factors: pd.DataFrame = field(default_factory=pd.DataFrame)
  factor_coverage: DividendFactorCoverageReport | None = None


def _datetime_to_iso(value: datetime | None) -> str | None:
  return value.isoformat() if value is not None else None


def _iso_to_datetime(value: Any) -> datetime | None:
  if value is None or value == "":
    return None
  if isinstance(value, datetime):
    return value
  return datetime.fromisoformat(str(value))
