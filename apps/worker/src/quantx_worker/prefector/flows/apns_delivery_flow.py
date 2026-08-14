"""Cancellable APNs outbox delivery owned by the independent Worker process."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from prefect import flow, get_run_logger
from quantx_infrastructure.config.settings import Settings, settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.services.apns_delivery_service import (
  ApnsDeliverySummary,
  ApnsProviderClient,
  ApnsProviderConfiguration,
  deliver_apns_batch,
)

_UNSAFE_SECRETS = (
  "change-this",
  "replace-me",
)
_MAX_PRIVATE_KEY_BYTES = 16 * 1024


def _notification_signing_key(configured: Settings) -> bytes:
  secret = configured.secret_key.strip()
  if len(secret.encode("utf-8")) < 32 or secret.lower().startswith(_UNSAFE_SECRETS):
    raise RuntimeError("APNs delivery requires the configured auth signing key")
  return secret.encode("utf-8")


def _provider_configuration(configured: Settings) -> ApnsProviderConfiguration:
  key_path = Path(configured.apns_private_key_file).expanduser()
  try:
    if (
      not key_path.is_file()
      or not 0 < key_path.stat().st_size <= _MAX_PRIVATE_KEY_BYTES
    ):
      raise RuntimeError("APNs private key file is unavailable")
    private_key_pem = key_path.read_bytes()
  except OSError:
    raise RuntimeError("APNs private key file is unavailable")
  return ApnsProviderConfiguration(
    team_id=configured.apns_team_id,
    key_id=configured.apns_key_id,
    topic=configured.apns_topic,
    private_key_pem=private_key_pem,
    timeout_seconds=configured.apns_timeout_seconds,
  )


async def run_apns_delivery(
  configured: Settings = settings,
  *,
  monotonic_clock: Callable[[], float] = time.monotonic,
  sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, object]:
  """Poll for one fixed delivery window using one HTTP/2 connection."""

  if not configured.apns_delivery_enabled:
    return {"status": "disabled", **asdict(ApnsDeliverySummary())}
  if configured.apns_lease_seconds < (2 * configured.apns_timeout_seconds) + 15:
    raise RuntimeError("APNs delivery lease is shorter than the request safety window")
  signing_key = _notification_signing_key(configured)
  provider = ApnsProviderClient(_provider_configuration(configured))
  counts = {key: 0 for key in asdict(ApnsDeliverySummary())}
  delivery_window = float(configured.apns_delivery_window_seconds)
  deadline = monotonic_clock() + delivery_window
  try:
    while monotonic_clock() < deadline:
      summary = await deliver_apns_batch(
        session_factory=AsyncSessionLocal,
        sender=provider,
        signing_key=signing_key,
        topic=configured.apns_topic,
        batch_size=configured.apns_batch_size,
        max_attempts=configured.apns_max_attempts,
        lease_seconds=configured.apns_lease_seconds,
      )
      for key in counts:
        counts[key] += int(getattr(summary, key))

      current = monotonic_clock()
      remaining = deadline - current
      if remaining <= 0:
        break
      await sleeper(min(float(configured.apns_poll_interval_seconds), remaining))
  finally:
    await provider.close()
  return {"status": "completed", **counts}


@flow(name="ios-apns-delivery", log_prints=False)
async def apns_delivery_flow() -> dict[str, object]:
  result = await run_apns_delivery()
  logger = get_run_logger()
  logger.info(
    "APNs outbox batch status=%s claimed=%s sent=%s retried=%s failed=%s discarded=%s",
    result.get("status"),
    result.get("claimed", 0),
    result.get("sent", 0),
    result.get("retried", 0),
    result.get("failed", 0),
    result.get("discarded", 0),
  )
  return result


__all__ = ["apns_delivery_flow", "run_apns_delivery"]
