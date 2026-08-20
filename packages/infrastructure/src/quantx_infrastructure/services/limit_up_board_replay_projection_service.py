"""Durable coordination projection for account-level board replays."""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.models.limit_up_board_replay import (
  LimitUpBoardReplayJob,
  LimitUpBoardReplayScenario,
)
from quantx_infrastructure.repositories.limit_up_board_replay_repository import (
  LimitUpBoardReplayRepository,
)

logger = logging.getLogger(__name__)

LIMIT_UP_BOARD_REPLAY_UPDATE_CHANNEL_PREFIX = "limit-up-board:replay:update:"
TERMINAL_BOARD_REPLAY_STATUSES = frozenset({"COMPLETED", "CANCELLED", "ERROR"})


class LimitUpBoardReplayUpdateKind(str, Enum):
  CREATED = "CREATED"
  STATUS_CHANGED = "STATUS_CHANGED"
  PROGRESS = "PROGRESS"
  RESULT_READY = "RESULT_READY"


def limit_up_board_replay_update_channel(account_id: str) -> str:
  return f"{LIMIT_UP_BOARD_REPLAY_UPDATE_CHANNEL_PREFIX}{account_id.strip()}"


class LimitUpBoardReplayProjectionService:
  async def create_job(
    self,
    *,
    job_id: str,
    account_id: str,
    scenario_profile: str,
    request: dict[str, Any],
    dataset_fingerprint: str,
    config_fingerprint: str,
    input_manifest: dict[str, Any],
    data_quality: dict[str, Any],
  ) -> dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    normalized_account_id = str(account_id or "").strip()
    if not normalized_job_id or not normalized_account_id:
      raise ValueError("回放任务和账户不能为空")
    if not dataset_fingerprint or not config_fingerprint:
      raise ValueError("回放数据集和配置指纹不能为空")
    created = False
    async for db in get_async_db():
      repo = LimitUpBoardReplayRepository(db)
      row = await repo.get_job(normalized_job_id)
      if row is None:
        row = LimitUpBoardReplayJob(
          id=normalized_job_id,
          account_id=normalized_account_id,
          status="PENDING",
          progress_pct=0.0,
          revision=1,
          scenario_profile=str(scenario_profile or "STANDARD_V1").upper(),
          request=dict(request or {}),
          dataset_fingerprint=str(dataset_fingerprint),
          config_fingerprint=str(config_fingerprint),
          input_manifest=dict(input_manifest or {}),
          data_quality=dict(data_quality or {}),
        )
        db.add(row)
        try:
          await db.commit()
          await db.refresh(row)
          created = True
        except IntegrityError:
          await db.rollback()
          row = await repo.get_job(normalized_job_id)
      if row is None:
        raise RuntimeError("回放任务创建失败")
      self._validate_bound_job(
        row,
        account_id=normalized_account_id,
        scenario_profile=scenario_profile,
        request=request,
        dataset_fingerprint=dataset_fingerprint,
        config_fingerprint=config_fingerprint,
        input_manifest=input_manifest,
        data_quality=data_quality,
      )
      scenarios = await repo.list_scenarios(normalized_job_id)
      snapshot = _job_snapshot(row, scenarios)
      break
    if created:
      await self._publish(snapshot, LimitUpBoardReplayUpdateKind.CREATED)
    return snapshot

  async def bind_scenario(
    self,
    *,
    job_id: str,
    scenario_id: str,
    backtest_id: str,
    confirmation_delay_ms: int,
    participation_cap_pct: float,
    book_depth_participation_pct: float,
  ) -> dict[str, Any]:
    normalized_scenario_id = str(scenario_id or "").strip()
    normalized_backtest_id = str(backtest_id or "").strip()
    normalized_delay = int(confirmation_delay_ms)
    normalized_participation = float(participation_cap_pct)
    normalized_depth_participation = float(book_depth_participation_pct)
    if not normalized_scenario_id or not normalized_backtest_id:
      raise ValueError("回放成交情景和权威回测不能为空")
    if normalized_delay < 0:
      raise ValueError("确认延迟不能为负数")
    if not 0 < normalized_participation <= 1:
      raise ValueError("成交参与率必须在 (0, 1] 范围内")
    if not 0 < normalized_depth_participation <= 1:
      raise ValueError("五档盘口参与率必须在 (0, 1] 范围内")
    created = False
    async for db in get_async_db():
      repo = LimitUpBoardReplayRepository(db)
      job = await repo.get_job(job_id, for_update=True)
      if job is None:
        raise ValueError("打板回放任务不存在")
      if str(job.status).upper() in TERMINAL_BOARD_REPLAY_STATUSES:
        raise ValueError("终态回放任务不能再绑定成交情景")
      existing = (
        await db.execute(
          select(LimitUpBoardReplayScenario).where(
            LimitUpBoardReplayScenario.job_id == job_id,
            LimitUpBoardReplayScenario.scenario_id == normalized_scenario_id,
          )
        )
      ).scalar_one_or_none()
      if existing is None:
        existing = LimitUpBoardReplayScenario(
          job_id=job_id,
          scenario_id=normalized_scenario_id,
          backtest_id=normalized_backtest_id,
          status="PENDING",
          progress_pct=0.0,
          revision=1,
          confirmation_delay_ms=normalized_delay,
          participation_cap_pct=normalized_participation,
          book_depth_participation_pct=normalized_depth_participation,
        )
        db.add(existing)
        job.revision = int(job.revision or 0) + 1
        await db.commit()
        created = True
      else:
        expected = (
          normalized_backtest_id,
          normalized_delay,
          normalized_participation,
          normalized_depth_participation,
        )
        actual = (
          str(existing.backtest_id),
          int(existing.confirmation_delay_ms),
          float(existing.participation_cap_pct),
          float(existing.book_depth_participation_pct),
        )
        if actual != expected:
          raise ValueError("回放成交情景已绑定且参数不一致")
      scenarios = await repo.list_scenarios(job_id)
      snapshot = _job_snapshot(job, scenarios)
      break
    if created:
      await self._publish(snapshot, LimitUpBoardReplayUpdateKind.STATUS_CHANGED)
    return snapshot

  async def get(self, job_id: str) -> Optional[dict[str, Any]]:
    async for db in get_async_db():
      repo = LimitUpBoardReplayRepository(db)
      row = await repo.get_job(str(job_id or "").strip())
      if row is None:
        return None
      return _job_snapshot(row, await repo.list_scenarios(row.id))
    return None

  async def list_by_account(
    self,
    account_id: str,
    limit: int = 20,
  ) -> list[dict[str, Any]]:
    async for db in get_async_db():
      repo = LimitUpBoardReplayRepository(db)
      jobs = await repo.list_jobs(str(account_id or "").strip(), limit)
      result = []
      for row in jobs:
        result.append(_job_snapshot(row, await repo.list_scenarios(row.id)))
      return result
    return []

  async def has_active(self, account_id: str) -> bool:
    async for db in get_async_db():
      return await LimitUpBoardReplayRepository(db).has_active_job(account_id)
    return False

  async def update_job_error(
    self,
    *,
    job_id: str,
    error_message: str,
  ) -> dict[str, Any]:
    message = str(error_message or "").strip()
    if not message:
      raise ValueError("回放任务失败原因不能为空")
    return await self._finish_job(
      job_id=job_id,
      status="ERROR",
      error_message=message,
    )

  async def cancel_job(
    self,
    *,
    job_id: str,
    reason: Optional[str] = None,
  ) -> dict[str, Any]:
    return await self._finish_job(
      job_id=job_id,
      status="CANCELLED",
      error_message=str(reason or "").strip() or None,
    )

  async def _finish_job(
    self,
    *,
    job_id: str,
    status: str,
    error_message: Optional[str],
  ) -> dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
      raise ValueError("回放任务不能为空")
    changed = False
    async for db in get_async_db():
      repo = LimitUpBoardReplayRepository(db)
      job = await repo.get_job(normalized_job_id, for_update=True)
      if job is None:
        raise ValueError("打板回放任务不存在")
      current_status = str(job.status or "PENDING").upper()
      if current_status not in TERMINAL_BOARD_REPLAY_STATUSES:
        job.status = status
        job.error_message = (
          str(error_message)[:512] if error_message is not None else None
        )
        job.completed_at = _naive(time_utils.now())
        job.revision = int(job.revision or 0) + 1
        await db.commit()
        await db.refresh(job)
        changed = True
      snapshot = _job_snapshot(job, await repo.list_scenarios(job.id))
      break
    if changed:
      await self._publish(snapshot, LimitUpBoardReplayUpdateKind.RESULT_READY)
    return snapshot

  async def update_scenario(
    self,
    *,
    backtest_id: str,
    status: str,
    progress_pct: Optional[float] = None,
    processed_until: Optional[datetime] = None,
    error_message: Optional[str] = None,
    kind: LimitUpBoardReplayUpdateKind = LimitUpBoardReplayUpdateKind.STATUS_CHANGED,
  ) -> dict[str, Any]:
    normalized_status = str(getattr(status, "value", status) or "PENDING").upper()
    if normalized_status not in {
      "PENDING",
      "STARTING",
      "RUNNING",
      "COMPLETED",
      "CANCELLED",
      "ERROR",
    }:
      raise ValueError(f"不支持的打板回放状态: {normalized_status}")
    changed = False
    async for db in get_async_db():
      scenario = (
        await db.execute(
          select(LimitUpBoardReplayScenario)
          .where(LimitUpBoardReplayScenario.backtest_id == backtest_id)
          .with_for_update()
        )
      ).scalar_one_or_none()
      if scenario is None:
        raise ValueError("打板回放成交情景不存在")
      repo = LimitUpBoardReplayRepository(db)
      job = await repo.get_job(scenario.job_id, for_update=True)
      if job is None:
        raise ValueError("打板回放任务不存在")
      if str(job.status or "PENDING").upper() in TERMINAL_BOARD_REPLAY_STATUSES:
        return _job_snapshot(job, await repo.list_scenarios(job.id))
      current_status = str(scenario.status or "PENDING").upper()
      if current_status in TERMINAL_BOARD_REPLAY_STATUSES:
        if current_status != normalized_status:
          return _job_snapshot(job, await repo.list_scenarios(job.id))
      next_progress = _normalized_progress(
        normalized_status,
        max(float(scenario.progress_pct or 0.0), float(progress_pct or 0.0)),
      )
      next_processed = scenario.processed_until
      normalized_processed = _naive(processed_until)
      if normalized_processed is not None and (
        next_processed is None or normalized_processed > next_processed
      ):
        next_processed = normalized_processed
      changed = any(
        (
          current_status != normalized_status,
          abs(float(scenario.progress_pct or 0.0) - next_progress) >= 0.001,
          scenario.processed_until != next_processed,
          bool(error_message) and scenario.error_message != str(error_message),
        )
      )
      if changed:
        scenario.status = normalized_status
        scenario.progress_pct = next_progress
        scenario.processed_until = next_processed
        scenario.error_message = str(error_message)[:512] if error_message else None
        scenario.revision = int(scenario.revision or 0) + 1
        await db.flush()
        scenarios = await repo.list_scenarios(job.id)
        _converge_job(job, scenarios)
        await db.commit()
        await db.refresh(job)
      scenarios = await repo.list_scenarios(job.id)
      snapshot = _job_snapshot(job, scenarios)
      break
    if changed:
      await self._publish(snapshot, kind)
    return snapshot

  async def subscribe(self, account_id: str) -> AsyncIterator[dict[str, Any]]:
    normalized = str(account_id or "").strip()
    subscription = await redis_pubsub.open_subscription(
      limit_up_board_replay_update_channel(normalized)
    )
    try:
      async for message in subscription.messages():
        if str(message.get("account_id") or "") == normalized:
          yield message
    finally:
      await subscription.close()

  async def _publish(
    self,
    snapshot: dict[str, Any],
    kind: LimitUpBoardReplayUpdateKind,
  ) -> None:
    try:
      await redis_pubsub.publish(
        limit_up_board_replay_update_channel(snapshot["account_id"]),
        {
          "account_id": snapshot["account_id"],
          "job_id": snapshot["job_id"],
          "revision": snapshot["revision"],
          "kind": kind.value,
          "occurred_at": snapshot.get("updated_at") or time_utils.now(),
        },
      )
    except Exception as exc:
      logger.warning(
        "Board replay notification failed: job=%s error=%s",
        snapshot.get("job_id"),
        exc,
      )

  @staticmethod
  def _validate_bound_job(
    row: LimitUpBoardReplayJob,
    *,
    account_id: str,
    scenario_profile: str,
    request: dict[str, Any],
    dataset_fingerprint: str,
    config_fingerprint: str,
    input_manifest: dict[str, Any],
    data_quality: dict[str, Any],
  ) -> None:
    if row.account_id != account_id:
      raise ValueError("回放任务不属于指定账户")
    if row.dataset_fingerprint != dataset_fingerprint:
      raise ValueError("回放任务已绑定其他历史数据集")
    if row.config_fingerprint != config_fingerprint:
      raise ValueError("回放任务已绑定其他助手配置")
    if row.scenario_profile != str(scenario_profile or "STANDARD_V1").upper():
      raise ValueError("回放任务已绑定其他成交情景配置")
    if dict(row.request or {}) != dict(request or {}):
      raise ValueError("回放任务已绑定其他请求快照")
    if dict(row.input_manifest or {}) != dict(input_manifest or {}):
      raise ValueError("回放任务已绑定其他输入 manifest")
    if dict(row.data_quality or {}) != dict(data_quality or {}):
      raise ValueError("回放任务已绑定其他数据质量快照")


def _converge_job(
  job: LimitUpBoardReplayJob,
  scenarios: list[LimitUpBoardReplayScenario],
) -> None:
  statuses = [str(item.status or "PENDING").upper() for item in scenarios]
  if not statuses:
    next_status = "PENDING"
  elif all(status == "COMPLETED" for status in statuses):
    next_status = "COMPLETED"
  elif all(status in TERMINAL_BOARD_REPLAY_STATUSES for status in statuses):
    next_status = (
      "CANCELLED" if all(status == "CANCELLED" for status in statuses) else "ERROR"
    )
  elif any(status == "RUNNING" for status in statuses):
    next_status = "RUNNING"
  elif any(status == "STARTING" for status in statuses):
    next_status = "STARTING"
  else:
    next_status = "PENDING"
  progress = (
    sum(float(item.progress_pct or 0.0) for item in scenarios) / len(scenarios)
    if scenarios
    else 0.0
  )
  processed = [item.processed_until for item in scenarios if item.processed_until]
  job.status = next_status
  job.progress_pct = 100.0 if next_status == "COMPLETED" else min(99.9, progress)
  job.processed_until = min(processed) if processed else None
  job.started_at = job.started_at or (
    time_utils.now() if next_status in {"STARTING", "RUNNING"} else None
  )
  if next_status in TERMINAL_BOARD_REPLAY_STATUSES:
    job.completed_at = job.completed_at or time_utils.now()
  errors = [item.error_message for item in scenarios if item.error_message]
  job.error_message = "；".join(errors)[:512] if errors else None
  job.revision = int(job.revision or 0) + 1


def _scenario_snapshot(row: LimitUpBoardReplayScenario) -> dict[str, Any]:
  return {
    "scenario_id": row.scenario_id,
    "backtest_id": row.backtest_id,
    "status": row.status,
    "progress_pct": float(row.progress_pct or 0.0),
    "processed_until": row.processed_until,
    "revision": str(row.revision or 0),
    "error_message": row.error_message,
    "confirmation_delay_ms": int(row.confirmation_delay_ms or 0),
    "participation_cap_pct": float(row.participation_cap_pct or 0.0),
    "book_depth_participation_pct": float(
      row.book_depth_participation_pct or 0.0
    ),
  }


def _job_snapshot(
  row: LimitUpBoardReplayJob,
  scenarios: list[LimitUpBoardReplayScenario],
) -> dict[str, Any]:
  return {
    "job_id": row.id,
    "account_id": row.account_id,
    "status": row.status,
    "progress_pct": float(row.progress_pct or 0.0),
    "processed_until": row.processed_until,
    "revision": str(row.revision or 0),
    "scenario_profile": row.scenario_profile,
    "request": dict(row.request or {}),
    "dataset_fingerprint": row.dataset_fingerprint,
    "config_fingerprint": row.config_fingerprint,
    "input_manifest": dict(row.input_manifest or {}),
    "data_quality": dict(row.data_quality or {}),
    "error_message": row.error_message,
    "started_at": row.started_at,
    "completed_at": row.completed_at,
    "created_at": row.created_at,
    "updated_at": row.updated_at,
    "scenarios": [_scenario_snapshot(item) for item in scenarios],
  }


def _normalized_progress(status: str, value: float) -> float:
  if status == "COMPLETED":
    return 100.0
  return max(0.0, min(99.9, float(value or 0.0)))


def _naive(value: Optional[datetime]) -> Optional[datetime]:
  if value is None:
    return None
  return value.replace(tzinfo=None) if value.tzinfo else value


limit_up_board_replay_projection_service = LimitUpBoardReplayProjectionService()
