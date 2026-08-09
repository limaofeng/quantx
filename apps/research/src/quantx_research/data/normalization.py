"""把仓储返回值转换成研究应用使用的稳定表结构。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from typing import Any

import pandas as pd

from .models import CANONICAL_BAR_COLUMNS, FACTOR_COLUMNS, INSTRUMENT_COLUMNS

_NUMERIC_BAR_COLUMNS = ("open", "high", "low", "close", "volume", "amount")


def normalize_instrument_type(value: Any) -> str | None:
  """把 ORM/Strawberry 枚举或普通字符串归一为稳定的小写类型名。"""
  if value is None or value is pd.NA:
    return None
  enum_name = getattr(value, "name", None)
  if enum_name:
    return str(enum_name).strip().lower()
  raw_value = getattr(value, "value", value)
  nested_value = getattr(raw_value, "value", None)
  if nested_value is not None and not isinstance(raw_value, str):
    raw_value = nested_value
  normalized = str(raw_value).strip().lower()
  return normalized or None


def normalize_daily_bars(
  frames: Mapping[str, pd.DataFrame] | pd.DataFrame | None,
) -> pd.DataFrame:
  """标准化批量日线，不去重，以便质量报告保留重复记录证据。"""
  if frames is None:
    return _empty_frame(CANONICAL_BAR_COLUMNS)

  if isinstance(frames, pd.DataFrame):
    combined = frames.copy()
  else:
    parts: list[pd.DataFrame] = []
    for code, frame in frames.items():
      if frame is None or frame.empty:
        continue
      part = frame.copy()
      if "stock_code" not in part.columns:
        part["stock_code"] = str(code).upper()
      parts.append(part)
    if not parts:
      return _empty_frame(CANONICAL_BAR_COLUMNS)
    combined = pd.concat(parts, ignore_index=True, sort=False)

  for column in CANONICAL_BAR_COLUMNS:
    if column not in combined.columns:
      combined[column] = pd.NA
  combined["stock_code"] = combined["stock_code"].astype("string").str.upper()
  combined["time"] = _normalize_market_dates(combined["time"])
  for column in _NUMERIC_BAR_COLUMNS:
    combined[column] = pd.to_numeric(combined[column], errors="coerce")
  combined["suspend_flag"] = pd.to_numeric(
    combined["suspend_flag"], errors="coerce"
  ).astype("Int64")
  return (
    combined.loc[:, CANONICAL_BAR_COLUMNS]
    .dropna(subset=["stock_code", "time"])
    .sort_values(["stock_code", "time"], kind="stable")
    .reset_index(drop=True)
  )


def normalize_instruments(items: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
  """将 ORM 实体或 DataFrame 转换为证券时点元数据。"""
  if isinstance(items, pd.DataFrame):
    frame = items.copy()
    if "code" in frame.columns and "stock_code" not in frame.columns:
      frame = frame.rename(columns={"code": "stock_code"})
    if "type" in frame.columns and "instrument_type" not in frame.columns:
      frame = frame.rename(columns={"type": "instrument_type"})
  else:
    rows: list[dict[str, Any]] = []
    for item in items:
      code = getattr(item, "id", None) or getattr(item, "code", None)
      rows.append(
        {
          "stock_code": code,
          "instrument_type": normalize_instrument_type(getattr(item, "type", None)),
          "name": getattr(item, "name", None),
          "market": getattr(item, "market", None),
          "open_date": getattr(item, "open_date", None),
          "expire_date": getattr(item, "expire_date", None),
        }
      )
    frame = pd.DataFrame(rows)

  for column in INSTRUMENT_COLUMNS:
    if column not in frame.columns:
      frame[column] = pd.NA
  frame["stock_code"] = frame["stock_code"].astype("string").str.upper()
  frame["instrument_type"] = frame["instrument_type"].map(normalize_instrument_type)
  frame["open_date"] = (
    pd.to_datetime(frame["open_date"], errors="coerce")
    .dt.normalize()
    .astype("datetime64[ns]")
  )
  frame["expire_date"] = (
    pd.to_datetime(frame["expire_date"], errors="coerce")
    .dt.normalize()
    .astype("datetime64[ns]")
  )
  return (
    frame.loc[:, INSTRUMENT_COLUMNS]
    .dropna(subset=["stock_code"])
    .drop_duplicates(subset=["stock_code"], keep="last")
    .sort_values("stock_code", kind="stable")
    .reset_index(drop=True)
  )


def normalize_dividend_factors(
  factors: Iterable[Any] | pd.DataFrame,
) -> pd.DataFrame:
  """将 DividFactor 实体转换为按交易日对齐的复权因子表。"""
  if isinstance(factors, pd.DataFrame):
    frame = factors.copy()
  else:
    rows = [
      {
        "stock_code": getattr(item, "stock_code", None),
        "time": getattr(item, "time", None),
        "dr": getattr(item, "dr", None),
      }
      for item in factors
    ]
    frame = pd.DataFrame(rows)

  for column in FACTOR_COLUMNS:
    if column not in frame.columns:
      frame[column] = pd.NA
  frame["stock_code"] = frame["stock_code"].astype("string").str.upper()
  frame["time"] = _normalize_reference_dates(frame["time"])
  frame["dr"] = pd.to_numeric(frame["dr"], errors="coerce")
  return (
    frame.loc[:, FACTOR_COLUMNS]
    .dropna(subset=["stock_code", "time"])
    .sort_values(["stock_code", "time"], kind="stable")
    .reset_index(drop=True)
  )


def as_datetime(value: date | datetime) -> datetime:
  if isinstance(value, datetime):
    return value
  return datetime.combine(value, time.min)


def _normalize_market_dates(values: pd.Series) -> pd.Series:
  parsed = pd.to_datetime(values, errors="coerce", utc=True)
  return (
    parsed.dt.tz_convert("Asia/Shanghai")
    .dt.tz_localize(None)
    .dt.normalize()
    .astype("datetime64[ns]")
  )


def _normalize_reference_dates(values: pd.Series) -> pd.Series:
  parsed = pd.to_datetime(values, errors="coerce")
  try:
    timezone = parsed.dt.tz
  except AttributeError:
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    timezone = parsed.dt.tz
  if timezone is not None:
    parsed = parsed.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
  return parsed.dt.normalize().astype("datetime64[ns]")


def _empty_frame(columns: tuple[str, ...]) -> pd.DataFrame:
  return pd.DataFrame(columns=list(columns))
