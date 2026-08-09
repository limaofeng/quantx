"""研究数据覆盖和质量检查。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .models import DataQualityReport, SymbolCoverage
from .normalization import as_datetime

_PRICE_COLUMNS = ["open", "high", "low", "close"]


def build_quality_report(
  panel: pd.DataFrame,
  *,
  requested_codes: Iterable[str],
  requested_start: date | datetime,
  requested_end: date | datetime,
  metadata_codes: Iterable[str] = (),
  minimum_observations: int = 0,
  boundary_tolerance_days: int = 7,
) -> DataQualityReport:
  """从尚未去重的标准面板生成可审计质量报告。"""
  start = as_datetime(requested_start)
  end = as_datetime(requested_end)
  requested = tuple(sorted({str(code).upper() for code in requested_codes}))
  metadata = {str(code).upper() for code in metadata_codes}
  loaded = tuple(
    sorted(str(code).upper() for code in panel["stock_code"].dropna().unique())
  )
  missing = tuple(sorted(set(requested) - set(loaded)))

  coverage: list[SymbolCoverage] = []
  for code in requested:
    rows = panel[panel["stock_code"] == code]
    coverage_rows = (
      rows.sort_values("time", kind="stable")
      .drop_duplicates(["stock_code", "time"], keep="last")
      .copy()
    )
    duplicate_rows = int(rows.duplicated(["stock_code", "time"], keep=False).sum())
    missing_prices = int(rows[_PRICE_COLUMNS].isna().any(axis=1).sum())
    invalid_ohlc = _invalid_ohlc_count(rows)
    volume = pd.to_numeric(rows.get("volume"), errors="coerce")
    negative_volume = int((volume < 0).sum())
    zero_volume = int((volume == 0).sum())
    suspended = int(
      (pd.to_numeric(rows.get("suspend_flag"), errors="coerce") == 1).sum()
    )
    first_time = _to_python_datetime(rows["time"].min()) if not rows.empty else None
    last_time = _to_python_datetime(rows["time"].max()) if not rows.empty else None
    # Coverage mirrors the panel that enters research: one final record per
    # stock-date. Raw rows above remain the source for anomaly counts.
    valid_mask = _valid_trading_row_mask(coverage_rows)
    valid_rows = coverage_rows.loc[valid_mask]
    first_valid_time = (
      _to_python_datetime(valid_rows["time"].min()) if not valid_rows.empty else None
    )
    last_valid_time = (
      _to_python_datetime(valid_rows["time"].max()) if not valid_rows.empty else None
    )
    adjustment_valid = bool(
      not rows.empty
      and rows.get("adjustment_valid", pd.Series(False, index=rows.index))
      .fillna(False)
      .all()
    )
    coverage.append(
      SymbolCoverage(
        stock_code=code,
        rows=len(rows),
        first_time=first_time,
        last_time=last_time,
        valid_rows=len(valid_rows),
        first_valid_time=first_valid_time,
        last_valid_time=last_valid_time,
        duplicate_rows=duplicate_rows,
        missing_price_rows=missing_prices,
        invalid_ohlc_rows=invalid_ohlc,
        negative_volume_rows=negative_volume,
        zero_volume_rows=zero_volume,
        suspended_rows=suspended,
        adjustment_valid=adjustment_valid,
        has_instrument_metadata=code in metadata,
        has_start_coverage=(
          first_valid_time is not None
          and first_valid_time <= start + timedelta(days=boundary_tolerance_days)
        ),
        has_end_coverage=(
          last_valid_time is not None
          and last_valid_time >= end - timedelta(days=boundary_tolerance_days)
        ),
        has_minimum_observations=len(valid_rows) >= minimum_observations,
      )
    )

  invalid_adjustment = tuple(
    item.stock_code for item in coverage if item.rows > 0 and not item.adjustment_valid
  )
  missing_metadata = tuple(
    item.stock_code for item in coverage if not item.has_instrument_metadata
  )
  insufficient_history = tuple(
    item.stock_code for item in coverage if not item.has_minimum_observations
  )
  warnings: list[str] = []
  if missing:
    warnings.append(f"{len(missing)} 个请求标的没有日线数据")
  if invalid_adjustment:
    warnings.append(f"{len(invalid_adjustment)} 个标的存在无效复权因子")
  if missing_metadata:
    warnings.append(f"{len(missing_metadata)} 个标的缺少证券元数据")
  if insufficient_history:
    warnings.append(f"{len(insufficient_history)} 个标的历史样本不足")

  return DataQualityReport(
    requested_start=start,
    requested_end=end,
    requested_codes=requested,
    loaded_codes=loaded,
    missing_codes=missing,
    row_count=len(panel),
    duplicate_rows=sum(item.duplicate_rows for item in coverage),
    missing_price_rows=sum(item.missing_price_rows for item in coverage),
    invalid_ohlc_rows=sum(item.invalid_ohlc_rows for item in coverage),
    negative_volume_rows=sum(item.negative_volume_rows for item in coverage),
    zero_volume_rows=sum(item.zero_volume_rows for item in coverage),
    suspended_rows=sum(item.suspended_rows for item in coverage),
    valid_row_count=sum(item.valid_rows for item in coverage),
    invalid_adjustment_codes=invalid_adjustment,
    missing_metadata_codes=missing_metadata,
    insufficient_history_codes=insufficient_history,
    coverage=tuple(coverage),
    warnings=tuple(warnings),
  )


def combine_quality_reports(
  reports: Iterable[DataQualityReport],
  *,
  requested_codes: Iterable[str],
  requested_start: date | datetime,
  requested_end: date | datetime,
) -> DataQualityReport:
  """Combine disjoint stock-batch reports without changing quality semantics."""
  batches = tuple(reports)
  requested = tuple(sorted({str(code).strip().upper() for code in requested_codes}))
  coverage_by_code = {
    item.stock_code: item for report in batches for item in report.coverage
  }
  coverage = tuple(
    coverage_by_code.get(code, _missing_symbol_coverage(code)) for code in requested
  )
  loaded = tuple(item.stock_code for item in coverage if item.rows > 0)
  missing = tuple(item.stock_code for item in coverage if item.rows == 0)
  invalid_adjustment = tuple(
    item.stock_code for item in coverage if item.rows > 0 and not item.adjustment_valid
  )
  missing_metadata = tuple(
    item.stock_code for item in coverage if not item.has_instrument_metadata
  )
  insufficient_history = tuple(
    item.stock_code for item in coverage if not item.has_minimum_observations
  )
  warnings: list[str] = []
  if missing:
    warnings.append(f"{len(missing)} 个请求标的没有日线数据")
  if invalid_adjustment:
    warnings.append(f"{len(invalid_adjustment)} 个标的存在无效复权因子")
  if missing_metadata:
    warnings.append(f"{len(missing_metadata)} 个标的缺少证券元数据")
  if insufficient_history:
    warnings.append(f"{len(insufficient_history)} 个标的历史样本不足")
  return DataQualityReport(
    requested_start=as_datetime(requested_start),
    requested_end=as_datetime(requested_end),
    requested_codes=requested,
    loaded_codes=loaded,
    missing_codes=missing,
    row_count=sum(item.rows for item in coverage),
    duplicate_rows=sum(item.duplicate_rows for item in coverage),
    missing_price_rows=sum(item.missing_price_rows for item in coverage),
    invalid_ohlc_rows=sum(item.invalid_ohlc_rows for item in coverage),
    negative_volume_rows=sum(item.negative_volume_rows for item in coverage),
    zero_volume_rows=sum(item.zero_volume_rows for item in coverage),
    suspended_rows=sum(item.suspended_rows for item in coverage),
    valid_row_count=sum(item.valid_rows for item in coverage),
    invalid_adjustment_codes=invalid_adjustment,
    missing_metadata_codes=missing_metadata,
    insufficient_history_codes=insufficient_history,
    coverage=coverage,
    warnings=tuple(warnings),
  )


def _missing_symbol_coverage(code: str) -> SymbolCoverage:
  return SymbolCoverage(
    stock_code=code,
    rows=0,
    first_time=None,
    last_time=None,
    adjustment_valid=False,
    has_instrument_metadata=False,
  )


def _invalid_ohlc_count(rows: pd.DataFrame) -> int:
  if rows.empty:
    return 0
  prices = rows[_PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
  nonfinite = ~pd.DataFrame(
    np.isfinite(prices.to_numpy(dtype=float)),
    index=prices.index,
    columns=prices.columns,
  ).all(axis=1)
  nonpositive = (prices <= 0).any(axis=1)
  inconsistent = prices["high"] < prices[["open", "close", "low"]].max(axis=1)
  inconsistent |= prices["low"] > prices[["open", "close", "high"]].min(axis=1)
  return int((nonfinite | nonpositive | inconsistent).sum())


def _valid_trading_row_mask(rows: pd.DataFrame) -> pd.Series:
  if rows.empty:
    return pd.Series(False, index=rows.index, dtype=bool)
  prices = rows[_PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
  volume = pd.to_numeric(rows.get("volume"), errors="coerce")
  finite_prices = pd.Series(
    np.isfinite(prices.to_numpy(dtype=float)).all(axis=1),
    index=rows.index,
  )
  valid = finite_prices & (prices > 0).all(axis=1)
  valid &= prices["high"] >= prices[["open", "close", "low"]].max(axis=1)
  valid &= prices["low"] <= prices[["open", "close", "high"]].min(axis=1)
  finite_volume = pd.Series(
    np.isfinite(volume.to_numpy(dtype=float)),
    index=rows.index,
  )
  valid &= finite_volume & (volume > 0)
  suspended = pd.to_numeric(rows.get("suspend_flag"), errors="coerce")
  valid &= suspended.fillna(1) != 1
  if "listing_valid" in rows:
    valid &= rows["listing_valid"].fillna(False).astype(bool)
  if "adjustment_valid" in rows:
    valid &= rows["adjustment_valid"].fillna(False).astype(bool)
  else:
    valid &= False
  return valid.fillna(False)


def _to_python_datetime(value: object) -> datetime | None:
  if value is None or pd.isna(value):
    return None
  timestamp = pd.Timestamp(value)
  return timestamp.to_pydatetime()
