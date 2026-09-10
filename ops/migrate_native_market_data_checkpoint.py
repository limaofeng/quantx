"""Plan or migrate one frozen canonical checkpoint without writing market data."""

import argparse
import asyncio
import json
import logging
import os
import re
from pathlib import Path

from diagnose_market_data_readback import configure


def read_json(path):
  with path.open("rb") as stream:
    payload = stream.read(2 * 1024 * 1024 + 1)
  if len(payload) > 2 * 1024 * 1024:
    raise ValueError("NATIVE_MIGRATION_INPUT_CAPACITY")
  return json.loads(payload)


async def run(args):
  from quantx_infrastructure.services.native_checkpoint_migration import (
    NativeCheckpointPlan,
    apply_native_migration,
    plan_native_migration,
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
        "application_name": "quantx-native-checkpoint-migration",
      }
    },
  )
  try:
    if args.command == "plan":
      return (await plan_native_migration(engine, args.request_id)).model_dump(
        mode="json"
      )
    plan = NativeCheckpointPlan.model_validate(read_json(args.input))
    return await apply_native_migration(engine, plan)
  finally:
    await engine.dispose()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("command", choices=("plan", "apply"))
  parser.add_argument(
    "--environment", required=True, choices=("development", "production")
  )
  parser.add_argument(
    "--input",
    type=Path,
    help="Saved native checkpoint migration plan for apply",
  )
  parser.add_argument("--request-id", help="Original source request ID for plan")
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--config-root", type=Path)
  args = parser.parse_args()
  if (args.command == "plan" and (not args.request_id or args.input)) or (
    args.command == "apply" and (not args.input or args.request_id)
  ):
    parser.error("plan requires --request-id; apply requires --input")
  logging.disable(logging.CRITICAL)
  try:
    configure(args.environment, args.config_root)
    # Reserve the output before any mutation. A lost response can be replayed idempotently.
    with args.output.open("x", encoding="utf-8") as output:
      result = asyncio.run(run(args))
      json.dump(result, output, ensure_ascii=False, indent=2)
      output.write("\n")
  except Exception as exc:
    print(
      json.dumps(
        {
          "status": "failed",
          "exception_type": type(exc).__name__,
          "reason_code": str(exc)
          if re.fullmatch(r"NATIVE_MIGRATION_[A-Z_]+", str(exc))
          else "NATIVE_MIGRATION_FAILED",
        }
      )
    )
    return 1
  print(json.dumps({"status": args.command + "-completed"}))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
