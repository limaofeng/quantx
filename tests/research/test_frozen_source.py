from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from quantx_research.data.factor_coverage import build_dividend_factor_coverage_report
from quantx_research.data.frozen_source import (
  FrozenResearchDataSource,
  export_frozen_source,
)
from quantx_research.data.normalization import normalize_daily_bars

START, END = date(2026, 1, 5), date(2026, 1, 7)
CODES = ["600000.SH", "000300.SH"]


def inputs():
  bars = normalize_daily_bars(
    pd.DataFrame(
      [
        {
          "stock_code": code,
          "time": day,
          "open": 10,
          "high": 11,
          "low": 9,
          "close": 10,
          "volume": 100,
          "amount": 1000,
          "suspend_flag": 0,
        }
        for code in CODES
        for day in pd.date_range(START, END)
      ]
    )
  )

  async def load(codes, start, end, **kwargs):
    return bars[bars.stock_code.isin(codes)].copy()

  coverage = pd.DataFrame(
    [
      {
        "request_id": code,
        "source": "qmt-get-divid-factors-v1",
        "status": "COMPLETED",
        "start_date": "20260105",
        "end_date": "20260107",
        "stock_codes": [code],
        "expected_chunks": 1,
        "received_chunks": 1,
        "completed_at": pd.Timestamp(END),
        "audit_schema_version": 2,
        "record_count": 0,
        "current_record_count": 0,
        "content_sha256": "a" * 64,
        "current_content_sha256": "a" * 64,
        "current_matches": True,
      }
      for code in CODES
    ],
    dtype=object,
  )
  source = SimpleNamespace(
    list_instruments=AsyncMock(
      return_value=pd.DataFrame(
        {
          "stock_code": CODES,
          "instrument_type": ["stock", "index"],
          "open_date": [pd.Timestamp("2020-01-01")] * 2,
        }
      )
    ),
    load_daily_bars=AsyncMock(side_effect=load),
    load_dividend_factors=AsyncMock(
      return_value=pd.DataFrame(columns=["stock_code", "time", "dr"])
    ),
    load_dividend_factor_coverage=AsyncMock(return_value=coverage),
  )
  calendar = SimpleNamespace(
    get_trading_calendar=AsyncMock(return_value=list(pd.date_range(START, END)))
  )
  return source, calendar, bars


@pytest.mark.asyncio
async def test_export_roundtrip_preserves_factor_gate_and_batched_bars(tmp_path):
  source, calendar, bars = inputs()
  # Mixed legacy/invalid records must retain nulls without coercing valid
  # integer evidence into floats, which the certification gate rejects.
  source.load_dividend_factor_coverage.return_value = pd.concat(
    [
      source.load_dividend_factor_coverage.return_value,
      pd.DataFrame([{"stock_codes": [CODES[0]], "status": "FAILED"}], dtype=object),
    ],
    ignore_index=True,
  )
  directory = await export_frozen_source(
    source, calendar, tmp_path / "source", start=START, end=END, batch_size=1
  )
  assert source.load_daily_bars.await_count == 2
  frozen = FrozenResearchDataSource(directory)
  # Export clients are deliberately unavailable after publication.
  source.load_daily_bars.side_effect = AssertionError("remote source reused")
  calendar.get_trading_calendar.side_effect = AssertionError("remote calendar reused")
  actual = await frozen.load_daily_bars(CODES, START, END)
  pd.testing.assert_frame_equal(actual, bars)
  assert list((await frozen.list_instruments()).stock_code) == [CODES[0]]
  assert await frozen.get_next_trading_date("SH", START) == date(2026, 1, 6)
  factors = await frozen.load_dividend_factors(CODES, start=START, end=END)
  assert factors.empty
  evidence = await frozen.load_dividend_factor_coverage(CODES, start=START, end=END)
  assert type(evidence.iloc[0].audit_schema_version) is int
  assert evidence.iloc[0].current_matches is True
  assert isinstance(evidence.iloc[0].stock_codes, list)
  report = build_dividend_factor_coverage_report(
    evidence, requested_codes=CODES, requested_start=START, requested_end=END
  )
  assert report.is_complete
  with pytest.raises(FileExistsError):
    await export_frozen_source(source, calendar, directory, start=START, end=END)


@pytest.mark.asyncio
async def test_frozen_queries_reject_missing_coverage_and_detect_late_tamper(tmp_path):
  source, calendar, _ = inputs()
  directory = await export_frozen_source(
    source, calendar, tmp_path / "source", start=START, end=END
  )
  frozen = FrozenResearchDataSource(directory)
  with pytest.raises(ValueError, match="outside frozen"):
    await frozen.load_daily_bars(CODES, date(2026, 1, 4), END)
  with pytest.raises(ValueError, match="outside frozen"):
    await frozen.load_dividend_factors(["600001.SH"])
  with pytest.raises(ValueError, match="outside frozen"):
    await frozen.get_next_trading_date("SH", END)
  with pytest.raises(ValueError, match="requires SH"):
    await frozen.get_next_trading_date("SZ", START)
  with (directory / "bars-00000.parquet").open("ab") as stream:
    stream.write(b"corruption")
  with pytest.raises(ValueError, match="integrity mismatch"):
    await frozen.load_daily_bars(CODES, START, END)
  with pytest.raises(ValueError, match="integrity mismatch"):
    FrozenResearchDataSource(directory)


@pytest.mark.asyncio
async def test_failed_export_never_publishes_partial_inputs(tmp_path):
  source, calendar, _ = inputs()
  source.load_daily_bars.side_effect = RuntimeError("interrupted export")
  with pytest.raises(RuntimeError, match="interrupted export"):
    await export_frozen_source(
      source, calendar, tmp_path / "source", start=START, end=END
    )
  assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_linked_source_file_is_rejected(tmp_path):
  source, calendar, _ = inputs()
  directory = await export_frozen_source(
    source, calendar, tmp_path / "source", start=START, end=END
  )
  target = tmp_path / "real.parquet"
  (directory / "factors.parquet").rename(target)
  (directory / "factors.parquet").symlink_to(target)
  with pytest.raises(ValueError, match="links"):
    FrozenResearchDataSource(directory)


@pytest.mark.asyncio
async def test_frozen_source_runs_real_feature_builder_with_identical_output(tmp_path):
  from datetime import timedelta

  from quantx_research.indicator_config import IndicatorStudyConfig
  from quantx_research.indicator_runner import stage_indicator_features

  source, calendar, _ = inputs()
  original = source.list_instruments.return_value

  async def instruments(*, instrument_types=("stock",), codes=None):
    return original[
      original.instrument_type.isin(instrument_types)
      & original.stock_code.isin(CODES if codes is None else codes)
    ].copy()

  source.list_instruments.side_effect = instruments
  start = START - timedelta(days=400)
  coverage = source.load_dividend_factor_coverage.return_value
  coverage["start_date"] = start.strftime("%Y%m%d")
  directory = await export_frozen_source(
    source, calendar, tmp_path / "source", start=start, end=END
  )
  frozen = FrozenResearchDataSource(directory)
  config = IndicatorStudyConfig.model_validate(
    {
      "indicator_ids": ["change_pct"],
      "date_range": [START, END],
      "universe": {"stock_codes": [CODES[0]], "benchmark_code": CODES[1]},
    }
  )
  monitor = SimpleNamespace(guard=lambda *args, **kwargs: None)
  results = []
  for name, active in [("original", source), ("frozen", frozen)]:
    output = tmp_path / name
    output.mkdir()
    stage = await stage_indicator_features(active, config, output, monitor)
    results.append((pd.concat([pd.read_parquet(path) for path in stage.paths]), stage))
  pd.testing.assert_frame_equal(results[0][0], results[1][0])
  assert results[1][0].change_pct.notna().any()
  assert (
    results[0][1].quality["data_fingerprint"]
    == results[1][1].quality["data_fingerprint"]
  )
  assert results[0][1].calendar.equals(results[1][1].calendar)
  assert results[1][1].quality["source_provenance"] == frozen.provenance
  assert len(frozen.provenance["manifest_sha256"]) == 64


@pytest.mark.asyncio
async def test_explicit_export_never_queries_unrequested_stocks(tmp_path):
  source, calendar, _ = inputs()
  source.list_instruments.return_value = pd.concat([
    source.list_instruments.return_value,
    pd.DataFrame({"stock_code": ["600001.SH"], "instrument_type": ["stock"]}),
  ], ignore_index=True)
  directory = await export_frozen_source(source, calendar, tmp_path / "source", start=START, end=END, stock_codes=[CODES[0]], benchmark_code=CODES[1])
  frozen = FrozenResearchDataSource(directory)
  assert frozen.codes == set(CODES)
  for call in source.load_daily_bars.await_args_list:
    assert set(call.args[0]).issubset(CODES)
