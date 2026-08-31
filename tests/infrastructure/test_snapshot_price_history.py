from datetime import date, datetime

import pandas as pd
import pytest
from quantx_infrastructure.services.snapshot_price_history import (
  adjust_price_frame,
  covered_codes,
)


def test_corporate_action_adjustment_is_prefix_invariant_and_keeps_raw_price():
  raw = pd.DataFrame(
    {
      "time": pd.date_range("2025-01-01", periods=4),
      "open": [10, 5, 5, 2.5],
      "close": [10, 5, 5, 2.5],
      "high": [10, 5, 5, 2.5],
      "low": [10, 5, 5, 2.5],
    }
  )
  factors = [(datetime(2025, 1, 2), 2), (datetime(2025, 1, 4), 2)]
  adjusted = adjust_price_frame(raw, factors)
  assert adjusted.close.tolist() == [10, 10, 10, 10]
  assert adjusted.raw_close.tolist() == raw.close.tolist()
  pd.testing.assert_frame_equal(
    adjusted.iloc[:3], adjust_price_frame(raw.iloc[:3], factors)
  )
  with pytest.raises(ValueError):
    adjust_price_frame(raw, [(datetime(2025, 1, 2), 0)])


def test_sparse_factor_coverage_requires_complete_durable_evidence():
  def request(first, last, received=1):
    return (
      {
        "operation": "divid_factors",
        "source": "qmt-get-divid-factors-v1",
        "stock_list": ["000001.SZ"],
        "start_time": first,
        "end_time": last,
      },
      1,
      received,
      datetime(2025, 2, 1),
    )

  bounds = (["000001.SZ"], date(2025, 1, 1), date(2025, 1, 31))
  assert covered_codes([], *bounds) == set()
  assert covered_codes([request("20250101", "20250131", 0)], *bounds) == set()
  assert covered_codes(
    [request("20250101", "20250115"), request("20250116", "20250131")], *bounds
  ) == {"000001.SZ"}
  assert (
    covered_codes(
      [request("20250101", "20250115"), request("20250117", "20250131")], *bounds
    )
    == set()
  )
