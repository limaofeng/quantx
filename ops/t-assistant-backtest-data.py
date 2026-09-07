"""Read persisted Tick cache into an isolated P5 dataset; never call miniQMT."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
  str(ROOT / path)
  for path in (
    "apps/engine/src",
    "packages/application/src",
    "packages/domain/src",
    "packages/contracts/src",
    "packages/infrastructure/src",
  )
]


async def acquire(args):
  # Process-local controls only. No service configuration is modified.
  os.environ["ENV"] = "testing"
  os.environ["DEBUG"] = "false"
  os.environ["INFLUXDB_MAX_RETRIES"] = "0"
  logging.disable(logging.CRITICAL)
  from quantx_engine.t_assistant_backtest_data import acquire_backtest_dataset
  from quantx_infrastructure.services.historical_market_data_service import (
    HistoricalMarketDataService,
  )
  from quantx_infrastructure.services.trading_time_service import TradingDateHelper

  codes = tuple(code.strip().upper() for code in args.instruments.split(","))
  calendar = TradingDateHelper()
  start, end = args.start, args.end
  if args.probe:
    days = await calendar.get_trading_calendar(
      market="SH", start_date=start, end_date=end
    )
    if not days:
      raise ValueError("BACKTEST_CALENDAR_EMPTY")
    start = end = days[0]
    codes = codes[:1]
  if args.supplement:
    if len(codes) != 1 or start != end:
      raise ValueError("BACKTEST_SINGLE_PARTITION_PROBE_REQUIRED")
    from datetime import datetime, time

    from quantx_domain.clock import SHANGHAI
    from quantx_infrastructure.runtime_store import DurableRuntimeStore
    from quantx_infrastructure.services.market_data_request_service import (
      request_agent_market_data,
    )

    pages = HistoricalMarketDataService().iter_tick_pages(
      stock_code=codes[0],
      start_time=datetime.combine(start, time.min, SHANGHAI),
      end_time=datetime.combine(end, time.max, SHANGHAI),
      page_size=1,
    )
    try:
      cached = await anext(pages, [])
    finally:
      await pages.aclose()
    if not cached:
      store = DurableRuntimeStore()
      try:
        available = bool(await store.available_market_data_device())
      finally:
        await store.close()
      if not available:
        print(
          json.dumps(
            {"event": "SUPPLEMENT_SKIPPED", "reason": "market_data_agent_unavailable"}
          ),
          flush=True,
        )
        return 2
      print(
        json.dumps(
          {
            "event": "SUPPLEMENT_START",
            "code": codes[0],
            "day": str(start),
            "retries": 0,
          }
        ),
        flush=True,
      )
      result = await request_agent_market_data(
        payload={
          "operation": "bars",
          "download": True,
          "stock_list": list(codes),
          "start_time": start.strftime("%Y%m%d"),
          "end_time": end.strftime("%Y%m%d"),
          "periods": ["tick"],
        },
        timeout_seconds=args.wait_seconds,
        idempotency_scope="p5-tick-cache-probe-v1",
        retry_failed_requests=False,
      )
      print(
        json.dumps(
          {
            "event": "SUPPLEMENT_RESULT",
            "status": result.get("status"),
            "request_id": result.get("request_id"),
            "point_count": result.get("point_count"),
            "reason": result.get("reason"),
          },
          ensure_ascii=True,
        ),
        flush=True,
      )
      if result.get("status") != "success":
        return 2
  print(
    json.dumps(
      {
        "event": "START",
        "instruments": codes,
        "start": str(start),
        "end": str(end),
        "source": "persisted-tick-cache",
        "retries": 0,
      }
    ),
    flush=True,
  )
  dataset = await acquire_backtest_dataset(
    history=HistoricalMarketDataService(),
    calendar=calendar,
    source_version="quantx-persisted-tick-cache",
    instruments=codes,
    start=start,
    end=end,
    root=args.output,
    latency_ms=0,
    stop_on_error=True,
    preserve_raw=True,
    on_partition=lambda part: print(
      json.dumps({"event": "PARTITION", **part}), flush=True
    ),
  )
  material = dataset.manifest["material"]
  print(
    json.dumps(
      {
        "event": "RESULT",
        "directory": str(dataset.directory.resolve()),
        "status": material["status"],
        "hash": dataset.manifest["hash"],
        "points": sum(p["count"] for p in material["parts"]),
        "failures": material["failures"],
        "unattempted_partitions": material["unattempted_partitions"],
      }
    ),
    flush=True,
  )
  return 0 if material["status"] in {"FROZEN", "REFERENCE_REQUIRED"} else 2


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--instruments", required=True, help="Comma-separated instrument codes"
  )
  parser.add_argument("--start", type=date.fromisoformat, required=True)
  parser.add_argument("--end", type=date.fromisoformat, required=True)
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument(
    "--probe", action="store_true", help="Read only first symbol and first trading day"
  )
  parser.add_argument(
    "--supplement",
    action="store_true",
    help="One missing symbol/day through the durable data gateway, without failure retries",
  )
  parser.add_argument(
    "--wait-seconds",
    type=int,
    default=60,
    help="Wait on the same idempotent request, at most 600 seconds",
  )
  args = parser.parse_args()
  if not 1 <= args.wait_seconds <= 600:
    parser.error("--wait-seconds must be between 1 and 600")
  try:
    return asyncio.run(acquire(args))
  except Exception as exc:
    print(json.dumps({"event": "FAILED", "error_type": type(exc).__name__}), flush=True)
    return 2


if __name__ == "__main__":
  raise SystemExit(main())
