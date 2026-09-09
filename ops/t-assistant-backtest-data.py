"""Inspect persisted Tick data; save references by default, snapshots on request."""

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


def configure_environment(environment: str):
  from runtime_config import LIVE_KEYS, load_environment

  if environment not in {"development", "testing"}:
    raise ValueError("BACKTEST_DATA_ENVIRONMENT_INVALID")
  if sys.platform == "darwin" and environment != "development":
    raise ValueError("BACKTEST_DATA_MACOS_REQUIRES_DEVELOPMENT")
  # Validate explicit endpoints before importing clients or opening connections.
  configured = load_environment(ROOT, environment)
  configured.update(dict.fromkeys(LIVE_KEYS, "false"))
  configured["REAL_TRADING_ACCOUNT_ALLOWLIST"] = "[]"
  configured["QMT_ACCOUNT_WHITELIST"] = ""
  os.environ.update(configured)
  os.environ["DEBUG"] = "false"
  os.environ["INFLUXDB_MAX_RETRIES"] = "0"


async def supplement_partition(code, day, wait_seconds):
  """Submit once to local demand storage; only wait, never retry execution."""
  from quantx_contracts.market_data_service import HistoryDemand
  from quantx_infrastructure.services.local_market_data_client import (
    LocalMarketDataClient,
  )

  demand = HistoryDemand(instrument=code, period="tick", trading_date=day)
  client = LocalMarketDataClient()
  try:
    identity = await client.submit_history_demand(demand)
    print(
      json.dumps({"event": "SUPPLEMENT_ACCEPTED", "demand_id": identity}), flush=True
    )
    result = {"status": "pending", "demand_id": identity}
    try:
      async with asyncio.timeout(wait_seconds):
        while True:
          state = await client.history_demand(identity, expected_partition=demand)
          if state is None:
            raise ValueError("BACKTEST_HISTORY_DEMAND_MISSING")
          result.update(
            source_request_id=state.source_request_id,
            delivery_id=state.delivery_id,
            source_status=state.source_status,
            source_phase=state.source_phase,
            delivery_status=state.delivery_status,
            demand_reason=state.reason_code,
            reason=state.reason_code,
          )
          if (
            state.source_kind == "AGENT"
            and state.source_request_id is not None
            and state.source_status == "COMPLETED"
            and state.source_phase == "VERIFIED"
          ) or (
            state.source_kind == "REMOTE"
            and state.delivery_id is not None
            and state.delivery_status == "LOCAL_VERIFIED"
          ):
            return {**result, "status": "success"}
          terminal = {"FAILED", "CANCELLED", "INCOMPLETE"}
          if state.source_status in terminal or state.delivery_status in terminal:
            return {**result, "status": "failed"}
          await asyncio.sleep(2)
    except TimeoutError:
      return {**result, "reason": "HISTORY_WAIT_TIMEOUT"}
  finally:
    await client.close()


async def acquire(args):
  # Process-local controls only. No service configuration is modified.
  configure_environment(args.environment)
  logging.disable(logging.CRITICAL)

  from quantx_engine.t_assistant_backtest_data import acquire_backtest_dataset
  from quantx_infrastructure.config.settings import settings
  from quantx_infrastructure.services.local_historical_tick_reader import (
    LocalHistoricalTickReader,
  )
  from quantx_infrastructure.services.trading_time_service import TradingDateHelper

  # This is the requested local interface identity, not an attestation of the
  # service's physical database version. Dataset parts pin the returned bytes.
  source_version = f"local-market-data-api:{settings.environment}:history-v1"
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

    pages = LocalHistoricalTickReader().iter_tick_pages(
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
      result = await supplement_partition(codes[0], start, args.wait_seconds)
      print(json.dumps({"event": "SUPPLEMENT_RESULT", **result}), flush=True)
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
    history=LocalHistoricalTickReader(),
    calendar=calendar,
    source_version=source_version,
    instruments=codes,
    start=start,
    end=end,
    root=args.output,
    latency_ms=0,
    stop_on_error=True,
    preserve_raw=True,
    freeze=args.freeze,
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
    "--environment",
    choices=("development", "testing"),
    default="development",
    help="Explicit data environment; macOS only permits local development services",
  )
  parser.add_argument(
    "--instruments", required=True, help="Comma-separated instrument codes"
  )
  parser.add_argument("--start", type=date.fromisoformat, required=True)
  parser.add_argument("--end", type=date.fromisoformat, required=True)
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument(
    "--freeze",
    action="store_true",
    help="Explicit shared snapshot; default stores only a verified local history reference",
  )
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
    help="Wait on the same durable demand, at most 600 seconds",
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
