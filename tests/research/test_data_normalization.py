from datetime import date
from enum import Enum
from types import SimpleNamespace

import pandas as pd
from quantx_research.data import (
  CANONICAL_BAR_COLUMNS,
  normalize_daily_bars,
  normalize_dividend_factors,
  normalize_instruments,
)


class ExampleInstrumentType(str, Enum):
  STOCK = "stock"


def test_daily_bars_are_canonical_and_use_shanghai_trading_date() -> None:
  result = normalize_daily_bars(
    {
      "000001.sz": pd.DataFrame(
        [
          {
            "time": "2024-01-01T16:00:00Z",
            "open": "10",
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": "100",
          }
        ]
      )
    }
  )

  assert tuple(result.columns) == CANONICAL_BAR_COLUMNS
  assert result.loc[0, "stock_code"] == "000001.SZ"
  assert result.loc[0, "time"] == pd.Timestamp("2024-01-02")
  assert result.loc[0, "open"] == 10.0
  assert pd.isna(result.loc[0, "amount"])
  assert pd.isna(result.loc[0, "suspend_flag"])


def test_instrument_entities_preserve_point_in_time_dates() -> None:
  items = [
    SimpleNamespace(
      id="000001.SZ",
      type=ExampleInstrumentType.STOCK,
      name="平安银行",
      market="SZ",
      open_date=date(1991, 4, 3),
      expire_date=None,
    )
  ]

  result = normalize_instruments(items)

  assert result.to_dict(orient="records") == [
    {
      "stock_code": "000001.SZ",
      "instrument_type": "stock",
      "name": "平安银行",
      "market": "SZ",
      "open_date": pd.Timestamp("1991-04-03"),
      "expire_date": pd.NaT,
    }
  ]


def test_real_instrument_enum_is_normalized_by_member_name() -> None:
  from quantx_infrastructure.models.enums import InstrumentType

  result = normalize_instruments(
    [
      SimpleNamespace(
        id="000001.SZ",
        type=InstrumentType.STOCK,
        name="平安银行",
        market="SZ",
        open_date=None,
        expire_date=None,
      )
    ]
  )

  assert result.loc[0, "instrument_type"] == "stock"


def test_dividend_factors_are_numeric_sorted_and_normalized() -> None:
  factors = [
    SimpleNamespace(stock_code="000001.sz", time="2024-01-03", dr="1.2"),
    SimpleNamespace(stock_code="000001.sz", time="2024-01-02", dr="bad"),
  ]

  result = normalize_dividend_factors(factors)

  assert result["stock_code"].tolist() == ["000001.SZ", "000001.SZ"]
  assert result["time"].tolist() == [
    pd.Timestamp("2024-01-02"),
    pd.Timestamp("2024-01-03"),
  ]
  assert pd.isna(result.loc[0, "dr"])
  assert result.loc[1, "dr"] == 1.2
