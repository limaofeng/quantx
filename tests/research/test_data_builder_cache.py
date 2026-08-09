from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from quantx_research.data import (
  DatasetBuilder,
  DividendFactorCoverageError,
  ParquetDatasetCache,
)


class FakeResearchSource:
  def __init__(self) -> None:
    self.bar_requests: list[list[str]] = []
    self.factor_coverage_complete = True

  async def list_instruments(
    self,
    *,
    instrument_types=("stock",),
    codes=None,
  ) -> pd.DataFrame:
    frame = pd.DataFrame(
      [
        {
          "stock_code": "000001.SZ",
          "instrument_type": "stock",
          "name": "A",
          "market": "SZ",
          "open_date": pd.Timestamp("2000-01-01"),
          "expire_date": pd.NaT,
        },
        {
          "stock_code": "000002.SZ",
          "instrument_type": "stock",
          "name": "B",
          "market": "SZ",
          "open_date": pd.Timestamp("2024-01-03"),
          "expire_date": pd.NaT,
        },
        {
          "stock_code": "000300.SH",
          "instrument_type": "index",
          "name": "CSI300",
          "market": "SH",
          "open_date": pd.Timestamp("2005-04-08"),
          "expire_date": pd.NaT,
        },
        {
          "stock_code": "830001.BJ",
          "instrument_type": "stock",
          "name": "BJ",
          "market": "BJ",
          "open_date": pd.Timestamp("2000-01-01"),
          "expire_date": pd.NaT,
        },
        {
          "stock_code": "600001.SH",
          "instrument_type": "stock",
          "name": "expired",
          "market": "SH",
          "open_date": pd.Timestamp("2000-01-01"),
          "expire_date": pd.Timestamp("2023-12-31"),
        },
        {
          "stock_code": "600002.SH",
          "instrument_type": "stock",
          "name": "future",
          "market": "SH",
          "open_date": pd.Timestamp("2024-01-04"),
          "expire_date": pd.NaT,
        },
      ]
    )
    frame = frame[frame["instrument_type"].isin(instrument_types)]
    if codes is not None:
      frame = frame[frame["stock_code"].isin(codes)]
    return frame.reset_index(drop=True)

  async def load_daily_bars(
    self,
    stock_codes,
    start,
    end,
    *,
    batch_size=300,
  ) -> pd.DataFrame:
    self.bar_requests.append(list(stock_codes))
    rows = []
    for code in stock_codes:
      for offset, timestamp in enumerate(pd.date_range("2024-01-01", periods=3)):
        rows.append(
          {
            "stock_code": code,
            "time": timestamp,
            "open": float(10 + offset),
            "high": float(11 + offset),
            "low": float(9 + offset),
            "close": float(10.5 + offset),
            "volume": float(100 + offset),
            "amount": float(1_000 + offset),
            "suspend_flag": 0,
          }
        )
    # 重复数据只应留在质量报告中。
    rows.append(dict(rows[1]))
    return pd.DataFrame(rows)

  async def load_dividend_factors(
    self,
    stock_codes,
    *,
    start=None,
    end=None,
  ) -> pd.DataFrame:
    return pd.DataFrame(
      [
        {
          "stock_code": "000001.SZ",
          "time": pd.Timestamp("2024-01-02"),
          "dr": 1.1,
        }
      ]
    )

  async def load_dividend_factor_coverage(
    self,
    stock_codes,
    *,
    start,
    end,
  ) -> pd.DataFrame:
    covered = (
      list(stock_codes) if self.factor_coverage_complete else list(stock_codes)[:-1]
    )
    return pd.DataFrame(
      [
        {
          "request_id": "factor-request",
          "source": "qmt-get-divid-factors-v1",
          "status": "COMPLETED",
          "start_date": pd.Timestamp(start).strftime("%Y%m%d"),
          "end_date": pd.Timestamp(end).strftime("%Y%m%d"),
          "stock_codes": covered,
          "expected_chunks": 1,
          "received_chunks": 1,
          "completed_at": pd.Timestamp("2024-01-04"),
        }
      ]
    )


@pytest.mark.asyncio
async def test_dataset_builder_splits_benchmark_and_appends_audit_fields() -> None:
  source = FakeResearchSource()

  dataset = await DatasetBuilder(source).build(
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 3),
    stock_codes=["000001.SZ", "000002.SZ"],
    minimum_observations=3,
  )

  assert source.bar_requests == [["000001.SZ", "000002.SZ", "000300.SH"]]
  assert set(dataset.panel["stock_code"]) == {"000001.SZ", "000002.SZ"}
  assert set(dataset.benchmark["stock_code"]) == {"000300.SH"}
  assert len(dataset.panel) == 6
  assert len(dataset.benchmark) == 3
  assert dataset.quality.duplicate_rows == 2
  assert {
    "open_date",
    "expire_date",
    "listing_valid",
    "adjustment_valid",
  }.issubset(dataset.panel.columns)
  not_yet_listed = dataset.panel[
    (dataset.panel["stock_code"] == "000002.SZ")
    & (dataset.panel["time"] < pd.Timestamp("2024-01-03"))
  ]
  assert not not_yet_listed["listing_valid"].any()


@pytest.mark.asyncio
async def test_builder_can_resolve_stock_universe_from_instruments() -> None:
  source = FakeResearchSource()

  dataset = await DatasetBuilder(source).build(
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 3),
    stock_codes=None,
    adjustment="none",
    minimum_observations=3,
  )

  assert set(dataset.panel["stock_code"]) == {"000001.SZ", "000002.SZ"}
  assert dataset.factors.empty
  assert dataset.quality.requested_codes == ("000001.SZ", "000002.SZ")
  assert source.bar_requests == [["000001.SZ", "000002.SZ", "000300.SH"]]


@pytest.mark.asyncio
async def test_required_factor_coverage_fails_before_daily_bar_scan() -> None:
  source = FakeResearchSource()
  source.factor_coverage_complete = False

  with pytest.raises(DividendFactorCoverageError) as exc_info:
    await DatasetBuilder(source).build(
      start=datetime(2024, 1, 1),
      end=datetime(2024, 1, 3),
      stock_codes=["000001.SZ", "000002.SZ"],
      minimum_observations=3,
      require_factor_coverage=True,
    )

  assert exc_info.value.report.uncovered_codes == ("000300.SH",)
  assert source.bar_requests == []


@pytest.mark.asyncio
async def test_parquet_cache_round_trip_and_integrity_check(
  tmp_path: Path,
) -> None:
  dataset = await DatasetBuilder(FakeResearchSource()).build(
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 3),
    stock_codes=["000001.SZ"],
    minimum_observations=3,
    require_factor_coverage=True,
  )
  cache = ParquetDatasetCache(tmp_path / "cache")

  result_path = cache.write(dataset)
  restored = cache.read()

  assert result_path == (tmp_path / "cache").resolve()
  pd.testing.assert_frame_equal(restored.panel, dataset.panel)
  pd.testing.assert_frame_equal(restored.benchmark, dataset.benchmark)
  assert restored.quality.to_dict() == dataset.quality.to_dict()
  assert restored.factor_coverage is not None
  assert restored.factor_coverage.to_dict() == dataset.factor_coverage.to_dict()

  with (result_path / "panel.parquet").open("ab") as stream:
    stream.write(b"tampered")
  with pytest.raises(ValueError, match="校验失败"):
    cache.read()
