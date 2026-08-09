from __future__ import annotations

import pandas as pd
import pytest
from quantx_qmt_agent.broker import _divid_factor_records


class FakeManager:
  def __init__(self, frames):
    self.frames = frames
    self.calls = []

  def get_divid_factors(self, code, start_time, end_time):
    self.calls.append((code, start_time, end_time))
    return self.frames.get(code, pd.DataFrame())


def test_divid_factor_operation_normalizes_sparse_qmt_frames():
  manager = FakeManager(
    {
      "600519.SH": pd.DataFrame(
        [
          {
            "time": 1_592_928_000_000,
            "interest": 17.025,
            "stockBonus": 0,
            "stockGift": 0,
            "allotNum": 0,
            "allotPrice": 0,
            "gugai": 0,
            "dr": 1.011677,
          }
        ],
        index=["20200624"],
      )
    }
  )

  records = _divid_factor_records(
    manager,
    {
      "stock_list": ["600519.SH", "000001.SZ", "600519.SH"],
      "start_time": "20200313",
      "end_time": "20260729",
    },
  )

  assert manager.calls == [
    ("000001.SZ", "20200313", "20260729"),
    ("600519.SH", "20200313", "20260729"),
  ]
  assert records == [
    {
      "code": "600519.SH",
      "ex_date": "20200624",
      "time": 1_592_928_000_000.0,
      "interest": 17.025,
      "stockBonus": 0.0,
      "stockGift": 0.0,
      "allotNum": 0.0,
      "allotPrice": 0.0,
      "gugai": 0.0,
      "dr": 1.011677,
    }
  ]


def test_divid_factor_operation_rejects_non_finite_values():
  manager = FakeManager(
    {
      "600519.SH": pd.DataFrame(
        [
          {
            "time": 1_592_928_000_000,
            "interest": float("nan"),
            "stockBonus": 0,
            "stockGift": 0,
            "allotNum": 0,
            "allotPrice": 0,
            "gugai": 0,
            "dr": 1.01,
          }
        ],
        index=["20200624"],
      )
    }
  )

  with pytest.raises(ValueError, match="non-finite"):
    _divid_factor_records(
      manager,
      {
        "stock_list": ["600519.SH"],
        "start_time": "20200313",
        "end_time": "20260729",
      },
    )


def test_divid_factor_operation_rejects_out_of_range_events():
  manager = FakeManager(
    {
      "600519.SH": pd.DataFrame(
        [
          {
            "time": 1_592_928_000_000,
            "interest": 1,
            "stockBonus": 0,
            "stockGift": 0,
            "allotNum": 0,
            "allotPrice": 0,
            "gugai": 0,
            "dr": 1.01,
          }
        ],
        index=["20200624"],
      )
    }
  )

  with pytest.raises(ValueError, match="outside request range"):
    _divid_factor_records(
      manager,
      {
        "stock_list": ["600519.SH"],
        "start_time": "20210101",
        "end_time": "20260729",
      },
    )
