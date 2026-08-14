"""Append durable events before best-effort Redis notification."""

from __future__ import annotations

import logging
from typing import Any

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.ai_assistant_repository import (
  AiAssistantRepository,
)
from quantx_infrastructure.services.ai_assistant_event_bus import (
  notify_ai_assistant_event,
)

logger = logging.getLogger(__name__)


class AssistantEventWriter:
  async def append(
    self,
    *,
    thread_id: str,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
  ):
    async with AsyncSessionLocal() as db:
      event = await AiAssistantRepository(db).append_event(
        thread_id=thread_id,
        run_id=run_id,
        event_type=event_type,
        payload=payload,
      )
    try:
      await notify_ai_assistant_event(
        thread_id=thread_id,
        sequence=int(event.sequence),
      )
    except Exception as exc:
      logger.warning(
        "AI assistant event wake-up failed: %s",
        exc.__class__.__name__,
      )
    return event
