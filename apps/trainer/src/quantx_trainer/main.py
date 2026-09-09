"""Trainer administrative entrypoint; no implicit environment or connections."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from quantx_trainer.config import TrainerConfig, TrainerConfigurationError


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="quantx-trainer")
  parser.add_argument("command", choices=["preflight", "publish-result"])
  parser.add_argument("--config", type=Path, required=True)
  parser.add_argument("--run-id")
  parser.add_argument("--owner")
  args = parser.parse_args(argv)
  if args.command == "publish-result" and (not args.run_id or not args.owner):
    parser.error("publish-result requires --run-id and --owner")
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
  print(json.dumps(result, sort_keys=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
