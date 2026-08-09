"""从只读数据源构造事件研究面板。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .adjustments import apply_dividend_adjustment
from .factor_coverage import (
  DividendFactorCoverageError,
  build_dividend_factor_coverage_report,
)
from .models import AdjustmentMode, ResearchDataset
from .normalization import (
  normalize_daily_bars,
  normalize_dividend_factors,
  normalize_instruments,
)
from .quality import build_quality_report
from .source import ResearchDataSource

_A_SHARE_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")


class DatasetBuilder:
  """分批读取并标准化股票面板、基准和质量报告。"""

  def __init__(self, source: ResearchDataSource) -> None:
    self.source = source

  async def build(
    self,
    *,
    start: date | datetime,
    end: date | datetime,
    stock_codes: Sequence[str] | None = None,
    benchmark_code: str = "000300.SH",
    batch_size: int = 300,
    adjustment: AdjustmentMode = "point_in_time",
    minimum_observations: int = 252,
    factor_start: date | datetime | None = None,
    universe_start: date | datetime | None = None,
    require_factor_coverage: bool = False,
    cache_directory: str | Path | None = None,
  ) -> ResearchDataset:
    """构建数据集。

    调用方应把特征所需回看期包含在 ``start`` 中。``factor_start`` 默认与
    ``start`` 相同；更早的因子只产生统一尺度，不影响区间内收益或价格位置。
    """
    benchmark_code = str(benchmark_code).strip().upper()
    if stock_codes is None:
      instruments = normalize_instruments(
        await self.source.list_instruments(instrument_types=("stock",))
      )
      instruments = _filter_default_stock_universe(
        instruments,
        start=universe_start or start,
        end=end,
      )
      selected_codes = instruments["stock_code"].dropna().astype(str).tolist()
    else:
      selected_codes = _unique_codes(stock_codes)
      instruments = normalize_instruments(
        await self.source.list_instruments(
          instrument_types=("stock",),
          codes=selected_codes,
        )
      )
    requested_codes = _unique_codes(selected_codes)
    if not requested_codes:
      raise ValueError("研究窗口内没有合法的沪深 A 股标的")

    factor_coverage = None
    if adjustment != "none" and require_factor_coverage:
      coverage_codes = _unique_codes([*requested_codes, benchmark_code])
      coverage_loader = getattr(
        self.source,
        "load_dividend_factor_coverage",
        None,
      )
      evidence = (
        await coverage_loader(
          coverage_codes,
          start=factor_start or start,
          end=end,
        )
        if callable(coverage_loader)
        else None
      )
      factor_coverage = build_dividend_factor_coverage_report(
        evidence,
        requested_codes=coverage_codes,
        requested_start=factor_start or start,
        requested_end=end,
      )
      if not factor_coverage.is_complete:
        raise DividendFactorCoverageError(factor_coverage)

    benchmark_instrument = await self.source.list_instruments(
      instrument_types=("index",),
      codes=[benchmark_code],
    )
    benchmark_instrument = normalize_instruments(benchmark_instrument)
    all_instruments = pd.concat(
      [instruments, benchmark_instrument], ignore_index=True, sort=False
    ).drop_duplicates("stock_code", keep="last")

    all_codes = _unique_codes([*requested_codes, benchmark_code])
    all_bars = normalize_daily_bars(
      await self.source.load_daily_bars(
        all_codes,
        start,
        end,
        batch_size=batch_size,
      )
    )
    raw_panel = all_bars[all_bars["stock_code"].isin(requested_codes)].copy()
    benchmark = all_bars[all_bars["stock_code"] == benchmark_code].copy()

    if adjustment == "none":
      factors = pd.DataFrame(columns=["stock_code", "time", "dr"])
    else:
      factors = normalize_dividend_factors(
        await self.source.load_dividend_factors(
          requested_codes,
          start=factor_start or start,
          end=end,
        )
      )
    panel = apply_dividend_adjustment(
      raw_panel,
      factors,
      mode=adjustment,
      as_of=end,
    )
    benchmark["adjustment_valid"] = True

    panel = _append_instrument_dates(panel, instruments)
    benchmark = _append_instrument_dates(benchmark, benchmark_instrument)
    metadata_codes = instruments["stock_code"].dropna().astype(str).tolist()
    quality = build_quality_report(
      panel,
      requested_codes=requested_codes,
      requested_start=start,
      requested_end=end,
      metadata_codes=metadata_codes,
      minimum_observations=minimum_observations,
    )

    # 重复记录已进入质量报告；研究面板本身采用最后入库记录，避免事件重复。
    panel = _deduplicate(panel)
    benchmark = _deduplicate(benchmark)
    dataset = ResearchDataset(
      panel=panel,
      benchmark=benchmark,
      quality=quality,
      instruments=all_instruments.reset_index(drop=True),
      factors=factors,
      factor_coverage=factor_coverage,
    )
    if cache_directory is not None:
      from .parquet_cache import ParquetDatasetCache

      ParquetDatasetCache(cache_directory).write(dataset)
    return dataset


def _append_instrument_dates(
  bars: pd.DataFrame,
  instruments: pd.DataFrame,
) -> pd.DataFrame:
  metadata = instruments[["stock_code", "open_date", "expire_date"]].copy()
  result = bars.merge(metadata, on="stock_code", how="left", validate="many_to_one")
  result["listing_valid"] = (
    result["open_date"].isna() | (result["time"] >= result["open_date"])
  ) & (result["expire_date"].isna() | (result["time"] <= result["expire_date"]))
  return result


def _deduplicate(frame: pd.DataFrame) -> pd.DataFrame:
  if frame.empty:
    return frame.reset_index(drop=True)
  return (
    frame.sort_values(["stock_code", "time"], kind="stable")
    .drop_duplicates(["stock_code", "time"], keep="last")
    .reset_index(drop=True)
  )


def _unique_codes(codes: Sequence[str]) -> list[str]:
  return list(
    dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip())
  )


def _filter_default_stock_universe(
  instruments: pd.DataFrame,
  *,
  start: date | datetime,
  end: date | datetime,
) -> pd.DataFrame:
  """Keep legal Shanghai/Shenzhen stocks intersecting the analysis window."""
  if instruments.empty:
    return instruments.copy()
  start_at = pd.Timestamp(start).normalize()
  end_at = pd.Timestamp(end).normalize()
  if end_at < start_at:
    raise ValueError("证券总体结束日期不能早于开始日期")
  codes = instruments["stock_code"].astype("string")
  legal_code = codes.str.fullmatch(_A_SHARE_CODE_PATTERN.pattern, na=False)
  open_date = pd.to_datetime(instruments["open_date"], errors="coerce")
  expire_date = pd.to_datetime(instruments["expire_date"], errors="coerce")
  intersects = (open_date.isna() | (open_date <= end_at)) & (
    expire_date.isna() | (expire_date >= start_at)
  )
  return instruments.loc[legal_code & intersects].reset_index(drop=True)
