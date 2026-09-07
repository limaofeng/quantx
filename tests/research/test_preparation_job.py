from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from quantx_contracts.research_preparation import ResearchPreparationConfig
from quantx_research import preparation_job as preparation


class Calendar:
  async def get_next_trading_date(self, market, target):
    return (pd.Timestamp(target) + pd.offsets.BDay(1)).date()

  async def get_trading_calendar(self, market, start, end):
    return list(pd.bdate_range(start, end).date)


class Source:
  def __init__(self, missing=False):
    self.missing = missing

  async def list_instruments(self, codes=None):
    return pd.DataFrame({"stock_code": ["600000.SH"], "open_date": ["2000-01-01"]})

  async def load_daily_bars(self, codes, start, end, **kwargs):
    dates = pd.bdate_range(start, end)
    if self.missing and codes == ["600000.SH"]:
      dates = dates[:-1]
    return pd.DataFrame(
      [(code, day) for code in codes for day in dates], columns=["stock_code", "time"]
    )

  async def load_dividend_factor_coverage(self, codes, **kwargs):
    return pd.DataFrame()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_complete_and_missing_coverage_with_real_history_files(
  tmp_path, monkeypatch, missing
):
  monkeypatch.setenv("QUANTX_RESEARCH_EVIDENCE_ROOT", str(tmp_path))
  monkeypatch.setattr(
    preparation,
    "build_dividend_factor_coverage_report",
    lambda *a, **k: SimpleNamespace(is_complete=True, covered_codes=["600000.SH"]),
  )
  payload = {
    "date_start": "2025-01-02",
    "date_end": "2025-01-10",
    "stock_codes": ["600000.SH"],
  }
  for key, column, _ in preparation.HISTORIES:
    frame = pd.DataFrame(
      {
        "event_date": pd.bdate_range("2025-01-02", "2025-01-10"),
        "stock_code": "600000.SH",
        column: "银行" if column == "industry" else False,
      }
    )
    filename = key + ".csv"
    frame.to_csv(tmp_path / filename, index=False)
    payload[key] = filename
  result = await preparation.coverage(
    ResearchPreparationConfig.model_validate(payload),
    source=Source(missing),
    calendar=Calendar(),
  )
  assert result["ready"] is (not missing)
  assert result["preview"]["end"] == "2025-01-13"
  assert result["download"]["periods"] == ["1d"]
  assert result["download"]["stock_list"] == ["000300.SH", "600000.SH"]
  assert all(len(value) == 64 for value in result["file_hashes"].values())


@pytest.mark.asyncio
async def test_missing_histories_are_actionable_and_never_certified(monkeypatch):
  monkeypatch.setattr(
    preparation,
    "build_dividend_factor_coverage_report",
    lambda *a, **k: SimpleNamespace(is_complete=False, covered_codes=[]),
  )
  config = ResearchPreparationConfig(
    date_start=date(2025, 1, 2), date_end=date(2025, 1, 10)
  )
  report = await preparation.coverage(config, source=Source(), calendar=Calendar())
  assert report["ready"] is False
  assert {item["name"] for item in report["checks"] if item["status"] != "READY"} >= {
    "历史 ST",
    "历史行业",
    "历史退市",
    "历史股票池",
    "指标复权依赖",
  }
