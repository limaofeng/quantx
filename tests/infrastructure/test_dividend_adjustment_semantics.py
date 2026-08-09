from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)
from quantx_infrastructure.services.historical_market_data_service_async import (
  HistoricalMarketDataServiceAsync,
)


class FakeFactorService:
  def __init__(self, factors):
    self.factors = factors

  async def get_divid_factors(self, **kwargs):
    del kwargs
    return self.factors


def _klines():
  values = [
    (datetime(2024, 1, 1), 100.0, 100.0),
    (datetime(2024, 1, 2), 50.0, 50.0),
    (datetime(2024, 1, 3), 55.0, 50.0),
  ]
  return [
    KLine(
      stock_code="000001.SZ",
      period="1d",
      time=value_time,
      open=close,
      high=close,
      low=close,
      close=close,
      pre_close=pre_close,
      volume=100,
      amount=close * 100,
      settelement_price=0,
      open_interest=0,
      suspend_flag=0,
    )
    for value_time, close, pre_close in values
  ]


def _service(service_class, factors):
  service = object.__new__(service_class)
  service.logger = logging.getLogger(__name__)
  factor_service = FakeFactorService(factors)
  if service_class is HistoricalMarketDataService:
    service.divid_factor_service_async = factor_service
  else:
    service.divid_factor_service = factor_service
  return service


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "service_class,method_name",
  [
    (HistoricalMarketDataService, "_apply_dividend_adjustment_async"),
    (HistoricalMarketDataServiceAsync, "_apply_dividend_adjustment"),
  ],
)
async def test_infrastructure_front_and_back_follow_qmt_dr_semantics(
  service_class,
  method_name,
):
  factors = [SimpleNamespace(time=datetime(2024, 1, 2), dr=2.0)]
  service = _service(service_class, factors)
  method = getattr(service, method_name)

  front = await method(_klines(), "000001.SZ", "front")
  back = await method(_klines(), "000001.SZ", "back")

  assert [item.close for item in front] == [50.0, 50.0, 55.0]
  assert [item.pre_close for item in front] == [50.0, 50.0, 50.0]
  assert [item.close for item in back] == [100.0, 100.0, 110.0]
  assert [item.pre_close for item in back] == [100.0, 100.0, 100.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "service_class,method_name",
  [
    (HistoricalMarketDataService, "_apply_dividend_adjustment_async"),
    (HistoricalMarketDataServiceAsync, "_apply_dividend_adjustment"),
  ],
)
async def test_front_adjustment_ignores_factors_after_requested_end(
  service_class,
  method_name,
):
  known = [SimpleNamespace(time=datetime(2024, 1, 2), dr=2.0)]
  future = [
    *known,
    SimpleNamespace(time=datetime(2024, 2, 1), dr=10.0),
  ]
  baseline_service = _service(service_class, known)
  future_service = _service(service_class, future)

  baseline = await getattr(baseline_service, method_name)(
    _klines(),
    "000001.SZ",
    "front",
  )
  with_future = await getattr(future_service, method_name)(
    _klines(),
    "000001.SZ",
    "front",
  )

  assert [item.close for item in with_future] == [
    item.close for item in baseline
  ]
