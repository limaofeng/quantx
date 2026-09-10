"""Plan or adopt a producer v2 receipt while archiving original v1 evidence."""

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
    raise ValueError("LEGACY_EXPORT_INPUT_CAPACITY")
  return json.loads(payload)


async def run(args):
  from quantx_infrastructure.services.legacy_export_migration import (
    LegacyExportPlan,
    apply_export_migration,
    plan_export_migration,
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
        "application_name": "quantx-legacy-export-migration",
      }
    },
  )
  try:
    if args.command == "plan":
      return (await plan_export_migration(engine, args.delivery_id)).model_dump(
        mode="json"
      )
    plan = LegacyExportPlan.model_validate(read_json(args.input))
    return await apply_export_migration(engine, plan)
  finally:
    await engine.dispose()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("command", choices=("plan", "apply"))
  parser.add_argument("--environment", required=True, choices=("production",))
  parser.add_argument(
    "--input",
    type=Path,
    help="Saved producer receipt migration plan for apply",
  )
  parser.add_argument("--delivery-id", help="Original producer delivery ID for plan")
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--config-root", type=Path)
  args = parser.parse_args()
  if (args.command == "plan" and (not args.delivery_id or args.input)) or (
    args.command == "apply" and (not args.input or args.delivery_id)
  ):
    parser.error("plan requires --delivery-id; apply requires --input")
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
          if re.fullmatch(
            r"(?:LEGACY_EXPORT|LEGACY_DELIVERY|NATIVE_STORAGE_VERSION|NATIVE_VERSION|SOURCE_PROVENANCE|DELIVERY)_[A-Z_]+",
            str(exc),
          )
          else "LEGACY_EXPORT_FAILED",
        }
      )
    )
    return 1
  print(json.dumps({"status": args.command + "-completed"}))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
