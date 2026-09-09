"""Explicit, fail-closed deployment configuration, before any service imports.

This check establishes local deployment identity. Database grants and remote
service identity must additionally be verified before a dispatcher can start.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote, urlsplit

TRAINING_POOL = "quantx-train-pool"


class TrainerConfigurationError(ValueError):
  """A public error that never includes configuration values or credentials."""


def _path(value: str, name: str) -> Path:
  path = Path(value)
  if not path.is_absolute():
    raise TrainerConfigurationError(f"{name} must be an absolute local path")
  return path.resolve()


def _overlaps(left: Path, right: Path) -> bool:
  return left == right or left in right.parents or right in left.parents


@dataclass(frozen=True)
class TrainerConfig:
  environment: str
  code_root: Path = field(repr=False)
  production_root: Path = field(repr=False)
  state_root: Path = field(repr=False)
  database_url: str = field(repr=False)
  prefect_api_url: str = field(repr=False)
  prefect_pool: str

  @classmethod
  def load(cls, filename: Path) -> TrainerConfig:
    """Read only the requested file; never load repository or ambient dotenv."""
    try:
      with filename.open("rb") as stream:
        values = tomllib.load(stream)
    except (OSError, ValueError):
      raise TrainerConfigurationError(
        "Cannot read Trainer TOML configuration"
      ) from None
    expected = {item.name for item in fields(cls)}
    if set(values) != expected or any(
      not isinstance(value, str) or not value.strip() for value in values.values()
    ):
      raise TrainerConfigurationError(
        "Trainer configuration fields are missing or invalid"
      )
    if values["environment"] != "development":
      raise TrainerConfigurationError("Trainer requires environment=development")
    if values["prefect_pool"] != TRAINING_POOL:
      raise TrainerConfigurationError("Trainer requires its dedicated training pool")
    for name in ("code_root", "production_root", "state_root"):
      values[name] = _path(values[name], name)
    config = cls(**values)
    config._validate_targets()
    return config

  def _validate_targets(self) -> None:
    if _overlaps(self.code_root, self.production_root) or _overlaps(
      self.state_root, self.production_root
    ):
      raise TrainerConfigurationError("Trainer paths must be separate from production")
    # Keep mutable artifacts out of source code, including through symlinks.
    if _overlaps(self.state_root, self.code_root):
      raise TrainerConfigurationError(
        "Trainer state and code directories must be separate"
      )
    try:
      database = urlsplit(self.database_url)
      prefect = urlsplit(self.prefect_api_url)
      valid_database = (
        database.scheme == "postgresql+asyncpg"
        and bool(database.hostname)
        and bool(database.port)
        and bool(database.username)
        and unquote(database.path).count("/") == 1
        and unquote(database.path)[1:].endswith("_dev")
        and not database.query
        and not database.fragment
      )
      valid_prefect = (
        prefect.scheme in {"http", "https"}
        and bool(prefect.hostname)
        and bool(prefect.port)
        and prefect.path == "/api"
        and not prefect.username
        and not prefect.password
        and not prefect.query
        and not prefect.fragment
        # The existing production default must never be inherited or copied.
        and not (prefect.hostname == "192.168.5.6" and prefect.port == 30420)
      )
    except ValueError:
      raise TrainerConfigurationError("Invalid Trainer service endpoint") from None
    if not valid_database:
      raise TrainerConfigurationError(
        "Trainer requires an explicit PostgreSQL *_dev target"
      )
    if not valid_prefect:
      raise TrainerConfigurationError(
        "Trainer requires an explicit development Prefect endpoint"
      )

  def validate_runtime(self, *, prefix: Path, code_root: Path) -> None:
    prefix = prefix.resolve()
    if prefix.name != "quantx-train" or not (prefix / "conda-meta").is_dir():
      raise TrainerConfigurationError(
        "Trainer requires the quantx-train Conda interpreter"
      )
    if _overlaps(prefix, self.production_root):
      raise TrainerConfigurationError(
        "Trainer interpreter must be separate from production"
      )
    if code_root.resolve() != self.code_root:
      raise TrainerConfigurationError(
        "Trainer configuration does not match the running checkout"
      )

  def child_environment(self, ambient: Mapping[str, str]) -> dict[str, str]:
    """Inherit OS essentials, never broker, production or Python search settings."""
    allowed = {
      "SYSTEMROOT",
      "WINDIR",
      "COMSPEC",
      "TEMP",
      "TMP",
      "TMPDIR",
      "HOME",
      "USERPROFILE",
      "APPDATA",
      "LOCALAPPDATA",
      "LANG",
      "LC_ALL",
    }
    result = {key: value for key, value in ambient.items() if key.upper() in allowed}
    result.update(
      {
        "ENV": self.environment,
        "DATABASE_URL": self.database_url,
        "PREFECT_API_URL": self.prefect_api_url,
        "PREFECT_WORKER_POOL": self.prefect_pool,
        "ENABLE_REAL_TRADING": "false",
        "QMT_REAL_TRADING_ENABLED": "false",
        "QUANTX_ROOT": str(self.code_root),
        "QUANTX_RESEARCH_DATASETS_ROOT": str(self.state_root / "datasets"),
        "QUANTX_RESEARCH_RUNS_ROOT": str(self.state_root / "runs"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
      }
    )
    return result


def main() -> None:
  parser = argparse.ArgumentParser(
    description="Validate isolated Trainer deployment configuration"
  )
  parser.add_argument("--config", required=True, type=Path)
  args = parser.parse_args()
  try:
    config = TrainerConfig.load(args.config)
    config.validate_runtime(
      prefix=Path(sys.prefix), code_root=Path(__file__).resolve().parents[4]
    )
  except TrainerConfigurationError as exc:
    parser.exit(2, f"Trainer configuration rejected: {exc}\n")
  print(
    "Trainer local configuration validated; remote permissions are not yet verified."
  )


if __name__ == "__main__":
  main()
