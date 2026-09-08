"""Rebuild an expired export from verified persisted market rows."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from quantx_contracts.data_exchange import HistoryPartitionRequest

from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)

TICK_FIELDS = {
  "lastPrice": "last_price",
  "open": "open",
  "high": "high",
  "low": "low",
  "lastClose": "last_close",
  "amount": "amount",
  "volume": "volume",
  "pvolume": "pvolume",
  "tickvol": "tickvol",
  "stockStatus": "stock_status",
  "openInt": "open_int",
  "lastSettlementPrice": "last_settlement_price",
  "settlementPrice": "settlement_price",
  "transactionNum": "transaction_num",
  "askPrice": "ask_price",
  "bidPrice": "bid_price",
  "askVol": "ask_vol",
  "bidVol": "bid_vol",
  "priceTick": "price_tick",
  "upperLimit": "up_stop_price",
  "lowerLimit": "down_stop_price",
  "tick_ordinal": "tick_ordinal",
}
KLINE_FIELDS = {
  "open": "open",
  "high": "high",
  "low": "low",
  "close": "close",
  "preClose": "pre_close",
  "volume": "volume",
  "amount": "amount",
  "settlementPrice": "settelement_price",
  "openInterest": "open_interest",
  "suspendFlag": "suspend_flag",
}


async def persisted_partition(
  request: HistoryPartitionRequest, source_audit: dict
) -> list[dict]:
  coverage = [
    item
    for item in source_audit.get("day_coverage", [])
    if item["instrument_code"] == request.instrument
    and item["period"] == request.period
    and item["trading_date"] == request.trading_date.isoformat()
  ]
  if len(coverage) != 1 or coverage[0]["point_count"] <= 0:
    raise ValueError("PERSISTED_COVERAGE_UNPROVEN")
  start = datetime.combine(
    request.trading_date, datetime.min.time(), ZoneInfo("Asia/Shanghai")
  )
  end = start + timedelta(days=1) - timedelta(microseconds=1)
  service = HistoricalMarketDataService()
  kwargs = dict(
    stock_code=request.instrument,
    start_time=start,
    end_time=end,
    limit=500001,
    order="asc",
  )
  if request.period == "tick":
    rows = await service.get_tick_data(**kwargs)
    fields = TICK_FIELDS
  else:
    rows = await service.get_kline_data(**kwargs, period=request.period)
    fields = KLINE_FIELDS
  if len(rows) != coverage[0]["point_count"] or len(rows) > 500000:
    raise ValueError("PERSISTED_COVERAGE_CHANGED")
  records = []
  for row in rows:
    stamp = (
      getattr(row, "source_time_ms", 0)
      if request.period == "tick"
      else int(row.time.timestamp() * 1000)
    )
    if not stamp:
      raise ValueError("HISTORICAL_SOURCE_IDENTITY_MISSING")
    records.append(
      {
        "code": request.instrument,
        "period": request.period,
        "time": stamp,
        **{wire: getattr(row, field) for wire, field in fields.items()},
      }
    )
  return records
