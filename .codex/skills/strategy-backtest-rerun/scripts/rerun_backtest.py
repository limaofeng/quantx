"""Rerun and summarize one existing backtest in the same isolated test process."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

TEST_PATH = "tests/engine/integration/strategies/test_backtest_rerun_real.py"


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[4]


def _json_default(value: Any) -> str:
  return str(getattr(value, "value", value))


def _configure_test_environment() -> None:
  from sqlalchemy.engine import make_url

  raw = os.environ.get("QUANTX_TEST_DATABASE_URL", "").strip()
  if not raw:
    raise ValueError(
      "Set QUANTX_TEST_DATABASE_URL to the intended dedicated test database"
    )
  try:
    url = make_url(raw)
  except Exception:
    raise ValueError("Invalid test database URL") from None
  name = url.database or ""
  if url.drivername != "postgresql+asyncpg" or not (
    name.startswith("test_") or name.endswith("_test")
  ):
    raise ValueError("Use an asyncpg PostgreSQL database named test_* or *_test")
  override = os.environ.get("QUANTX_TEST_DATABASE_NAME", "").strip()
  if override and override != name:
    raise ValueError("QUANTX_TEST_DATABASE_NAME conflicts with the explicit test URL")
  os.environ["DATABASE_URL"] = raw
  os.environ["ENV"] = "testing"
  os.environ["ENABLE_REAL_TRADING"] = "false"
  os.environ["QMT_REAL_TRADING_ENABLED"] = "false"


async def _query_latest_summary(run_id: str) -> dict[str, Any]:
  from quantx_infrastructure.models.strategy_backtest import StrategyBacktest
  from quantx_infrastructure.models.strategy_performance_sample import (
    StrategyPerformanceSample,
  )
  from quantx_infrastructure.models.strategy_run import StrategyRun
  from quantx_infrastructure.models.t_trade_replay_projection import (
    TTradeReplayProjection,
  )
  from sqlalchemy import func, select
  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
  from sqlalchemy.pool import NullPool

  # Pytest closes its event loops; pooled asyncpg connections cannot cross loops.
  # Open summary connections independently against the same selected database.
  engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    latest_result = await db.execute(
      select(StrategyBacktest)
      .where(StrategyBacktest.strategy_run_id == run_id)
      .order_by(StrategyBacktest.version.desc())
      .limit(1)
    )
    latest = latest_result.scalar_one_or_none()
    run_result = await db.execute(select(StrategyRun).where(StrategyRun.id == run_id))
    run = run_result.scalar_one_or_none()
    projection_result = await db.execute(
      select(TTradeReplayProjection).where(TTradeReplayProjection.run_id == run_id)
    )
    projection = projection_result.scalar_one_or_none()

    temp_samples = 0
    if latest:
      sample_result = await db.execute(
        select(func.count())
        .select_from(StrategyPerformanceSample)
        .where(StrategyPerformanceSample.backtest_id == latest.id)
      )
      temp_samples = int(sample_result.scalar_one())

    metrics = latest.metrics if latest and isinstance(latest.metrics, dict) else {}
    return {
      "run_id": run_id,
      "run_status": getattr(run.status, "value", run.status) if run else None,
      "run_mode": getattr(run.mode, "value", run.mode) if run else None,
      "run_error_message": run.error_message if run else None,
      "run_instruments": list(run.instruments or []) if run else [],
      "replay_projection": (
        {
          "status": projection.status,
          "progress_pct": projection.progress_pct,
          "phase": projection.phase,
          "phase_progress_pct": projection.phase_progress_pct,
          "phase_message": projection.phase_message,
          "data_preparation": projection.data_preparation,
        }
        if projection
        else None
      ),
      "latest_backtest_id": latest.id if latest else None,
      "latest_version": latest.version if latest else None,
      "latest_status": latest.status if latest else None,
      "latest_error_message": latest.error_message if latest else None,
      "backtest_start_time": (
        latest.backtest_start_time.isoformat()
        if latest and latest.backtest_start_time
        else None
      ),
      "backtest_end_time": (
        latest.backtest_end_time.isoformat()
        if latest and latest.backtest_end_time
        else None
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


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--run-id", type=UUID, required=True)
  parser.add_argument("--start", help="ISO start time; supply with --end")
  parser.add_argument("--end", help="ISO end time; omit both to preserve the window")
  modes = parser.add_mutually_exclusive_group()
  modes.add_argument("--dry-run", action="store_true")
  modes.add_argument("--summary-only", action="store_true")
  args = parser.parse_args(argv)
  if bool(args.start) != bool(args.end):
    parser.error("Supply --start and --end together")
  if args.summary_only and args.start:
    parser.error("Summary-only does not accept window changes")
  if args.start:
    from datetime import datetime

    try:
      start, end = (
        datetime.fromisoformat(v.replace("Z", "+00:00")) for v in (args.start, args.end)
      )
      if end < start:
        raise ValueError()
    except (ValueError, TypeError):
      parser.error(
        "Use valid ISO timestamps with matching timezone conventions and end >= start"
      )
  repo_root = _repo_root()
  run_id = str(args.run_id)
  pytest_args = [
    TEST_PATH,
    "-q",
    "--quantx-run-e2e",
    "-p",
    "no:cacheprovider",
    "--basetemp",
    str(repo_root / ".codex_screenshots" / "backtest-rerun" / uuid4().hex),
  ]
  print(f"Repository root: {repo_root}")
  print(f"Python: {sys.executable}")
  print(f"Run ID: {run_id}")
  print(
    f"Window: {args.start} -> {args.end}"
    if args.start
    else "Window: preserve latest version"
  )
  if args.dry_run:
    print("Execution requires QUANTX_TEST_DATABASE_URL; live trading remains disabled.")
    print("pytest " + " ".join(pytest_args))
    return 0
  try:
    _configure_test_environment()
  except ValueError as exc:
    print(str(exc), file=sys.stderr)
    return 2
  os.environ["PULLBACK_GRID_RERUN_REAL_RUN_ID"] = run_id
  for key, value in [
    ("PULLBACK_GRID_RERUN_BACKTEST_START_TIME", args.start),
    ("PULLBACK_GRID_RERUN_BACKTEST_END_TIME", args.end),
  ]:
    if value is None:
      os.environ.pop(key, None)
    else:
      os.environ[key] = value
  old_cwd = Path.cwd()
  os.chdir(repo_root)
  try:
    if not args.summary_only:
      import pytest

      code = int(pytest.main(pytest_args))
      if code:
        return code
    summary = asyncio.run(_query_latest_summary(run_id))
    _print_summary(summary)
    if summary.get("run_mode") != "backtest":
      print("Target is missing or is not a backtest run", file=sys.stderr)
      return 1
    if (
      not args.summary_only
      and _json_default(summary.get("latest_status")).lower() != "completed"
    ):
      print("Latest backtest did not complete", file=sys.stderr)
      return 1
    return 0
  except Exception as exc:
    # Connection exceptions can include credentials; expose only the error class.
    print(
      f"Backtest workflow failed ({type(exc).__name__}); inspect sanitized diagnostics",
      file=sys.stderr,
    )
    return 1
  finally:
    os.chdir(old_cwd)


if __name__ == "__main__":
  raise SystemExit(main())
