"""
Real integration test for rerunning an existing backtest version.

This test intentionally uses the project database configured by backend/.env and
backend/.env.testing. It does not create a temporary SQLite database and does
not mock repositories.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import select

from config.settings import settings
from core.strategy_executor import ExecutionStatus
from database.connection import get_async_db
from gqlapi.resolvers.strategies import strategy_manager
from gqlapi.schema import schema
from models.strategy_backtest import StrategyBacktest
from models.strategy_run import StrategyRun
from repositories.backtest_repository import BacktestRepository


TARGET_RUN_ID = os.getenv(
  "PULLBACK_GRID_RERUN_REAL_RUN_ID",
  "632958c3-751f-4862-80ab-b61ca30c0a8a",
)
TARGET_BACKTEST_START_TIME = os.getenv(
  "PULLBACK_GRID_RERUN_BACKTEST_START_TIME",
  "2026-04-14 00:00:00",
)
TARGET_BACKTEST_END_TIME = os.getenv(
  "PULLBACK_GRID_RERUN_BACKTEST_END_TIME",
  "2026-05-14 23:59:59",
)

HISTORY_QUERY = """
query BacktestHistory($runId: String!) {
  backtestHistory(runId: $runId) {
    id
    strategyRunId
    version
    status
    backtestStartTime
    backtestEndTime
    createdAt
  }
}
"""

RERUN_MUTATION = """
mutation RerunBacktestVersion(
  $runId: String!
  $backtestStartTime: DateTime
  $backtestEndTime: DateTime
) {
  rerunBacktestVersion(
    runId: $runId
    backtestStartTime: $backtestStartTime
    backtestEndTime: $backtestEndTime
  ) {
    id
    strategyRunId
    version
    status
    backtestStartTime
    backtestEndTime
    createdAt
  }
}
"""


def _enum_value(value: Any) -> str:
  if hasattr(value, "value"):
    return str(value.value).lower()
  return str(value).lower()


def _params_dict(value: Any) -> Dict[str, Any]:
  if isinstance(value, dict):
    return dict(value)
  if isinstance(value, str):
    try:
      parsed = json.loads(value)
      return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
      return {}
  return {}


def _parse_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value
  if isinstance(value, str) and value:
    try:
      normalized = value.strip()
      if len(normalized) == 8 and normalized.isdigit():
        return datetime.strptime(normalized, "%Y%m%d")
      return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
      return None
  return None


def _configured_backtest_window() -> tuple[datetime, datetime]:
  start_time = _parse_datetime(TARGET_BACKTEST_START_TIME)
  end_time = _parse_datetime(TARGET_BACKTEST_END_TIME)
  assert start_time and end_time, (
    "重新回测时间范围配置无效: "
    f"{TARGET_BACKTEST_START_TIME} ~ {TARGET_BACKTEST_END_TIME}"
  )
  assert end_time >= start_time, "重新回测结束时间早于开始时间"
  return start_time, end_time


async def _assert_real_postgres_database() -> None:
  database_url = settings.database_url.lower()
  assert database_url.startswith("postgresql+asyncpg://"), (
    "真实重新回测集成测试必须使用项目 PostgreSQL asyncpg 数据库"
  )
  assert "mysql" not in database_url and "sqlite" not in database_url

  async for db in get_async_db():
    await db.execute(select(1))
    return

  raise AssertionError("无法创建真实数据库会话")


async def _get_run_by_id(run_id: str) -> StrategyRun:
  async for db in get_async_db():
    result = await db.execute(select(StrategyRun).filter(StrategyRun.id == run_id))
    run = result.scalar_one_or_none()
    if run:
      return run
  raise AssertionError(f"未找到指定策略实例: {run_id}")


async def _get_history_records(run_id: str) -> List[StrategyBacktest]:
  async for db in get_async_db():
    repo = BacktestRepository(db)
    return await repo.get_backtests_by_run(run_id)
  raise AssertionError("无法读取回测历史")


async def _get_backtest(backtest_id: str) -> StrategyBacktest:
  async for db in get_async_db():
    repo = BacktestRepository(db)
    backtest = await repo.get_backtest(backtest_id)
    if backtest:
      return backtest
  raise AssertionError(f"未找到新回测版本: {backtest_id}")


async def _graphql_history(run_id: str) -> List[Dict[str, Any]]:
  result = await schema.execute(HISTORY_QUERY, variable_values={"runId": run_id})
  assert not result.errors, result.errors
  return list(result.data["backtestHistory"])


async def _wait_for_runtime_task(run_id: str, timeout_seconds: float = 300.0):
  deadline = asyncio.get_running_loop().time() + timeout_seconds
  while asyncio.get_running_loop().time() < deadline:
    runtime = strategy_manager.get_run(run_id)
    if runtime and runtime.task is not None:
      return runtime
    if runtime and runtime.status == ExecutionStatus.ERROR:
      raise AssertionError(runtime.error_message or "后台启动回测任务失败")
    await asyncio.sleep(0.5)
  raise AssertionError("重新回测后台启动超时")


def _choose_backtest_window(
  run: StrategyRun,
  history: List[StrategyBacktest],
) -> tuple[datetime, datetime]:
  latest = history[0]
  start_time = latest.backtest_start_time
  end_time = latest.backtest_end_time

  if not start_time or not end_time:
    params = _params_dict(run.parameters)
    start_time = start_time or _parse_datetime(
      params.get("backtestStartTime")
      or params.get("backtest_start_time")
      or params.get("startTime")
      or params.get("start_time")
    )
    end_time = end_time or _parse_datetime(
      params.get("backtestEndTime")
      or params.get("backtest_end_time")
      or params.get("endTime")
      or params.get("end_time")
    )

  assert start_time and end_time, "指定实例缺少可复用的回测时间范围"
  assert end_time >= start_time, "指定实例的回测结束时间早于开始时间"
  return start_time, end_time


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.graphql
@pytest.mark.asyncio
async def test_rerun_backtest_version_creates_history_for_specific_run():
  """Rerun the specified real strategy instance and verify history grows."""
  await _assert_real_postgres_database()

  run = await _get_run_by_id(TARGET_RUN_ID)
  assert _enum_value(run.mode) == "backtest", "指定实例必须是回测实例"
  assert _enum_value(run.status) not in {"running", "pending"}, (
    f"指定实例当前状态不允许重新回测: {run.status}"
  )

  before_records = await _get_history_records(TARGET_RUN_ID)
  assert before_records, "重新回测前必须已有至少一个历史版本"
  before_versions = {record.version for record in before_records}
  before_max_version = max(before_versions)
  start_time, end_time = _configured_backtest_window()

  before_history = await _graphql_history(TARGET_RUN_ID)
  assert len(before_history) == len(before_records)

  mutation_result = await schema.execute(
    RERUN_MUTATION,
    variable_values={
      "runId": TARGET_RUN_ID,
      "backtestStartTime": start_time.isoformat(),
      "backtestEndTime": end_time.isoformat(),
    },
  )
  assert not mutation_result.errors, mutation_result.errors

  created = mutation_result.data["rerunBacktestVersion"]
  assert created["strategyRunId"] == TARGET_RUN_ID
  assert created["version"] == before_max_version + 1
  assert created["id"]

  after_history = await _graphql_history(TARGET_RUN_ID)
  after_ids = {item["id"] for item in after_history}
  after_versions = {item["version"] for item in after_history}
  assert created["id"] in after_ids
  assert len(after_history) == len(before_history) + 1
  assert after_versions == before_versions | {before_max_version + 1}
  assert len(after_history) >= 2

  runtime = await _wait_for_runtime_task(TARGET_RUN_ID)
  assert runtime is not None, "重新回测必须加载真实运行时"
  assert runtime.task is not None, "重新回测必须启动真实回测任务"
  await runtime.task
  await asyncio.sleep(0.2)

  runtime = strategy_manager.get_run(TARGET_RUN_ID)
  assert runtime is not None
  assert runtime.error_message is None, runtime.error_message
  assert runtime.status == ExecutionStatus.COMPLETED

  completed_backtest = await _get_backtest(created["id"])
  assert completed_backtest.status == "COMPLETED"
