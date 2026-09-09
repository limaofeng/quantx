"""Trainer administrative entrypoint; no implicit environment or connections."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from quantx_trainer.config import TrainerConfig, TrainerConfigurationError


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="quantx-trainer")
  parser.add_argument(
    "command", choices=["preflight", "publish-result", "publish-dataset"]
  )
  parser.add_argument("--config", type=Path, required=True)
  parser.add_argument("--run-id")
  parser.add_argument("--owner")
  parser.add_argument("--dataset-version")
  args = parser.parse_args(argv)
  if args.command == "publish-result" and (not args.run_id or not args.owner):
    parser.error("publish-result requires --run-id and --owner")
  if args.command == "publish-dataset" and not args.dataset_version:
    parser.error("publish-dataset requires --dataset-version")
  try:
    config = TrainerConfig.load(args.config)
    config.validate_runtime(
      prefix=Path(sys.prefix), code_root=Path(__file__).resolve().parents[4]
    )
  except TrainerConfigurationError as exc:
    print(f"Trainer configuration rejected: {exc}", file=sys.stderr)
    return 2

  from quantx_trainer.preflight import TrainerPreflightError, preflight

  try:
    result = asyncio.run(preflight(config))
  except TrainerPreflightError as exc:
    print(f"Trainer preflight rejected: {exc}", file=sys.stderr)
    return 2
  if args.command == "publish-result":
    from quantx_trainer.publication import PublicationError, run_publication

    try:
      result = asyncio.run(
        run_publication(config, run_id=args.run_id, owner=args.owner)
      )
    except PublicationError as exc:
      print(f"Trainer publication pending: {exc}", file=sys.stderr)
      return 3
  if args.command == "publish-dataset":
    from quantx_infrastructure.repositories.stock_selection_training_repository import (
      StockSelectionTrainingRepository,
    )

    from quantx_trainer.dataset_transfer import publish_dataset
    from quantx_trainer.runtime import training_session

    async def publish():
      async with training_session(str(args.config)) as db:
        return await publish_dataset(
          config,
          StockSelectionTrainingRepository(db),
          dataset_version=args.dataset_version,
        )

    try:
      result = asyncio.run(publish())
    except Exception:
      print(
        "Trainer dataset publication pending: DATASET_PUBLICATION_RETRY_REQUIRED",
        file=sys.stderr,
      )
      return 3
  print(json.dumps(result, sort_keys=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
