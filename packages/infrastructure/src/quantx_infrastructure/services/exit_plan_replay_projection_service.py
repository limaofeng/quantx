"""Durable exit-plan replay lifecycle and lightweight update notices."""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.models.exit_plan_replay_projection import (
  ExitPlanReplayProjection,
)
from quantx_infrastructure.repositories.exit_plan_replay_projection_repository import (
  ExitPlanReplayProjectionRepository,
)

logger = logging.getLogger(__name__)
EXIT_PLAN_REPLAY_UPDATE_CHANNEL_PREFIX = "exit-plan:replay:update:"
TERMINAL_EXIT_PLAN_REPLAY_STATUSES = frozenset(
  {"COMPLETED", "ERROR", "FAILED", "CANCELLED", "STOPPED"}
)


class ExitPlanReplayUpdateKind(str, Enum):
  CREATED = "CREATED"
  STATUS_CHANGED = "STATUS_CHANGED"
  PROGRESS = "PROGRESS"
  RESULT_READY = "RESULT_READY"


def exit_plan_replay_update_channel(account_id: str) -> str:
  return f"{EXIT_PLAN_REPLAY_UPDATE_CHANNEL_PREFIX}{str(account_id or '').strip()}"


def _snapshot(row: ExitPlanReplayProjection) -> Dict[str, Any]:
  return {
    "run_id": row.run_id,
    "account_id": row.account_id,
    "plan_id": row.plan_id,
    "instrument_code": row.instrument_code,
    "status": row.status,
    "progress_pct": float(row.progress_pct or 0.0),
    "processed_until": row.processed_until,
    "revision": str(row.revision or 0),
    "created_at": row.created_at,
    "updated_at": row.updated_at,
  }


class ExitPlanReplayProjectionService:
  async def get(self, run_id: str) -> Optional[Dict[str, Any]]:
    async for db in get_async_db():
      row = await ExitPlanReplayProjectionRepository(db).get(run_id)
      return _snapshot(row) if row else None
    return None

  async def list_by_account(self, account_id: str, limit: int) -> List[Dict[str, Any]]:
    async for db in get_async_db():
      rows = await ExitPlanReplayProjectionRepository(db).list_by_account(
        str(account_id or "").strip(), max(1, min(int(limit or 20), 100))
      )
      return [_snapshot(row) for row in rows]
    return []

  async def has_active(self, account_id: str) -> bool:
    async for db in get_async_db():
      return await ExitPlanReplayProjectionRepository(db).has_active(
        str(account_id or "").strip()
      )
    return False

  async def create(
    self,
    *,
    run_id: str,
    account_id: str,
    plan_id: Optional[str],
    instrument_code: str,
  ) -> Dict[str, Any]:
    return await self.update(
      run_id=run_id,
      account_id=account_id,
      plan_id=plan_id,
      instrument_code=instrument_code,
      status="PENDING",
      progress_pct=0.0,
      kind=ExitPlanReplayUpdateKind.CREATED,
    )

  async def update(
    self,
    *,
    run_id: str,
    account_id: str,
    status: str,
    plan_id: Optional[str] = None,
    instrument_code: str = "",
    progress_pct: Optional[float] = None,
    processed_until: Optional[datetime] = None,
    kind: ExitPlanReplayUpdateKind = ExitPlanReplayUpdateKind.STATUS_CHANGED,
  ) -> Dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    normalized_account_id = str(account_id or "").strip()
    normalized_status = str(getattr(status, "value", status) or "PENDING").upper()
    normalized_code = str(instrument_code or "").strip().upper()
    if not normalized_run_id or not normalized_account_id:
      raise ValueError("回放运行和账户不能为空")
    normalized_processed_until = (
      time_utils.to_shanghai(processed_until)
      if processed_until is not None and processed_until.tzinfo
      else processed_until
    )
    changed = False
    snapshot: Dict[str, Any] = {}
    async for db in get_async_db():
      repo = ExitPlanReplayProjectionRepository(db)
      row = await repo.get(normalized_run_id, for_update=True)
      if row is None:
        if not normalized_code:
          raise ValueError("新建回放投影必须提供证券代码")
        row = ExitPlanReplayProjection(
          run_id=normalized_run_id,
          account_id=normalized_account_id,
          plan_id=str(plan_id or "").strip() or None,
          instrument_code=normalized_code,
          status=normalized_status,
          progress_pct=self._normalized_progress(normalized_status, progress_pct),
          processed_until=normalized_processed_until,
          revision=1,
        )
        db.add(row)
        changed = True
      else:
        if row.account_id != normalized_account_id:
          raise ValueError("回放运行不属于指定账户")
        current_status = str(row.status or "PENDING").upper()
        if (
          current_status in TERMINAL_EXIT_PLAN_REPLAY_STATUSES
          and current_status != normalized_status
          and not (current_status == "STOPPED" and normalized_status == "CANCELLED")
        ):
          return _snapshot(row)
        next_progress = max(
          float(row.progress_pct or 0.0),
          self._normalized_progress(normalized_status, progress_pct),
        )
        next_processed = row.processed_until
        if normalized_processed_until is not None and (
          next_processed is None or normalized_processed_until > next_processed
        ):
          next_processed = normalized_processed_until
        changed = any(
          (
            row.status != normalized_status,
            abs(float(row.progress_pct or 0.0) - next_progress) >= 0.001,
            row.processed_until != next_processed,
          )
        )
        if changed:
          row.status = normalized_status
          row.progress_pct = next_progress
          row.processed_until = next_processed
          row.revision = int(row.revision or 0) + 1
      await db.commit()
      await db.refresh(row)
      snapshot = _snapshot(row)
      break
    if changed:
      try:
        await redis_pubsub.publish(
          exit_plan_replay_update_channel(normalized_account_id),
          {
            "account_id": normalized_account_id,
            "run_id": normalized_run_id,
            "revision": snapshot["revision"],
            "kind": kind.value,
            "occurred_at": snapshot.get("updated_at") or time_utils.now(),
          },
        )
      except Exception as exc:
        logger.warning("卖出计划回放通知失败: run=%s error=%s", normalized_run_id, exc)
    return snapshot

  async def subscribe(self, account_id: str) -> AsyncIterator[Dict[str, Any]]:
    normalized = str(account_id or "").strip()
    subscription = await redis_pubsub.open_subscription(
      exit_plan_replay_update_channel(normalized)
    )
    try:
      async for message in subscription.messages():
        if str(message.get("account_id") or "") == normalized:
          yield message
    finally:
      await subscription.close()

  @staticmethod
  def _normalized_progress(status: str, value: Optional[float]) -> float:
    if status == "COMPLETED":
      return 100.0
    try:
      progress = float(value or 0.0)
    except (TypeError, ValueError):
      progress = 0.0
    return max(0.0, min(99.9, progress))


exit_plan_replay_projection_service = ExitPlanReplayProjectionService()
