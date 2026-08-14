"""Durable projections and wake-up notifications for the board assistant."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, AsyncIterator, Dict, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.models.limit_up_board_assistant import (
  LimitUpBoardAssistantProjection,
)

logger = logging.getLogger(__name__)
UPDATE_CHANNEL_PREFIX = "limit-up-board-assistant:update:"


def update_channel(account_id: str) -> str:
  return f"{UPDATE_CHANNEL_PREFIX}{str(account_id or '').strip()}"


def _json_value(value: Any) -> Any:
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, dict):
    return {str(key): _json_value(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_json_value(item) for item in value]
  return value


class LimitUpBoardAssistantProjectionService:
  async def get(self, account_id: str) -> Optional[Dict[str, Any]]:
    async for db in get_async_db():
      row = await db.get(LimitUpBoardAssistantProjection, account_id)
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
      row = await db.get(LimitUpBoardAssistantProjection, normalized_account_id)
      if row is None:
        row = LimitUpBoardAssistantProjection(
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
          update_channel(normalized_account_id),
          {
            "account_id": normalized_account_id,
            "version": str(version),
            "occurred_at": generated_at,
          },
        )
      except Exception as exc:
        logger.warning(
          "Board assistant projection notification failed: account=%s error=%s",
          normalized_account_id,
          exc,
        )
    return result

  async def subscribe(self, account_id: str) -> AsyncIterator[Dict[str, Any]]:
    subscription = await redis_pubsub.open_subscription(update_channel(account_id))
    try:
      async for message in subscription.messages():
        if str(message.get("account_id") or "") == account_id:
          yield message
    finally:
      await subscription.close()


limit_up_board_assistant_projection_service = (
  LimitUpBoardAssistantProjectionService()
)
