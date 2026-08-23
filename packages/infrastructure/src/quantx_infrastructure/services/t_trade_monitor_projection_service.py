"""Persistence and wake-up notifications for T-trade monitor projections."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections import Counter
from datetime import date, datetime
from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy import select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.models.t_trade_global_monitor_projection import (
  TTradeGlobalMonitorProjection,
)

logger = logging.getLogger(__name__)
T_TRADE_UPDATE_CHANNEL_PREFIX = "t-trade:update:"


def t_trade_update_channel(account_id: str) -> str:
  return f"{T_TRADE_UPDATE_CHANNEL_PREFIX}{str(account_id or '').strip()}"


def _json_value(value: Any) -> Any:
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, dict):
    return {str(key): _json_value(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_json_value(item) for item in value]
  return value


class TTradeMonitorProjectionService:
  def __init__(self, *, opportunity_notice_window_seconds: float = 2.0) -> None:
    self._opportunity_notice_window_seconds = max(
      0.01,
      float(opportunity_notice_window_seconds),
    )
    self._pending_opportunity_notices: Dict[
      tuple[str, str, str], Dict[str, Any]
    ] = {}
    self._opportunity_notice_tasks: Dict[
      tuple[str, str, str], asyncio.Task[None]
    ] = {}
    self._opportunity_notice_lock = asyncio.Lock()
    self._opportunity_metrics: Counter[str] = Counter()

  def metrics_snapshot(self) -> Dict[str, Any]:
    """Return bounded process-local delivery metrics for the Engine heartbeat."""

    return {
      "schemaVersion": 1,
      "counters": dict(sorted(self._opportunity_metrics.items())),
      "pendingNoticeCount": len(self._pending_opportunity_notices),
      "activeNoticeTaskCount": sum(
        1 for task in self._opportunity_notice_tasks.values() if not task.done()
      ),
    }

  async def get(self, account_id: str) -> Optional[Dict[str, Any]]:
    async for db in get_async_db():
      row = await db.get(TTradeGlobalMonitorProjection, account_id)
      if row is None:
        return None
      return {
        **dict(row.payload or {}),
        "projection_version": str(row.version or 0),
        "projection_generated_at": row.generated_at,
      }
    return None

  async def save(self, account_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_account_id = str(account_id or "").strip()
    if not normalized_account_id:
      raise ValueError("账户不能为空")
    normalized = _json_value(
      {
        key: value
        for key, value in dict(payload or {}).items()
        if key not in {"projection_version", "projection_generated_at"}
      }
    )
    generated_at = time_utils.now()
    changed = False
    version = 0
    async for db in get_async_db():
      result = await db.execute(
        select(TTradeGlobalMonitorProjection)
        .where(TTradeGlobalMonitorProjection.account_id == normalized_account_id)
        .with_for_update()
      )
      row = result.scalar_one_or_none()
      if row is None:
        row = TTradeGlobalMonitorProjection(
          account_id=normalized_account_id,
          version=1,
          payload=normalized,
          generated_at=generated_at,
        )
        db.add(row)
        changed = True
      elif dict(row.payload or {}) != normalized:
        row.version = int(row.version or 0) + 1
        row.payload = normalized
        row.generated_at = generated_at
        changed = True
      version = int(row.version or 0)
      await db.commit()
      if not changed:
        generated_at = row.generated_at
      break

    result = {
      **normalized,
      "projection_version": str(version),
      "projection_generated_at": generated_at,
    }
    if changed:
      try:
        await redis_pubsub.publish(
          t_trade_update_channel(normalized_account_id),
          {
            "account_id": normalized_account_id,
            "version": str(version),
            "occurred_at": generated_at,
          },
        )
      except Exception as exc:
        logger.warning(
          "T-trade projection notification failed: account=%s error=%s",
          normalized_account_id,
          exc,
        )
    return result

  async def subscribe(self, account_id: str) -> AsyncIterator[Dict[str, Any]]:
    channel = t_trade_update_channel(account_id)
    subscription = await redis_pubsub.open_subscription(channel)
    try:
      async for message in subscription.messages():
        if str(message.get("account_id") or "") == account_id:
          yield message
    finally:
      await subscription.close()

  async def notify_opportunity(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    instrument_code: str,
    version: str,
    immediate: bool,
    session_patch: Dict[str, Any],
  ) -> bool:
    """Publish a lossy wake-up only after V3 truth is durable.

    MATERIAL transitions bypass the coalescer. Unchanged diagnostic snapshots
    use a trailing two-second window so the last durable state is never lost
    when the market stream becomes idle. The payload is intentionally only an
    identity/version notice; clients must refetch PostgreSQL-backed state.
    """

    normalized_account = str(account_id or "").strip()
    normalized_run = str(strategy_run_id or "").strip()
    normalized_instrument = str(instrument_code or "").strip().upper()
    normalized_version = str(version or "").strip()
    if not all(
      (
        normalized_account,
        normalized_run,
        normalized_instrument,
        normalized_version,
      )
    ):
      raise ValueError("做 T 机会更新通知缺少完整身份")
    key = (normalized_account, normalized_run, normalized_instrument)
    normalized_patch = _json_value(dict(session_patch or {}))
    if not isinstance(normalized_patch.get("signal_snapshot"), dict):
      raise ValueError("做 T 机会更新缺少完整 signal_snapshot 投影")
    payload = {
      "account_id": normalized_account,
      "strategy_run_id": normalized_run,
      "instrument_code": normalized_instrument,
      "version": normalized_version,
      "occurred_at": time_utils.now(),
      "_session_patch": normalized_patch,
    }
    self._opportunity_metrics["received_total"] += 1
    if immediate:
      self._opportunity_metrics["immediate_total"] += 1
      task: Optional[asyncio.Task[None]] = None
      async with self._opportunity_notice_lock:
        if self._pending_opportunity_notices.pop(key, None) is not None:
          self._opportunity_metrics["pending_cancelled_by_material_total"] += 1
        task = self._opportunity_notice_tasks.pop(key, None)
      await self._cancel_notice_tasks([task] if task is not None else [])
      return await self._publish_opportunity_notice(payload)

    async with self._opportunity_notice_lock:
      if key in self._pending_opportunity_notices:
        self._opportunity_metrics["coalesced_replacements_total"] += 1
      else:
        self._opportunity_metrics["coalesced_windows_total"] += 1
      self._pending_opportunity_notices[key] = payload
      task = self._opportunity_notice_tasks.get(key)
      if task is None or task.done():
        self._opportunity_notice_tasks[key] = asyncio.create_task(
          self._publish_pending_opportunity_notice(key),
          name=(
            "t-trade-opportunity-notice:"
            f"{normalized_account}:{normalized_run}:{normalized_instrument}"
          ),
        )
    return True

  async def flush_opportunity_notices(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
  ) -> int:
    """Flush trailing wake-ups at a deterministic runtime boundary."""

    normalized_account = str(account_id or "").strip()
    normalized_run = str(strategy_run_id or "").strip()
    if not normalized_account or not normalized_run:
      return 0
    pending: list[Dict[str, Any]] = []
    tasks: list[asyncio.Task[None]] = []
    async with self._opportunity_notice_lock:
      keys = sorted(
        key
        for key in self._pending_opportunity_notices
        if key[0] == normalized_account and key[1] == normalized_run
      )
      for key in keys:
        pending.append(self._pending_opportunity_notices.pop(key))
        task = self._opportunity_notice_tasks.pop(key, None)
        if task is not None:
          tasks.append(task)
    await self._cancel_notice_tasks(tasks)
    published = 0
    for payload in pending:
      if await self._publish_opportunity_notice(payload):
        published += 1
    self._opportunity_metrics["flush_published_total"] += published
    return published

  async def _publish_pending_opportunity_notice(
    self,
    key: tuple[str, str, str],
  ) -> None:
    try:
      await asyncio.sleep(self._opportunity_notice_window_seconds)
    except asyncio.CancelledError:
      return
    async with self._opportunity_notice_lock:
      payload = self._pending_opportunity_notices.pop(key, None)
      self._opportunity_notice_tasks.pop(key, None)
    if payload is not None:
      await self._publish_opportunity_notice(payload)

  @staticmethod
  async def _cancel_notice_tasks(tasks: list[asyncio.Task[None]]) -> None:
    current = asyncio.current_task()
    cancellable = [task for task in tasks if task is not current and not task.done()]
    for task in cancellable:
      task.cancel()
    if cancellable:
      await asyncio.gather(*cancellable, return_exceptions=True)

  async def _publish_opportunity_notice(self, payload: Dict[str, Any]) -> bool:
    try:
      projection_version = await self._persist_opportunity_projection(payload)
    except Exception as exc:
      self._opportunity_metrics["projection_failures_total"] += 1
      logger.warning(
        "T-trade opportunity projection failed before notification: "
        "account=%s run=%s instrument=%s error=%s",
        payload.get("account_id"),
        payload.get("strategy_run_id"),
        payload.get("instrument_code"),
        exc,
      )
      return False
    if projection_version is None:
      self._opportunity_metrics["projection_missing_total"] += 1
      logger.warning(
        "T-trade opportunity projection missing before notification: "
        "account=%s run=%s instrument=%s",
        payload.get("account_id"),
        payload.get("strategy_run_id"),
        payload.get("instrument_code"),
      )
      return False
    public_payload = {
      key: value for key, value in payload.items() if not str(key).startswith("_")
    }
    public_payload["projection_version"] = projection_version
    try:
      await redis_pubsub.publish(
        t_trade_update_channel(str(public_payload["account_id"])),
        public_payload,
      )
      self._opportunity_metrics["published_total"] += 1
      return True
    except Exception as exc:
      self._opportunity_metrics["publish_failures_total"] += 1
      logger.warning(
        "T-trade opportunity notification failed: account=%s run=%s "
        "instrument=%s error=%s",
        payload.get("account_id"),
        payload.get("strategy_run_id"),
        payload.get("instrument_code"),
        exc,
      )
      return False

  @staticmethod
  async def _persist_opportunity_projection(
    payload: Dict[str, Any],
  ) -> Optional[str]:
    """Patch the durable latest-read model before emitting its lossy wake-up."""

    account_id = str(payload.get("account_id") or "").strip()
    run_id = str(payload.get("strategy_run_id") or "").strip()
    instrument_code = str(payload.get("instrument_code") or "").strip().upper()
    session_patch = dict(payload.get("_session_patch") or {})
    generated_at = time_utils.now()
    async for db in get_async_db():
      result = await db.execute(
        select(TTradeGlobalMonitorProjection)
        .where(TTradeGlobalMonitorProjection.account_id == account_id)
        .with_for_update()
      )
      row = result.scalar_one_or_none()
      if row is None:
        return None
      projection = copy.deepcopy(dict(row.payload or {}))
      sessions = [
        dict(item) for item in list(projection.get("sessions") or []) if isinstance(item, dict)
      ]
      matched = False
      for index, session in enumerate(sessions):
        if (
          str(session.get("run_id") or "") == run_id
          and str(session.get("stock_code") or "").strip().upper()
          == instrument_code
        ):
          sessions[index] = {**session, **session_patch}
          matched = True
          break
      if not matched:
        return None
      projection["sessions"] = sessions
      projection["pending_signal_count"] = sum(
        1 for session in sessions if session.get("pending_entry_intent_id")
      )
      if projection != dict(row.payload or {}):
        row.payload = projection
        row.version = int(row.version or 0) + 1
        row.generated_at = generated_at
      await db.commit()
      return str(row.version or 0)
    return None


t_trade_monitor_projection_service = TTradeMonitorProjectionService()
