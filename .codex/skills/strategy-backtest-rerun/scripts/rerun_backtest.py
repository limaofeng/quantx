"""Run the QuantX real backtest-rerun integration test and print latest status."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_RUN_ID = "632958c3-751f-4862-80ab-b61ca30c0a8a"
DEFAULT_START = "2026-04-14 00:00:00"
DEFAULT_END = "2026-05-14 23:59:59"
DEFAULT_PYTHON = r"C:\Users\limao\miniconda3\envs\xtquant-demo\python.exe"
TEST_PATH = "tests/integration/core/strategies/test_backtest_rerun_real.py"


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[4]


def _python_executable(value: str | None) -> str:
  candidate = value or os.environ.get("QUANTX_PYTHON_EXE") or DEFAULT_PYTHON
  if Path(candidate).exists():
    return candidate
  return sys.executable


def _json_default(value: Any) -> str:
  if hasattr(value, "value"):
    return str(value.value)
  return str(value)


def _build_pytest_command(args: argparse.Namespace, python_exe: str) -> list[str]:
  return [
    python_exe,
    "-m",
    "pytest",
    TEST_PATH,
    "-q",
    "-s",
    "--basetemp",
    str(args.basetemp),
  ]


async def _query_latest_summary(run_id: str) -> dict[str, Any]:
  from sqlalchemy import func, select

  from database.relational_connection import AsyncSessionLocal
  from models.strategy_backtest import StrategyBacktest
  from models.strategy_performance_sample import StrategyPerformanceSample
  from models.strategy_run import StrategyRun

  async with AsyncSessionLocal() as db:
    latest_result = await db.execute(
      select(StrategyBacktest)
      .where(StrategyBacktest.strategy_run_id == run_id)
      .order_by(StrategyBacktest.version.desc())
      .limit(1)
    )
    latest = latest_result.scalar_one_or_none()
    run_result = await db.execute(select(StrategyRun).where(StrategyRun.id == run_id))
    run = run_result.scalar_one_or_none()

    temp_samples = 0
    if latest:
      sample_result = await db.execute(
        select(func.count()).select_from(StrategyPerformanceSample).where(
          StrategyPerformanceSample.backtest_id == latest.id
        )
      )
      temp_samples = int(sample_result.scalar_one())

    metrics = latest.metrics if latest and isinstance(latest.metrics, dict) else {}
    return {
      "run_id": run_id,
      "run_status": getattr(run.status, "value", run.status) if run else None,
      "run_mode": getattr(run.mode, "value", run.mode) if run else None,
      "latest_backtest_id": latest.id if latest else None,
      "latest_version": latest.version if latest else None,
      "latest_status": latest.status if latest else None,
      "backtest_start_time": (
        latest.backtest_start_time.isoformat()
        if latest and latest.backtest_start_time
        else None
      ),
      "backtest_end_time": (
        latest.backtest_end_time.isoformat() if latest and latest.backtest_end_time else None
      ),
      "performance_snapshot_path": metrics.get("performance_snapshot_path"),
      "temp_performance_samples": temp_samples,
      "metrics": {
        key: metrics.get(key)
        for key in [
          "trade_intents_generated",
          "orders_placed",
          "trades_executed",
          "total_pnl",
          "current_capital",
          "total_return_pct",
          "max_drawdown_pct",
          "win_rate_pct",
        ]
      },
    }


def _print_summary(summary: dict[str, Any]) -> None:
  print("\nLatest backtest summary:")
  print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Run QuantX strategy backtest rerun integration test."
  )
  parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
  parser.add_argument("--start", default=DEFAULT_START)
  parser.add_argument("--end", default=DEFAULT_END)
  parser.add_argument("--backend", type=Path, default=_repo_root() / "backend")
  parser.add_argument("--python", dest="python_exe", default=None)
  parser.add_argument("--basetemp", type=Path, default=Path(r"C:\tmp\quantx-pytest"))
  parser.add_argument("--dry-run", action="store_true")
  parser.add_argument("--summary-only", action="store_true")
  args = parser.parse_args()

  backend = args.backend.resolve()
  python_exe = _python_executable(args.python_exe)
  command = _build_pytest_command(args, python_exe)

  env = os.environ.copy()
  env["PULLBACK_GRID_RERUN_REAL_RUN_ID"] = args.run_id
  env["PULLBACK_GRID_RERUN_BACKTEST_START_TIME"] = args.start
  env["PULLBACK_GRID_RERUN_BACKTEST_END_TIME"] = args.end

  print(f"Backend: {backend}")
  print(f"Python: {python_exe}")
  print(f"Run ID: {args.run_id}")
  print(f"Window: {args.start} -> {args.end}")
  print("Command:")
  print(" ".join(f'"{part}"' if " " in part else part for part in command))

  if args.dry_run:
    return 0

  return_code = 0
  if not args.summary_only:
    completed = subprocess.run(command, cwd=backend, env=env, check=False)
    return_code = completed.returncode

  sys.path.insert(0, str(backend))
  old_cwd = Path.cwd()
  os.chdir(backend)
  try:
    summary = asyncio.run(_query_latest_summary(args.run_id))
    _print_summary(summary)
  finally:
    os.chdir(old_cwd)

  return return_code


if __name__ == "__main__":
  raise SystemExit(main())
