"""Trainer administrative entrypoint; no implicit environment or connections."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from quantx_trainer.config import TrainerConfig, TrainerConfigurationError


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="quantx-trainer")
  parser.add_argument("command", choices=["preflight"])
  parser.add_argument("--config", type=Path, required=True)
  args = parser.parse_args(argv)
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
  print(json.dumps(result, sort_keys=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
