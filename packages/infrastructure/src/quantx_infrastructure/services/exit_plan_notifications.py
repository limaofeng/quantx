"""Best-effort exit-plan update notifications emitted after DB commit."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord

logger = logging.getLogger(__name__)
EXIT_PLAN_UPDATE_CHANNEL = "exit-plan:updates:v1"
_SESSION_INFO_KEY = "quantx_exit_plan_updates"
_installed = False


async def _publish_updates(updates: list[dict[str, Any]]) -> None:
  for update in updates:
    try:
      await redis_pubsub.publish(EXIT_PLAN_UPDATE_CHANNEL, update)
    except Exception as exc:
      logger.warning(
        "Exit-plan update notification unavailable: error=%s",
        exc.__class__.__name__,
      )


def install_exit_plan_notification_hooks() -> None:
  global _installed
  if _installed:
    return
  _installed = True

  @event.listens_for(Session, "before_commit")
  def collect_updates(session: Session) -> None:
    updates: dict[str, dict[str, Any]] = {}
    for value in session.new.union(session.dirty).union(session.deleted):
      if not isinstance(value, AutoExitPlanRecord):
        continue
      plan_id = str(value.plan_id or "")
      if not plan_id:
        continue
      updates[plan_id] = {
        "plan_id": plan_id,
        "account_id": str(value.account_id or ""),
        "instrument_code": str(value.instrument_code or "").upper(),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
      }
    if updates:
      session.info[_SESSION_INFO_KEY] = list(updates.values())

  @event.listens_for(Session, "after_commit")
  def publish_updates(session: Session) -> None:
    updates = list(session.info.pop(_SESSION_INFO_KEY, []))
    if not updates:
      return
    try:
      loop = asyncio.get_running_loop()
    except RuntimeError:
      return
    loop.create_task(_publish_updates(updates))

  @event.listens_for(Session, "after_rollback")
  def clear_updates(session: Session) -> None:
    session.info.pop(_SESSION_INFO_KEY, None)
