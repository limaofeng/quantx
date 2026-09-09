"""Synthetic market inputs exercise the real offline certification computation."""

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest
import yaml
from quantx_research.certification_inputs import (
  certify_frozen_inputs,
  export_certification_inputs,
)
from quantx_research.next_day_selection_config import load_next_day_selection_config
from quantx_research.next_day_selection_dataset import load_certified_dataset_manifest

from tests.research.test_factor_coverage import evidence_row


@pytest.mark.asyncio
async def test_real_certification_uses_only_relocated_frozen_inputs(tmp_path):
  dates = pd.bdate_range("2023-01-02", periods=600)
  codes = ["600000.SH", "600001.SH", "000300.SH"]
  start, end = dates[-61].date(), dates[-2].date()
  bars = []
  for offset, code in enumerate(codes):
    t = np.arange(len(dates))
    close = 20 + offset + t * 0.01 + np.sin(t / 7 + offset)
    bars.append(
      pd.DataFrame(
        {
          "stock_code": code,
          "time": dates,
          "open": close * np.where(t % 2 == 0, 0.997, 1.003),
          "high": close * 1.02,
          "low": close * 0.98,
          "close": close,
          "volume": 100000 + (t % 17) * 1000,
          "amount": close * (100000 + (t % 17) * 1000),
          "suspend_flag": 0,
        }
      )
    )
  bars = pd.concat(bars, ignore_index=True)

  async def load(codes, start, end, **kwargs):
    return bars.loc[
      bars.stock_code.isin(codes)
      & bars.time.between(pd.Timestamp(start), pd.Timestamp(end))
    ].copy()

  source = SimpleNamespace(
    list_instruments=AsyncMock(
      return_value=pd.DataFrame(
        {
          "stock_code": codes,
          "instrument_type": ["stock", "stock", "index"],
          "open_date": pd.Timestamp("2020-01-01"),
        }
      )
    ),
    load_daily_bars=AsyncMock(side_effect=load),
    load_dividend_factors=AsyncMock(
      return_value=pd.DataFrame(columns=["stock_code", "time", "dr"])
    ),
    load_dividend_factor_coverage=AsyncMock(
      return_value=pd.DataFrame(
        [
          evidence_row(
            request_id=code,
            stock_code=code,
            start_date="20230102",
            end_date=dates[-1].strftime("%Y%m%d"),
            completed_at=dates[-1].to_pydatetime(),
          )
          for code in codes
        ],
        dtype=object,
      )
    ),
  )

  async def sessions(market, start, end):
    return list(dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))])

  calendar = SimpleNamespace(
    get_trading_calendar=AsyncMock(side_effect=sessions),
    get_next_trading_date=AsyncMock(return_value=dates[-1].date()),
  )
  origin = tmp_path / "origin"
  origin.mkdir()
  payload = load_next_day_selection_config(
    Path(__file__).resolve().parents[2]
    / "apps/research/configs/next_day_selection_v1.yaml"
  ).model_dump(mode="json")
  payload["data"].update(
    date_range=[start.isoformat(), end.isoformat()],
    stock_codes=codes[:2],
    universe_kind="EXPLICIT",
  )
  payload["runtime"].update(batch_size=1, minimum_available_memory_gib=1)
  for key, column, value in [
    ("historical_st_membership_path", "is_st", False),
    ("historical_industry_membership_path", "industry", "bank"),
    ("historical_delisting_status_path", "delisting_risk", False),
  ]:
    path = origin / f"{column}.csv"
    pd.DataFrame(
      [
        {"event_date": date, "stock_code": code, column: value}
        for date in dates[-61:]
        for code in codes[:2]
      ]
    ).to_csv(path, index=False)
    payload["data"][key] = str(path)
  config_path = origin / "config.yaml"
  config_path.write_text(yaml.safe_dump(payload))
  exported = tmp_path / "exported"
  digest = await export_certification_inputs(
    config_path, source, calendar, exported, dataset_version="synthetic-v1"
  )
  frozen = tmp_path / "trainer-inputs"
  shutil.move(exported, frozen)
  shutil.rmtree(origin)
  for client in (source, calendar):
    for method in vars(client).values():
      method.side_effect = AssertionError("online source reused after export")

  output = await certify_frozen_inputs(
    frozen,
    dataset_version="synthetic-v1",
    manifest_sha256=digest,
    work_directory=tmp_path / "work",
    output_root=tmp_path / "datasets",
  )
  manifest = load_certified_dataset_manifest(output)
  quality = manifest["quality"]
  assert quality["sample_count"] == 120
  assert quality["stock_count"] == 2
  assert quality["trading_day_count"] == 60
  assert all(quality["leakage_checks"].values())
  assert quality["coverage"]["historical_universe"]["complete"] is True
  provenance = quality["coverage"]["source"]["source_provenance"]
  assert provenance["input_manifest_sha256"] == digest
  panel = pd.read_parquet(output / manifest["panel_path"])
  assert panel.event_date.max().date() == end
  assert panel.target_date.max().date() == dates[-1].date()
  assert set(panel.stock_code) == set(codes[:2])
  expected = panel.merge(
    bars[["stock_code", "time", "open", "close"]].rename(
      columns={"open": "expected_open", "close": "expected_close"}
    ),
    left_on=["stock_code", "target_date"],
    right_on=["stock_code", "time"],
    validate="many_to_one",
  )
  assert len(expected) == len(panel)
  np.testing.assert_allclose(expected.next_open, expected.expected_open)
  np.testing.assert_allclose(expected.next_close, expected.expected_close)
  np.testing.assert_array_equal(
    expected.label, (expected.expected_close > expected.expected_open).astype(float)
  )
  assert set(panel.label) == {0.0, 1.0}
  assert list((tmp_path / "work").iterdir()) == []
