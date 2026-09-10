"""Replay one saved upload's read-back using explicit host-local configuration."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure(environment: str, config_root: Path | None = None) -> None:
  if environment == "production" and sys.platform != "win32":
    raise ValueError("PRODUCTION_REQUIRES_WINDOWS")
  if (
    not (Path(sys.prefix) / "conda-meta").is_dir() or Path(sys.prefix).name != "quantx"
  ):
    raise ValueError("QUANTX_CONDA_REQUIRED")
  from runtime_config import load_environment

  values = load_environment((config_root or ROOT).resolve(), environment)
  values.update(
    ENABLE_REAL_TRADING="false",
    QMT_REAL_TRADING_ENABLED="false",
    T_TRADE_LIVE_ENABLED="false",
    REAL_TRADING_ACCOUNT_ALLOWLIST="",
    DATABASE_PROCESS_ROLE="tooling",
  )
  os.environ.update(values)
  for package in (ROOT / "packages").glob("*/src"):
    sys.path.insert(0, str(package))


async def run(request_id, emit):
  from quantx_infrastructure.database.timeseries import (
    init_timeseries,
    shutdown_timeseries,
  )
  from quantx_infrastructure.services.market_data_readback_diagnostic import (
    diagnose_readback,
    read_request_snapshot,
  )
  from sqlalchemy.ext.asyncio import create_async_engine

  engine = create_async_engine(
    os.environ["DATABASE_URL"],
    pool_size=1,
    max_overflow=0,
    connect_args={
      "server_settings": {
        "default_transaction_read_only": "on",
        "statement_timeout": "10000",
        "application_name": "quantx-readback-diagnostic",
      }
    },
  )
  try:
    snapshot = await read_request_snapshot(engine, request_id)
  finally:
    await engine.dispose()
  try:
    connection = init_timeseries()
    if connection is None:
      raise RuntimeError("INFLUX_UNAVAILABLE")
    return await diagnose_readback(
      snapshot, request_id, connection=connection, emit=emit
    )
  finally:
    shutdown_timeseries()


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--environment", required=True, choices=("development", "production")
  )
  parser.add_argument(
    "--request-id", required=True, type=lambda value: str(uuid.UUID(value))
  )
  parser.add_argument("--output", required=True, type=Path)
  parser.add_argument(
    "--config-root",
    type=Path,
    help="Host-local deployment configuration root; code remains in this diagnostic snapshot",
  )
  args = parser.parse_args()
  # Existing libraries may log provider errors. Evidence contains only explicit safe fields.
  logging.disable(logging.CRITICAL)
  try:
    configure(args.environment, args.config_root)
    with args.output.open("x", encoding="utf-8") as evidence:

      def emit(event):
        evidence.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
        evidence.flush()

      emit(
        {
          "event": "start",
          "request_id": args.request_id,
          "environment": args.environment,
          "read_only": True,
        }
      )
      try:
        result = asyncio.run(run(args.request_id, emit))
      except Exception as exc:
        result = {
          "event": "result",
          "status": "failed",
          "reason_code": "DIAGNOSTIC_SETUP_FAILED",
          "exception_type": type(exc).__name__,
        }
        emit(result)
  except Exception as exc:
    print(json.dumps({"status": "failed", "exception_type": type(exc).__name__}))
    return 2
  print(json.dumps(result))
  return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
  raise SystemExit(main())
