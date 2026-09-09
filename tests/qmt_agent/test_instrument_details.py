"""Validate the original scope and native result before publishing unit rows."""

from unittest.mock import Mock

import pytest
from quantx_qmt_agent.broker import _market_data_records, validate_market_data_request


@pytest.mark.parametrize(
  "codes", [[], ["bad"], ["000001.SZ"] * 2, [f"{i:06}.SZ" for i in range(10001)]]
)
def test_original_request_rejects_invalid_scope(codes):
  with pytest.raises(ValueError):
    validate_market_data_request(
      {"operation": "instrument_details", "stock_list": codes}
    )


@pytest.mark.parametrize(
  "fields",
  [
    {"code": "600000.SH", "Name": "wrong"},
    {"Name": "x" * 65536},
    {"Price": float("nan")},
  ],
)
def test_native_source_rejects_invalid_record(fields):
  manager = Mock()
  manager.get_instrument_detail_list.return_value = {"000001.SZ": fields}
  with pytest.raises(ValueError):
    _market_data_records(
      manager, {"operation": "instrument_details", "stock_list": ["000001.SZ"]}
    )


def test_native_source_preserves_complete_fields():
  manager = Mock()
  manager.get_instrument_detail_list.return_value = {
    "000001.SZ": {"InstrumentName": "平安银行", "PriceTick": 0.01}
  }
  assert _market_data_records(
    manager, {"operation": "instrument_details", "stock_list": ["000001.SZ"]}
  ) == [{"code": "000001.SZ", "InstrumentName": "平安银行", "PriceTick": 0.01}]
  manager.get_instrument_detail_list.assert_called_once_with(
    ["000001.SZ"], iscomplete=True
  )


@pytest.mark.parametrize(
  "result", [{}, {"000001.SZ": {}}, {"000001.SZ": {"code": "000001.SZ"}}]
)
def test_native_missing_data_has_a_distinct_terminal_reason(result):
  from quantx_qmt_agent.market_data_errors import HistoricalDataUnavailableError
  from quantx_qmt_agent.runtime import _is_deterministic_market_data_request_error

  manager = Mock()
  manager.get_instrument_detail_list.return_value = result
  with pytest.raises(HistoricalDataUnavailableError) as caught:
    _market_data_records(
      manager, {"operation": "instrument_details", "stock_list": ["000001.SZ"]}
    )
  assert _is_deterministic_market_data_request_error(caught.value)


@pytest.mark.parametrize("result", [None, {"600000.SH": {"Name": "unexpected"}}])
def test_invalid_provider_scope_is_not_reported_as_missing_data(result):
  manager = Mock()
  manager.get_instrument_detail_list.return_value = result
  with pytest.raises(ValueError):
    _market_data_records(
      manager, {"operation": "instrument_details", "stock_list": ["000001.SZ"]}
    )
