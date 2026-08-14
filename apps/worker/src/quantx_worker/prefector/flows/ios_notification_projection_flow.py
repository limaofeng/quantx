"""Worker-owned projection of durable business events into the APNs outbox."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from prefect import flow, get_run_logger
from quantx_infrastructure.config.settings import Settings, settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.services.ios_business_notification_projector import (
  IosBusinessNotificationProjector,
)

_UNSAFE_SECRETS = ("change-this", "replace-me")


def _projection_signing_key(configured: Settings) -> bytes:
  secret = configured.secret_key.strip()
  if len(secret.encode("utf-8")) < 32 or secret.lower().startswith(
    _UNSAFE_SECRETS
  ):
    raise RuntimeError(
      "iOS business notification projection requires the configured auth key"
    )
  return secret.encode("utf-8")


async def run_ios_notification_projection(
  configured: Settings = settings,
  *,
  session_factory: Callable[[], Any] = AsyncSessionLocal,
  source_batch_limit: int = 100,
) -> dict[str, int | str]:
  """Run one bounded, fully transactional durable-source projection pass."""

  signing_key = _projection_signing_key(configured)
  async with session_factory() as db:
    try:
      summary = await IosBusinessNotificationProjector(
        db,
        signing_key=signing_key,
        source_batch_limit=source_batch_limit,
      ).project_once()
      await db.commit()
    except Exception:
      await db.rollback()
      raise
  return {"status": "completed", **asdict(summary)}


@flow(name="ios-business-notification-projection", log_prints=False)
async def ios_notification_projection_flow() -> dict[str, int | str]:
  result = await run_ios_notification_projection()
  logger = get_run_logger()
  logger.info(
    "iOS notification projection status=%s discovered=%s projected=%s "
    "already_projected=%s queued=%s",
    result["status"],
    result["discovered"],
    result["projected"],
    result["already_projected"],
    result["queued"],
  )
  return result


__all__ = [
  "ios_notification_projection_flow",
  "run_ios_notification_projection",
]
