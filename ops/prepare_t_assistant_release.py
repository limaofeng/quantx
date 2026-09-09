"""Read-only development release request preparation; never approves or dispatches."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from runtime_config import load_environment


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--environment", required=True, choices=["development"])
  for name in (
    "account-id",
    "source-execution-id",
    "config-version-id",
    "expected-config-hash",
    "expected-report-hash",
    "expected-policy-hash",
    "evidence-directory",
    "window-start",
    "window-end",
    "output",
  ):
    parser.add_argument(f"--{name}", required=True)
  args = vars(parser.parse_args())
  environment = args.pop("environment")
  output = Path(args.pop("output"))
  if output.exists():
    parser.error("output already exists")
  for key in ("window_start", "window_end"):
    args[key] = datetime.fromisoformat(args[key])
  os.environ.update(load_environment(Path(__file__).resolve().parents[1], environment))

  async def prepare():
    from quantx_engine.t_assistant_release_request import build_release_request
    from quantx_infrastructure.config.settings import settings
    from quantx_infrastructure.database.relational_connection import AsyncSessionLocal

    if settings.environment != "development":
      raise ValueError("DEVELOPMENT_ENVIRONMENT_REQUIRED")
    async with AsyncSessionLocal() as db:
      result = await build_release_request(db, **args, now=datetime.now(UTC))
      await db.rollback()
      return result

  result = asyncio.run(prepare())
  with output.open("x", encoding="utf-8") as file:
    json.dump(result, file, ensure_ascii=False, indent=2)
    file.write("\n")
  print("Release request prepared; no approval or command was created.")


if __name__ == "__main__":
  main()
