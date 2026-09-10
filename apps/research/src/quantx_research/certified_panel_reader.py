"""Verify every certified row while retaining only requested training features."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from quantx_domain.selection_factors import selection_feature_columns

BATCH_ROWS = 16_384


def read_panel(path: Path, manifest: dict, *, config=None):
  # The historical fingerprint sorts all rows by their original keys. Keep
  # only those two keys and the uint64 row hash, not the full feature matrix.
  keys, selected = [], []
  dates, codes = set(), set()
  total = 0
  columns = ["event_date", "stock_code", "label", "next_open_to_close_return",
             *selection_feature_columns()]
  required = {*columns, "target_date", "month", "open_date", "valid_history"}
  selection = None
  if config is not None:
    selection = (
      pd.Timestamp(config.data.date_range[0]).normalize(),
      pd.Timestamp(config.data.date_range[1]).normalize(),
      {str(code).upper() for code in (config.data.stock_codes or ())},
    )
  empty = None
  with pq.ParquetFile(path) as parquet:
    if required - set(parquet.schema_arrow.names):
      raise ValueError("认证训练面板缺少不可变训练或指纹字段")
    for record in parquet.iter_batches(batch_size=BATCH_ROWS, use_threads=False):
      batch = record.to_pandas(use_threads=False)
      if empty is None:
        empty = batch.iloc[:0].copy()
      actual_dates = pd.to_datetime(batch["event_date"], errors="coerce").dt.normalize()
      if actual_dates.isna().any():
        raise ValueError("认证训练面板含非法 event_date")
      normalized_codes = batch["stock_code"].astype(str).str.upper()
      dates.update(actual_dates.unique())
      codes.update(normalized_codes.unique())
      total += len(batch)
      key = batch[["event_date", "stock_code"]].copy()
      key["row_hash"] = pd.util.hash_pandas_object(batch[columns], index=False).to_numpy()
      keys.append(key)
      if selection is not None:
        start, end, requested_codes = selection
        mask = actual_dates.between(start, end)
        if requested_codes:
          mask &= normalized_codes.isin(requested_codes)
        if mask.any():
          selected.append(batch.loc[mask].copy())
  if not total:
    raise ValueError("认证训练面板不能为空")
  quality = manifest["quality"]
  if str(pd.Timestamp(min(dates)).date()) != manifest["date_start"] or str(pd.Timestamp(max(dates)).date()) != manifest["date_end"]:
    raise ValueError("认证日期证据与训练面板实际日期不匹配")
  if (total, len(codes), len(dates)) != (quality["sample_count"], quality["stock_count"], quality["trading_day_count"]):
    raise ValueError("认证质量计数与训练面板实际计数不匹配")
  import re

  if any(not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code) for code in codes):
    raise ValueError("认证训练面板含非规范股票代码")
  universe = manifest["universe_spec"]
  if universe["kind"] == "ORDINARY_A_SHARE" and any(
    not re.fullmatch(r"(?:(?:600|601|603|605|688|689)\d{3}\.SH|(?:000|001|002|003|300|301)\d{3}\.SZ)", code)
    for code in codes
  ):
    raise ValueError("普通 A 股认证训练面板含非普通 A 股代码")
  if universe["stock_codes"] is not None and not codes <= set(universe["stock_codes"]):
    raise ValueError("认证训练面板含 universe_spec 之外的股票")
  ordered = pd.concat(keys, ignore_index=True)
  # Arrow can supply different categorical dictionaries in separate batches.
  # Preserve the same union/order that a whole-file Pandas load would use.
  for name in ("event_date", "stock_code"):
    if all(isinstance(key[name].dtype, pd.CategoricalDtype) for key in keys):
      ordered[name] = pd.api.types.union_categoricals([key[name] for key in keys])
  del keys
  ordered = ordered.sort_values(["event_date", "stock_code"], kind="mergesort")
  digest = hashlib.sha256()
  for start in range(0, len(ordered), BATCH_ROWS):
    digest.update(ordered["row_hash"].iloc[start:start + BATCH_ROWS].to_numpy().tobytes())
  if digest.hexdigest() != manifest["data_fingerprint"]:
    raise ValueError("认证数据集面板内容哈希不匹配")
  return pd.concat(selected, ignore_index=True) if selected else empty
