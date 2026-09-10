"""Explicit control-plane export/import; never runs the Trainer or trading stack."""

import argparse
import asyncio
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit


def load_config(path: Path, command: str, *, platform: str = sys.platform):
  if command not in {"export", "import"}:
    raise ValueError("MODEL_RELEASE_COMMAND_INVALID")
  value = tomllib.loads(path.read_text(encoding="utf-8"))
  if set(value) != {"environment", "database_url", "artifact_root"}:
    raise ValueError("MODEL_RELEASE_CONFIG_FIELDS_INVALID")
  if not all(isinstance(item, str) and item for item in value.values()):
    raise ValueError("MODEL_RELEASE_CONFIG_VALUES_INVALID")
  environment = value["environment"]
  if command == "export" and environment != "development":
    raise ValueError("MODEL_RELEASE_EXPORT_REQUIRES_DEVELOPMENT")
  if command == "import" and environment not in {"testing", "production"}:
    raise ValueError("MODEL_RELEASE_IMPORT_TARGET_INVALID")
  target = urlsplit(value["database_url"])
  database = unquote(target.path).removeprefix("/")
  if target.scheme != "postgresql+asyncpg" or not target.hostname or not database or target.query or target.fragment:
    raise ValueError("MODEL_RELEASE_DATABASE_INVALID")
  expected_suffix = {"development": "_dev", "testing": "_test"}.get(environment)
  if expected_suffix and not database.endswith(expected_suffix):
    raise ValueError("MODEL_RELEASE_DATABASE_ENVIRONMENT_MISMATCH")
  if environment == "production" and (platform != "win32" or database.endswith(("_dev", "_test"))):
    raise ValueError("MODEL_RELEASE_PRODUCTION_TARGET_INVALID")
  if platform == "darwin" and target.hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise ValueError("MODEL_RELEASE_MACOS_REQUIRES_LOCAL_DATABASE")
  if not Path(value["artifact_root"]).is_absolute():
    raise ValueError("MODEL_RELEASE_ARTIFACT_ROOT_MUST_BE_ABSOLUTE")
  return value


async def execute(args):
  from quantx_infrastructure.selection_model_release import verify_release

  if args.command == "verify":
    checked = verify_release(args.package, expected_bundle_id=args.bundle_id)
    return {"status": "VERIFIED", "bundle_id": checked.inventory.bundle_id,
            "model_version": checked.artifact.manifest["model_version"],
            "cpu_runtime": checked.cpu_runtime}
  config = load_config(args.config, args.command)
  # No production gates, broker variables or Python paths enter this operation.
  essentials = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
                "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL"}
  environment = {key: value for key, value in os.environ.items() if key.upper() in essentials}
  environment.update(ENV=config["environment"], DATABASE_URL=config["database_url"],
                     ENABLE_REAL_TRADING="false", QMT_REAL_TRADING_ENABLED="false",
                     DATABASE_PROCESS_ROLE="tooling", PREFECT_SERVER_ALLOW_EPHEMERAL_MODE="false")
  os.environ.clear()
  os.environ.update(environment)
  from sqlalchemy import text
  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
  from quantx_api.stock_selection_model_service import StockSelectionModelService
  from quantx_infrastructure.repositories.stock_selection_repository import StockSelectionRepository
  from quantx_infrastructure.repositories.stock_selection_training_repository import StockSelectionTrainingRepository

  engine = create_async_engine(config["database_url"], connect_args={"timeout": 10, "command_timeout": 30})
  try:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
      service = StockSelectionModelService(StockSelectionRepository(db),
        StockSelectionTrainingRepository(db) if args.command == "export" else None,
        runs_root=config["artifact_root"])
      if args.command == "export":
        await db.execute(text("SET TRANSACTION READ ONLY"))
        release = await service.export_release(args.run_key, source_environment=config["environment"],
          reviewed_by=args.reviewed_by, output=args.output, reserve_bytes=args.reserve_mib * 1024**2)
        return {"status": "EXPORTED", "bundle_id": release.inventory.bundle_id,
                "model_version": release.artifact.manifest["model_version"]}
      row = await service.import_release(args.package, expected_bundle_id=args.bundle_id,
        import_root=Path(config["artifact_root"]), reserve_bytes=args.reserve_mib * 1024**2)
      return {"status": "REGISTERED", "model_version": row.model_version, "stage": row.stage,
              "bundle_id": args.bundle_id, "state_version": row.state_version}
  finally:
    await engine.dispose()


def main(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  export = commands.add_parser("export", help="Export a reviewed successful development final evaluation")
  export.add_argument("--config", required=True, type=Path)
  export.add_argument("--run-key", required=True)
  export.add_argument("--reviewed-by", required=True)
  export.add_argument("--output", required=True, type=Path)
  export.add_argument("--reserve-mib", required=True, type=int)
  for command in ("verify", "import"):
    child = commands.add_parser(command)
    child.add_argument("--package", required=True, type=Path)
    child.add_argument("--bundle-id", required=True)
    if command == "import":
      child.add_argument("--config", required=True, type=Path)
      child.add_argument("--reserve-mib", required=True, type=int)
  args = parser.parse_args(argv)
  try:
    if Path(sys.prefix).name != "quantx" or not (Path(sys.prefix) / "conda-meta").is_dir():
      raise ValueError("MODEL_RELEASE_REQUIRES_QUANTX_CONDA")
    if getattr(args, "reserve_mib", 0) < 0:
      raise ValueError("MODEL_RELEASE_DISK_RESERVE_INVALID")
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / item / "src") for item in (
      "apps/api", "packages/infrastructure", "packages/application",
      "packages/domain", "packages/contracts",
    )]
    print(json.dumps(asyncio.run(execute(args)), ensure_ascii=False, sort_keys=True))
  except Exception as exc:
    message = str(exc)
    code = message if re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", message) else "MODEL_RELEASE_FAILED"
    print(code, file=sys.stderr)
    return 2
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
