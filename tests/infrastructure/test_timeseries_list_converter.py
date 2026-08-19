from dataclasses import fields
from datetime import datetime, timedelta

import pandas as pd
from quantx_infrastructure.database.timeseries_base import ListAttributeConverter
from quantx_infrastructure.models.tick import Tick
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


def test_tick_bulk_save_keeps_reversible_same_millisecond_identity_as_fields():
  class Operations:
    def write_records(self, **kwargs):
      self.kwargs = kwargs

  repository = TickRepository()
  repository.operations = Operations()
  model_fields = {model_field.name for model_field in fields(Tick)}
  assert {"source_time_ms", "tick_ordinal"} <= model_fields
  assert Tick().get_tag_columns() == ["stock_code", "period"]

  source_time_ms = 1786671000123
  effective_time = datetime(2026, 8, 14, 9, 30, 0, 123000)
  records = pd.DataFrame(
    [
      {
        "stock_code": "601318.SH",
        "period": "tick",
        "time": effective_time,
        "last_price": 50.0,
        "source_time_ms": source_time_ms,
        "tick_ordinal": 0,
      },
      {
        "stock_code": "601318.SH",
        "period": "tick",
        "time": effective_time + timedelta(microseconds=1),
        "last_price": 50.01,
        "source_time_ms": source_time_ms,
        "tick_ordinal": 1,
      },
    ]
  )

  assert repository.bulk_save(records) == 2

  written = repository.operations.kwargs["records"]
  assert written["time"].is_unique
  assert written.loc[1, "time"] - written.loc[0, "time"] == timedelta(
    microseconds=1
  )
  assert written["source_time_ms"].tolist() == [source_time_ms, source_time_ms]
  assert written["tick_ordinal"].tolist() == [0, 1]
  assert repository.operations.kwargs["tag_columns"] == [
    "stock_code",
    "period",
  ]
