"""Explicit local receipt adoption and recovery; producer checks are authenticated GETs."""

import argparse
import asyncio
import json
import logging
import os
import re
from pathlib import Path

from diagnose_market_data_readback import configure


def identity(value):
  if re.fullmatch(r"[0-9a-f]{64}", value) is None:
    raise argparse.ArgumentTypeError(
      "delivery ID must be 64 lowercase hexadecimal characters"
    )
  return value


def read_plan(path):
  with path.open("rb") as stream:
    raw = stream.read(4 * 1024 * 1024 + 1)
  if len(raw) > 4 * 1024 * 1024:
    raise ValueError("LEGACY_IMPORT_INPUT_CAPACITY")
  return json.loads(raw)


async def producer(identity):
  import httpx
  from quantx_infrastructure.services.development_delivery_manifest import (
    read_delivery_metadata,
  )

  async with (
    asyncio.timeout(30),
    httpx.AsyncClient(
      base_url=os.environ["QUANTX_MARKET_DATA_URL"].rstrip("/"),
      headers={"Authorization": f"Bearer {os.environ['QUANTX_MARKET_DATA_TOKEN']}"},
      timeout=30,
      trust_env=False,
      follow_redirects=False,
    ) as client,
  ):
    return await read_delivery_metadata(
      client, "GET", f"/market-data/v1/history/{identity}"
    )


async def run(args):
  from quantx_infrastructure.services.legacy_import_migration import (
    LegacyImportPlan,
    apply_import_migration,
    plan_import_migration,
    recover_import_migration,
  )
  from sqlalchemy.ext.asyncio import create_async_engine

  engine = create_async_engine(
    os.environ["DATABASE_URL"],
    pool_size=1,
    max_overflow=0,
    connect_args={
      "server_settings": {
        "statement_timeout": "10000",
        "default_transaction_read_only": "on" if args.command == "plan" else "off",
        "application_name": "quantx-legacy-import-migration",
      }
    },
  )
  try:
    if args.command == "plan":
      remote = await producer(args.delivery_id)
      return (await plan_import_migration(engine, args.delivery_id, remote)).model_dump(
        mode="json"
      )
    if args.command == "apply":
      plan = LegacyImportPlan.model_validate(read_plan(args.input))
      return await apply_import_migration(
        engine, plan, await producer(plan.delivery_id)
      )
    return await recover_import_migration(engine, args.delivery_id, args.reason)
  finally:
    await engine.dispose()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("command", choices=("plan", "apply", "recover"))
  parser.add_argument("--environment", required=True, choices=("development",))
  parser.add_argument("--delivery-id", type=identity)
  parser.add_argument("--input", type=Path)
  parser.add_argument("--reason")
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--config-root", type=Path)
  args = parser.parse_args()
  if args.command == "apply":
    valid = args.input and not args.delivery_id and not args.reason
  else:
    valid = (
      args.delivery_id
      and not args.input
      and (bool(args.reason) if args.command == "recover" else not args.reason)
    )
  if not valid:
    parser.error(
      "plan requires --delivery-id; apply requires --input; recover requires --delivery-id and --reason"
    )
  logging.disable(logging.CRITICAL)
  try:
    configure(args.environment, args.config_root)
    with args.output.open("x", encoding="utf-8") as output:
      result = asyncio.run(run(args))
      json.dump(result, output, ensure_ascii=False, separators=(",", ":"))
      output.write("\n")
  except Exception as exc:
    print(
      json.dumps(
        {
          "status": "failed",
          "exception_type": type(exc).__name__,
          "reason_code": str(exc)
          if re.fullmatch(
            r"(?:LEGACY_IMPORT|LEGACY_DELIVERY|DELIVERY|SOURCE_PROVENANCE)_[A-Z_]+", str(exc)
          )
          else "LEGACY_IMPORT_FAILED",
        }
      )
    )
    return 1
  print(json.dumps({"status": args.command + "-completed"}))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
