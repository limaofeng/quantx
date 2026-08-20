"""Durable replay lifecycle state and lightweight Redis wake-up notices."""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.models.t_trade_replay_projection import (
  TTradeReplayProjection,
)
from quantx_infrastructure.repositories.t_trade_replay_projection_repository import (
  TTradeReplayProjectionRepository,
)

logger = logging.getLogger(__name__)
T_TRADE_REPLAY_UPDATE_CHANNEL_PREFIX = "t-trade:replay:update:"
TERMINAL_REPLAY_STATUSES = frozenset(
  {"COMPLETED", "ERROR", "FAILED", "CANCELLED", "STOPPED"}
)


class TTradeReplayUpdateKind(str, Enum):
  CREATED = "CREATED"
  STATUS_CHANGED = "STATUS_CHANGED"
  PROGRESS = "PROGRESS"
  RESULT_READY = "RESULT_READY"


def t_trade_replay_update_channel(account_id: str) -> str:
  return (
    f"{T_TRADE_REPLAY_UPDATE_CHANNEL_PREFIX}"
    f"{str(account_id or '').strip()}"
  )


def _snapshot(row: TTradeReplayProjection) -> Dict[str, Any]:
  return {
    "run_id": row.run_id,
    "account_id": row.account_id,
    "status": row.status,
    "progress_pct": float(row.progress_pct or 0.0),
    "processed_until": row.processed_until,
    "revision": str(row.revision or 0),
    "created_at": row.created_at,
    "updated_at": row.updated_at,
  }


class TTradeReplayProjectionService:
  async def get(self, run_id: str) -> Optional[Dict[str, Any]]:
    async for db in get_async_db():
      row = await TTradeReplayProjectionRepository(db).get(run_id)
      return _snapshot(row) if row else None
    return None

  async def list_by_account(
    self,
    account_id: str,
    limit: int,
  ) -> List[Dict[str, Any]]:
    async for db in get_async_db():
      rows = await TTradeReplayProjectionRepository(db).list_by_account(
        str(account_id or "").strip(),
        limit,
      )
      return [_snapshot(row) for row in rows]
    return []

  async def has_active(self, account_id: str) -> bool:
    async for db in get_async_db():
      return await TTradeReplayProjectionRepository(db).has_active(
        str(account_id or "").strip()
      )
    return False

  async def create(
    self,
    *,
    run_id: str,
    account_id: str,
    status: str = "PENDING",
  ) -> Dict[str, Any]:
    return await self.update(
      run_id=run_id,
      account_id=account_id,
      status=status,
      progress_pct=0.0,
      processed_until=None,
      kind=TTradeReplayUpdateKind.CREATED,
    )

  async def update(
    self,
    *,
    run_id: str,
    account_id: str,
    status: str,
    progress_pct: Optional[float] = None,
    processed_until: Optional[datetime] = None,
    kind: TTradeReplayUpdateKind = TTradeReplayUpdateKind.STATUS_CHANGED,
  ) -> Dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    normalized_account_id = str(account_id or "").strip()
    normalized_status = str(getattr(status, "value", status) or "PENDING").upper()
    normalized_processed_until = (
      time_utils.to_shanghai(processed_until)
      if processed_until is not None and processed_until.tzinfo
      else processed_until
    )
    if not normalized_run_id or not normalized_account_id:
      raise ValueError("回放运行和账户不能为空")

    changed = False
    snapshot: Dict[str, Any] = {}
    async for db in get_async_db():
      repo = TTradeReplayProjectionRepository(db)
      row = await repo.get(normalized_run_id, for_update=True)
      if row is None:
        row = TTradeReplayProjection(
          run_id=normalized_run_id,
          account_id=normalized_account_id,
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
        terminal_transition_allowed = (
          current_status == normalized_status
          or (
            current_status == "STOPPED"
            and normalized_status == "CANCELLED"
          )
        )
        if (
          current_status in TERMINAL_REPLAY_STATUSES
          and not terminal_transition_allowed
        ):
          snapshot = _snapshot(row)
          break
        next_progress = max(
          float(row.progress_pct or 0.0),
          self._normalized_progress(normalized_status, progress_pct),
        )
        next_processed_until = row.processed_until
        if normalized_processed_until is not None and (
          next_processed_until is None
          or normalized_processed_until > next_processed_until
        ):
          next_processed_until = normalized_processed_until
        changed = any(
          (
            row.status != normalized_status,
            abs(float(row.progress_pct or 0.0) - next_progress) >= 0.001,
            row.processed_until != next_processed_until,
          )
        )
        if changed:
          row.status = normalized_status
          row.progress_pct = next_progress
          row.processed_until = next_processed_until
          row.revision = int(row.revision or 0) + 1
      await db.commit()
      await db.refresh(row)
      snapshot = _snapshot(row)
      break

    if changed:
      occurred_at = snapshot.get("updated_at") or time_utils.now()
      try:
        await redis_pubsub.publish(
          t_trade_replay_update_channel(normalized_account_id),
          {
            "account_id": normalized_account_id,
            "run_id": normalized_run_id,
            "revision": snapshot["revision"],
            "kind": kind.value,
            "occurred_at": occurred_at,
          },
        )
      except Exception as exc:
        logger.warning(
          "T-trade replay notification failed: run=%s error=%s",
          normalized_run_id,
          exc,
        )
    return snapshot

  async def subscribe(self, account_id: str) -> AsyncIterator[Dict[str, Any]]:
    normalized_account_id = str(account_id or "").strip()
    subscription = await redis_pubsub.open_subscription(
      t_trade_replay_update_channel(normalized_account_id)
    )
    try:
      async for message in subscription.messages():
        if str(message.get("account_id") or "") == normalized_account_id:
          yield message
    finally:
      await subscription.close()

  @staticmethod
  def _normalized_progress(status: str, progress_pct: Optional[float]) -> float:
    if status == "COMPLETED":
      return 100.0
    try:
      progress = float(progress_pct or 0.0)
    except (TypeError, ValueError):
      progress = 0.0
    return max(0.0, min(99.9, progress))


t_trade_replay_projection_service = TTradeReplayProjectionService()
