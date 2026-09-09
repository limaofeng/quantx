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


@pytest.mark.asyncio
async def test_gpu_preparation_uses_downloaded_official_wheel(tmp_path, monkeypatch):
  from quantx_research import next_day_selection_dataset as dataset
  from quantx_research import next_day_selection_gpu as gpu

  monkeypatch.delenv("QUANTX_LIGHTGBM_BUILD_EVIDENCE", raising=False)
  monkeypatch.setattr(preparation, "root", lambda: tmp_path)
  wheel = tmp_path / ".runtime/research-gpu/official-wheel/lightgbm-4.6.0-py3-none-win_amd64.whl"
  wheel.parent.mkdir(parents=True)
  wheel.touch()
  monkeypatch.setattr(dataset, "resolve_dataset_directory", lambda version: tmp_path / version)

  def qualify(directory, *, build_evidence, output):
    assert directory == tmp_path / "certified"
    assert build_evidence == wheel
    assert output == tmp_path / "qualification.json"
    raise RuntimeError("qualification invoked")

  monkeypatch.setattr(gpu, "qualify_lightgbm_gpu", qualify)
  request = {
    "kind": "GPU", "dataset_version": "certified",
    "build_evidence": str(wheel), "dataset_directory": str(tmp_path / "certified"),
    "qualification_output": str(tmp_path / "qualification.json"),
    "config": {"date_start": "2025-01-02", "date_end": "2025-01-10"},
  }
  with pytest.raises(RuntimeError, match="qualification invoked"):
    await preparation.execute(request, tmp_path)
  wheel.unlink()
  result = await preparation.execute(request, tmp_path)
  assert result["ready"] is False
  assert "官方 GPU wheel" in result["error"]


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


@pytest.mark.asyncio
async def test_certify_exports_and_reuses_frozen_inputs_without_computing(tmp_path, monkeypatch):
  from contextlib import asynccontextmanager
  from unittest.mock import AsyncMock

  from quantx_research import next_day_selection_dataset
  from quantx_research.certification_inputs import load_certification_inputs

  from tests.research.test_frozen_source import CODES, END, START, inputs

  source, calendar, _ = inputs()
  calendar.get_next_trading_date = AsyncMock(return_value=END)
  request_config = {"date_start": START.isoformat(), "date_end": "2026-01-06", "stock_codes": [CODES[0]],
                    "st_file": "st.csv", "industry_file": "industry.csv", "delisting_file": "delisting.csv"}
  hashes = {}
  for key in ["st_file", "industry_file", "delisting_file"]:
    path = tmp_path / request_config[key]
    path.write_text("event_date,stock_code,value\n2026-01-05,600000.SH,0\n")
    hashes[key] = preparation.file_hash(path)
  monkeypatch.setenv("QUANTX_RESEARCH_EVIDENCE_ROOT", str(tmp_path))
  monkeypatch.setattr(preparation, "require_development_export", lambda: None)
  monkeypatch.setattr(preparation, "coverage", AsyncMock(return_value={"ready": True, "stock_codes": [CODES[0]], "file_hashes": hashes, "checks": []}))

  @asynccontextmanager
  async def opened():
    yield source

  def forbidden(*args, **kwargs):
    pytest.fail("Worker export must not perform certification or reopen inputs on retry")

  monkeypatch.setattr(preparation, "InfrastructureResearchDataSource", opened)
  monkeypatch.setattr(preparation, "TradingDateHelper", lambda: calendar)
  monkeypatch.setattr(next_day_selection_dataset, "certify_next_day_selection_dataset", forbidden)
  attempt = tmp_path / "attempt"
  attempt.mkdir()
  request = {"kind": "CERTIFY", "config": request_config, "dataset_version": "export-v1"}
  result = await preparation.execute(request, attempt)
  assert result["ready"] is True and "sample_count" not in result
  reference = result["certification_input"]
  load_certification_inputs(attempt / "certification-inputs", dataset_version="export-v1", manifest_sha256=reference["manifest_sha256"])
  monkeypatch.setattr(preparation, "coverage", forbidden)
  monkeypatch.setattr(preparation, "InfrastructureResearchDataSource", forbidden)
  retried = await preparation.execute(request, attempt)
  assert retried["certification_input"] == reference
