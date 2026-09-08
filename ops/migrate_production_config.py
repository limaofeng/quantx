"""Prepare and apply the Windows environment cutover without exposing credentials."""

import argparse
import hashlib
import json
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psutil
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
FILES = (".env", ".env.development", ".env.production", ".env.testing")
REVOKE_DEVELOPMENT_SESSIONS_SQL = """
  UPDATE auth_device_sessions AS session SET revoked_at=CURRENT_TIMESTAMP
  WHERE session.revoked_at IS NULL AND EXISTS (
    SELECT 1 FROM auth_audit_events AS event
    WHERE event.device_session_id=session.id
      AND event.event_type='DEVELOPMENT_LOGIN' AND event.outcome='SUCCEEDED'
  )
"""


def fingerprints(directory: Path) -> dict:
  return {
    name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
    if (directory / name).exists()
    else None
    for name in FILES
  }


def with_host(value: str, host: str) -> str:
  parsed = urlsplit(value)
  authority = parsed.netloc.rsplit("@", 1)
  userinfo = authority[0] + "@" if len(authority) == 2 else ""
  address = f"[{host}]" if ":" in host else host
  return urlunsplit(
    parsed._replace(
      netloc=userinfo + address + (f":{parsed.port}" if parsed.port else "")
    )
  )


def write_env(path: Path, values: dict) -> None:
  path.write_text(
    "".join(
      f"{key}={json.dumps(value, ensure_ascii=False)}\n"
      for key, value in values.items()
      if value is not None
    ),
    encoding="utf-8",
  )


def prepare(*, dependency_host: str, enable_live: bool) -> None:
  directory = ROOT / "apps/api"
  destination = ROOT / ".runtime/config-migration"
  destination.mkdir(parents=True, exist_ok=True)
  values = {
    **dotenv_values(directory / ".env", interpolate=False),
    **dotenv_values(directory / ".env.development", interpolate=False),
  }
  for key in ("DATABASE_URL", "REDIS_URL", "INFLUXDB_HOST", "PREFECT_API_URL"):
    if not values.get(key):
      raise ValueError(
        "The current Windows configuration lacks explicit data service endpoints"
      )
    if dependency_host:
      values[key] = with_host(values[key], dependency_host)
  if dependency_host:
    values["REDIS_HOST"] = dependency_host
  values.pop("QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST", None)
  values.update(
    ENV="production", DEBUG="false", GRAPHQL_DEBUG="false", CONDA_ENV_NAME="quantx"
  )
  if enable_live:
    accounts = set()
    for key in (
      "QMT_ACCOUNT_WHITELIST",
      "REAL_TRADING_ACCOUNT_ALLOWLIST",
      "AUTH_BOOTSTRAP_ACCOUNT_IDS",
    ):
      raw = values.get(key) or ""
      accounts.update(
        json.loads(raw)
        if raw.startswith("[")
        else [part.strip() for part in raw.split(",") if part.strip()]
      )
    if len(accounts) != 1:
      raise ValueError(
        "Live migration requires exactly one existing configured account"
      )
    values.update(
      ENABLE_REAL_TRADING="true",
      QMT_REAL_TRADING_ENABLED="true",
      T_TRADE_LIVE_ENABLED="true",
      QMT_ACCOUNT_WHITELIST=next(iter(accounts)),
      REAL_TRADING_ACCOUNT_ALLOWLIST=json.dumps(sorted(accounts)),
    )
  values["QUANTX_MARKET_DATA_TOKEN"] = secrets.token_urlsafe(48)
  testing = {**values, **dotenv_values(directory / ".env.testing", interpolate=False)}
  database = make_url(values["DATABASE_URL"])
  testing["DATABASE_URL"] = database.set(
    database=database.database + "_test"
  ).render_as_string(hide_password=False)
  testing["REDIS_URL"] = urlunsplit(urlsplit(values["REDIS_URL"])._replace(path="/1"))
  testing["REDIS_HOST"] = urlsplit(values["REDIS_URL"]).hostname
  testing["REDIS_PORT"] = str(urlsplit(values["REDIS_URL"]).port or 6379)
  testing["REDIS_DB"] = "1"
  testing["INFLUXDB_HOST"] = values["INFLUXDB_HOST"]
  testing["INFLUXDB_DATABASE"] = "quantx_test"
  testing.update(
    ENV="testing",
    ENABLE_REAL_TRADING="false",
    QMT_REAL_TRADING_ENABLED="false",
    T_TRADE_LIVE_ENABLED="false",
    REAL_TRADING_ACCOUNT_ALLOWLIST="[]",
    QMT_ACCOUNT_WHITELIST="",
    QMT_AGENT_MODE="data-only",
    SECRET_KEY=secrets.token_urlsafe(48),
  )
  testing.pop("QUANTX_MARKET_DATA_TOKEN", None)
  write_env(destination / "production.env", values)
  write_env(destination / "testing.env", testing)
  (destination / "manifest.json").write_text(
    json.dumps(
      {"sources": fingerprints(directory), "live_enabled": enable_live}, indent=2
    ),
    encoding="utf-8",
  )
  print(
    "Prepared production and isolated testing configurations; no credentials printed."
  )


def apply() -> None:
  for pattern in (".runtime/state/*-processes.json", ".runtime/monitor/*-process.json"):
    for state in ROOT.glob(pattern):
      entries = json.loads(state.read_text(encoding="utf-8-sig"))
      for entry in entries if isinstance(entries, list) else [entries]:
        if psutil.pid_exists(int(entry.get("pid", 0))):
          raise RuntimeError(
            "Stop the managed runtime and Monitor before applying configuration"
          )
  directory = ROOT / "apps/api"
  prepared = ROOT / ".runtime/config-migration"
  manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
  if manifest["sources"] != fingerprints(directory):
    raise RuntimeError("Source configuration changed; prepare again before applying")
  backup = prepared / "original-config"
  backup.mkdir(exist_ok=False)
  for name in FILES:
    if (directory / name).exists():
      shutil.copyfile(directory / name, backup / name)
  for source, target in (
    ("production.env", ".env.production"),
    ("testing.env", ".env.testing"),
  ):
    temporary = directory / (target + ".tmp")
    shutil.copyfile(prepared / source, temporary)
    temporary.replace(directory / target)
  (directory / ".env").write_text(
    "# Shared non-sensitive defaults only.\n", encoding="utf-8"
  )
  revoke_development_sessions()
  print(
    "Applied explicit production/testing configuration; originals retained locally."
  )


def revoke_development_sessions() -> None:
  import psycopg2
  from runtime_config import load_environment

  values = load_environment(ROOT, "production")
  url = make_url(values["DATABASE_URL"])
  with psycopg2.connect(
    host=url.host,
    port=url.port,
    dbname=url.database,
    user=url.username,
    password=url.password,
    connect_timeout=10,
  ) as connection:
    with connection.cursor() as cursor:
      cursor.execute(REVOKE_DEVELOPMENT_SESSIONS_SQL)
      print(f"Revoked development-origin sessions: {cursor.rowcount}")


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument(
    "command", choices=("prepare", "apply", "revoke-development-sessions")
  )
  parser.add_argument("--dependency-host", default="")
  parser.add_argument("--enable-live", action="store_true")
  args = parser.parse_args()
  if args.command == "prepare":
    prepare(dependency_host=args.dependency_host, enable_live=args.enable_live)
  elif args.command == "apply":
    apply()
  else:
    revoke_development_sessions()


if __name__ == "__main__":
  main()
