from datetime import datetime

import pandas as pd
from quantx_infrastructure.database.timeseries_base import ListAttributeConverter
from quantx_infrastructure.repositories.tick_repository import TickRepository


def test_list_converter_reads_legacy_json_string_columns():
  converter = ListAttributeConverter(prefix="ask", max_levels=5)
  records = pd.DataFrame(
    {"ask_price": ["[10.1, 10.2, 10.3, 10.4, 10.5]"]}
  )

  converted = converter.convert_to_entity_attribute(records, "ask_price")

  assert converted.loc[0, "ask_price"] == [10.1, 10.2, 10.3, 10.4, 10.5]


def test_list_converter_prefers_expanded_columns_over_legacy_value():
  converter = ListAttributeConverter(prefix="ask", max_levels=5)
  records = pd.DataFrame(
    {
      "ask_price": ["[9.1, 9.2, 9.3, 9.4, 9.5]"],
      "ask1": [10.1],
      "ask2": [10.2],
      "ask3": [10.3],
      "ask4": [10.4],
      "ask5": [10.5],
    }
  )

  converted = converter.convert_to_entity_attribute(records, "ask_price")

  assert converted.loc[0, "ask_price"] == [10.1, 10.2, 10.3, 10.4, 10.5]
  assert "ask1" not in converted.columns


def test_tick_bulk_save_expands_order_book_arrays_before_writing():
  class Operations:
    def write_records(self, **kwargs):
      self.records = kwargs["records"].copy()

  repository = TickRepository()
  repository.operations = Operations()
  records = pd.DataFrame(
    [
      {
        "stock_code": "600000.SH",
        "period": "tick",
        "time": datetime(2026, 8, 12, 9, 30),
        "last_price": 10.0,
        "ask_price": [10.01, 10.02, 10.03, 10.04, 10.05],
        "bid_price": [9.99, 9.98, 9.97, 9.96, 9.95],
        "ask_vol": [1, 2, 3, 4, 5],
        "bid_vol": [5, 4, 3, 2, 1],
      }
    ]
  )

  assert repository.bulk_save(records) == 1

  written = repository.operations.records
  assert "ask_price" not in written.columns
  assert written.loc[0, "ask1"] == 10.01
  assert written.loc[0, "bid5"] == 9.95
  assert written.loc[0, "ask_vol5"] == 5
  assert written.loc[0, "bid_vol1"] == 5
