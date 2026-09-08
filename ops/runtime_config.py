"""Explicit deployment configuration, validated before any child is started."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

DEPENDENCIES = ("DATABASE_URL", "REDIS_URL", "INFLUXDB_HOST", "PREFECT_API_URL")
LIVE_KEYS = ("ENABLE_REAL_TRADING", "QMT_REAL_TRADING_ENABLED", "T_TRADE_LIVE_ENABLED")


def load_environment(root: Path, environment: str) -> dict[str, str]:
  if environment not in {"development", "production", "testing"}:
    raise ValueError("Unsupported deployment environment")
  directory = root / "apps" / "api"
  selected = directory / f".env.{environment}"
  if not selected.is_file():
    raise ValueError(f"Explicit .env.{environment} is required")
  shared = dotenv_values(directory / ".env", interpolate=False)
  forbidden = set(DEPENDENCIES + LIVE_KEYS) | {
    "REAL_TRADING_ACCOUNT_ALLOWLIST",
    "QMT_ACCOUNT_WHITELIST",
    "INFLUXDB_TOKEN",
    "REDIS_PASSWORD",
    "QMT_AGENT_MODE",
  }
  if forbidden.intersection(shared):
    raise ValueError("Move environment-specific settings out of shared .env")
  configured = dotenv_values(selected, interpolate=False)
  if any(not configured.get(key) for key in DEPENDENCIES):
    raise ValueError("All data service endpoints must be explicitly configured")
  result = dict(os.environ)
  result.update({k: v for k, v in shared.items() if v is not None})
  result.update({k: v for k, v in configured.items() if v is not None})
  result["ENV"] = environment
  result["QUANTX_ENV_FILE"] = str(selected)
  result["QUANTX_ROOT"] = str(root)
  token = result.get("QUANTX_MARKET_DATA_TOKEN", "")
  result["QUANTX_MARKET_DATA_ENABLED"] = str(
    len(token) >= 32 and "CHANGE_ME" not in token
  ).lower()
  if environment == "development":
    for key in DEPENDENCIES:
      if urlsplit(result[key]).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"Development {key} must point to a local service")
    if not urlsplit(result["DATABASE_URL"]).path.endswith("_dev"):
      raise ValueError("Development PostgreSQL database name must end in _dev")
    if not result.get("INFLUXDB_DATABASE", "").endswith("_dev"):
      raise ValueError("Development InfluxDB database name must end in _dev")
    result.update(dict.fromkeys(LIVE_KEYS, "false"))
    result["REAL_TRADING_ACCOUNT_ALLOWLIST"] = "[]"
    result["QMT_ACCOUNT_WHITELIST"] = ""
  return result


if __name__ == "__main__":
  import json
  import sys

  values = load_environment(Path(__file__).resolve().parents[1], sys.argv[1])
  if "--json" in sys.argv:
    print(json.dumps(values))
  else:
    print("Explicit environment configuration: OK")
