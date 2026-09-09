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
    "command",
    choices=[
      "preflight",
      "publish-result",
      "publish-dataset",
      "drain",
      "resume",
      "admission-status",
      "serve",
      "status",
      "logs",
      "up",
      "down",
    ],
  )
  parser.add_argument("--config", type=Path, required=True)
  parser.add_argument("--run-id")
  parser.add_argument("--job-id")
  parser.add_argument("--owner")
  parser.add_argument("--dataset-version")
  parser.add_argument("--lines", type=int, default=100)
  args = parser.parse_args(argv)
  if args.job_id and args.command != "logs":
    parser.error("--job-id is only supported by logs")
  if args.command == "logs" and (
    bool(args.job_id) != bool(args.owner) or (args.job_id and args.run_id)
  ):
    parser.error("preparation logs require --job-id and --owner, without --run-id")
  if args.command == "logs" and not 1 <= args.lines <= 1000:
    parser.error("logs --lines must be 1..1000 (per stream with --run-id)")
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

  if args.command in {"up", "down"}:
    from quantx_trainer.launcher import start_service, stop_service

    try:
      result = (start_service if args.command == "up" else stop_service)(
        config, args.config
      )
    except Exception:
      print("Trainer lifecycle pending: SERVICE_OPERATION_UNCONFIRMED", file=sys.stderr)
      return 3
    if args.command == "down" and result.get("execution_state") == "GROUP_EXITED":
      from quantx_trainer.service_reconciliation import reconcile_stopped_service

      result.update(asyncio.run(reconcile_stopped_service(args.config)))
    print(json.dumps(result, sort_keys=True))
    if result.get("database_state") == "PENDING":
      return 3
    return (
      0 if result["service"] == ("ALIVE" if args.command == "up" else "OFFLINE") else 3
    )

  if args.command == "logs":
    from quantx_infrastructure.training_bundle_store import BundleTransferError

    from quantx_trainer.run_log import read_preparation_logs, read_run_logs
    from quantx_trainer.service_log import read_events

    try:
      events = (
        read_preparation_logs(
          config.state_root, args.job_id, args.owner, lines=args.lines
        )
        if args.job_id
        else (
          read_run_logs(config.state_root, args.run_id, lines=args.lines)
          if args.run_id
          else read_events(config.state_root, lines=args.lines)
        )
      )
      for event in events:
        print(json.dumps(event, sort_keys=True))
    except (OSError, ValueError, BundleTransferError):
      print("Trainer logs unavailable: SERVICE_LOG_READ_FAILED", file=sys.stderr)
      return 3
    return 0

  if args.command == "status":
    from quantx_infrastructure.training_bundle_store import BundleTransferError
    from quantx_infrastructure.training_host_guard import (
      host_guard_root,
      host_resource_status,
    )

    from quantx_trainer.admission import admission_status
    from quantx_trainer.service_status import service_status

    result = service_status(config.state_root, args.config)
    try:
      result["admission"] = admission_status(config.state_root / "control")["admission"]
    except (OSError, ValueError, BundleTransferError):
      result["admission"] = "UNKNOWN"
    try:
      result["host_resources"] = host_resource_status(host_guard_root())
    except Exception:
      result["host_resources"] = {"status": "UNKNOWN", "reason": "HOST_RESOURCE_STATE_UNKNOWN"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["service"] in {"ALIVE", "OFFLINE"} and result["admission"] != "UNKNOWN" and result["host_resources"]["status"] != "UNKNOWN" else 3

  if args.command == "serve":
    from quantx_trainer.service import serve

    try:
      serve(config, args.config)
    except KeyboardInterrupt:
      return 130
    except Exception:
      print("Trainer service stopped: SERVICE_START_OR_RUNTIME_FAILED", file=sys.stderr)
      return 3
    return 0

  if args.command in {"drain", "resume", "admission-status"}:
    from quantx_infrastructure.training_bundle_store import BundleTransferError

    from quantx_trainer.admission import admission_status, set_admission

    try:
      root = config.state_root / "control"
      result = (
        admission_status(root)
        if args.command == "admission-status"
        else set_admission(root, draining=args.command == "drain")
      )
    except (OSError, ValueError, BundleTransferError):
      print("Trainer admission control unavailable", file=sys.stderr)
      return 3
    print(json.dumps(result, sort_keys=True))
    return 0

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
