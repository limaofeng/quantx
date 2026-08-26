"""QMT Agent enrollment and runtime CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import NoReturn

import httpx

from .broker import LiveBroker, QmtDataBroker
from .credentials import DeviceCredentialStore, state_directory
from .emergency import EmergencyStopStore
from .journal import LocalJournal
from .process_watchdog import AgentProcessWatchdog
from .runtime import (
  AgentRuntime,
  _FatalMarketDataPreparationError,
  _FatalTradingRecoveryError,
)

FATAL_MARKET_DATA_EXIT_CODE = 70


def _hard_exit_for_fatal_market_data(exit_code: int) -> NoReturn:
  """Terminate even when an orphaned native XTData thread remains alive."""
  os._exit(exit_code)


async def _run_runtime_guarded(
  runtime: AgentRuntime,
  watchdog: AgentProcessWatchdog,
) -> None:
  runtime_task = asyncio.create_task(
    runtime.run_forever(),
    name="qmt-agent-runtime",
  )
  heartbeat_task = asyncio.create_task(
    watchdog.heartbeat_loop(),
    name="qmt-agent-process-watchdog-heartbeat",
  )
  try:
    done, _ = await asyncio.wait(
      {runtime_task, heartbeat_task},
      return_when=asyncio.FIRST_COMPLETED,
    )
    if heartbeat_task in done:
      await heartbeat_task
      raise RuntimeError("Agent process watchdog stopped unexpectedly")
    await runtime_task
  finally:
    for task in (runtime_task, heartbeat_task):
      if not task.done():
        task.cancel()
    await asyncio.gather(runtime_task, heartbeat_task, return_exceptions=True)


def _run_runtime(
  runtime: AgentRuntime,
  watchdog: AgentProcessWatchdog | None = None,
) -> None:
  owned_watchdog = watchdog
  if owned_watchdog is None:
    owned_watchdog = AgentProcessWatchdog.create(state_directory())
    owned_watchdog.start()
  try:
    try:
      asyncio.run(_run_runtime_guarded(runtime, owned_watchdog))
    except (_FatalMarketDataPreparationError, _FatalTradingRecoveryError):
      logging.getLogger(__name__).critical(
        "Fatal native QMT state requires a supervised process restart",
        exc_info=True,
      )
      _hard_exit_for_fatal_market_data(FATAL_MARKET_DATA_EXIT_CODE)
      # The production implementation never returns. Keep the exception path
      # explicit so tests can monkeypatch the hard-exit boundary safely.
      raise
  finally:
    owned_watchdog.close()


def _accounts() -> set[str]:
  return {
    value.strip()
    for value in os.environ.get("QMT_ACCOUNT_WHITELIST", "").split(",")
    if value.strip()
  }


def _require_safe_run_mode(mode: str, allowed_accounts: set[str]) -> None:
  if mode != "data-only" and not allowed_accounts:
    raise SystemExit("paper/live mode requires QMT_ACCOUNT_WHITELIST")
  if mode != "live":
    return

  environment = os.environ.get("ENV", "").strip().lower()
  if environment not in {"testing", "production"}:
    raise SystemExit("live mode requires ENV=testing or ENV=production")
  if os.environ.get("ENABLE_REAL_TRADING", "").strip().lower() != "true":
    raise SystemExit("live mode requires ENABLE_REAL_TRADING=true")
  if os.environ.get("QMT_REAL_TRADING_ENABLED", "").strip().lower() != "true":
    raise SystemExit("live mode requires QMT_REAL_TRADING_ENABLED=true")


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="QuantX QMT Agent")
  subparsers = parser.add_subparsers(dest="command")
  run = subparsers.add_parser("run", help="运行出站 QMT Agent")
  run.add_argument(
    "--mode",
    choices=("data-only", "paper", "live"),
    default=os.environ.get("QMT_AGENT_MODE", "data-only"),
  )
  enroll = subparsers.add_parser("enroll", help="用一次性登记码绑定设备")
  enroll.add_argument("--api-url", default="http://127.0.0.1:8080")
  enroll.add_argument("--code", required=True)
  subparsers.add_parser("status", help="显示本机登记状态")
  emergency_stop = subparsers.add_parser(
    "emergency-stop",
    help="本地紧急停止新委托；撤单仍允许执行",
  )
  emergency_stop.add_argument("--reason", required=True)
  subparsers.add_parser("emergency-status", help="显示本地紧急停止状态")
  emergency_clear = subparsers.add_parser(
    "emergency-clear",
    help="清除本地紧急停止状态",
  )
  emergency_clear.add_argument(
    "--confirmation",
    "--confirm",
    dest="confirmation",
    required=True,
  )
  backup = subparsers.add_parser(
    "backup-state",
    help="备份本地 journal 和非敏感设备元数据",
  )
  backup.add_argument("--destination", required=True)
  prune = subparsers.add_parser("prune-journal", help="清理已确认的旧 journal")
  prune.add_argument("--retention-days", type=int, default=30)
  args = parser.parse_args()
  if args.command is None:
    args.command = "run"
    args.mode = os.environ.get("QMT_AGENT_MODE", "data-only")
  return args


def _enroll(api_url: str, code: str) -> None:
  response = httpx.post(
    f"{api_url.rstrip('/')}/auth/agent/enrollments/exchange",
    json={"enrollmentCode": code},
    timeout=10.0,
  )
  response.raise_for_status()
  payload = response.json()
  device_id = str(payload.get("deviceId") or payload.get("device_id") or "")
  device_secret = str(payload.get("deviceSecret") or payload.get("device_secret") or "")
  if not device_id or not device_secret:
    raise RuntimeError("设备登记响应不完整")
  DeviceCredentialStore().save(
    api_url=api_url,
    device_id=device_id,
    device_secret=device_secret,
  )
  print(f"QMT Agent 已登记，device_id={device_id}")


def _run(mode: str) -> None:
  allowed_accounts = _accounts()
  _require_safe_run_mode(mode, allowed_accounts)
  # Start before constructing XTData/XTTrading managers: their native connect
  # paths can also hold the GIL, before AgentRuntime exists.
  watchdog = AgentProcessWatchdog.create(state_directory())
  watchdog.start()
  try:
    try:
      configuration, secret = DeviceCredentialStore().load()
    except RuntimeError as exc:
      raise SystemExit(str(exc)) from None
    journal = LocalJournal(state_directory() / "idempotency.sqlite3")
    broker = (
      LiveBroker(allowed_accounts, journal=journal)
      if mode == "live"
      else QmtDataBroker(allowed_accounts, data_only=mode == "data-only")
    )
    runtime = AgentRuntime(
      configuration=configuration,
      device_secret=secret,
      mode=mode,
      allowed_accounts=allowed_accounts,
      broker=broker,
      journal=journal,
      emergency_stop=EmergencyStopStore(state_directory() / "emergency-stop.json"),
    )
  except BaseException:
    watchdog.close()
    raise
  _run_runtime(runtime, watchdog)


def main() -> None:
  args = parse_args()
  logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
  emergency = EmergencyStopStore(state_directory() / "emergency-stop.json")
  if args.command == "enroll":
    _enroll(args.api_url, args.code)
  elif args.command == "status":
    try:
      configuration, _ = DeviceCredentialStore().load()
    except RuntimeError as exc:
      raise SystemExit(str(exc)) from None
    print(
      f"registered device_id={configuration.device_id} api_url={configuration.api_url}"
    )
  elif args.command == "emergency-stop":
    print(
      json.dumps(
        emergency.activate(args.reason),
        ensure_ascii=False,
      )
    )
  elif args.command == "emergency-status":
    print(json.dumps(emergency.status(), ensure_ascii=False))
  elif args.command == "emergency-clear":
    try:
      emergency.clear(args.confirmation)
    except ValueError as exc:
      raise SystemExit(str(exc)) from None
    print("QMT Agent 本地紧急停止已清除")
  elif args.command == "backup-state":
    destination = Path(args.destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    source_directory = state_directory()
    journal = LocalJournal(source_directory / "idempotency.sqlite3")
    journal.backup_to(destination / "idempotency.sqlite3")
    device_path = source_directory / "device.json"
    if device_path.exists():
      shutil.copy2(device_path, destination / "device.json")
    emergency_path = source_directory / "emergency-stop.json"
    if emergency_path.exists():
      shutil.copy2(emergency_path, destination / "emergency-stop.json")
    print(json.dumps(journal.stats(), ensure_ascii=False))
  elif args.command == "prune-journal":
    journal = LocalJournal(state_directory() / "idempotency.sqlite3")
    print(
      json.dumps(
        journal.prune(args.retention_days),
        ensure_ascii=False,
      )
    )
  else:
    _run(args.mode)


if __name__ == "__main__":
  main()
