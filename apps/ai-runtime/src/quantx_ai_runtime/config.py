"""Validated and dynamically refreshable AI Runtime configuration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.ai_runtime_settings_repository import (
  AiRuntimeEditableValues,
  AiRuntimeSettingsRepository,
  EffectiveAiRuntimeSettings,
  run_values_from_snapshot,
)
from quantx_infrastructure.services.ai_runtime_settings_event_bus import (
  AI_RUNTIME_SETTINGS_WAKE_CHANNEL,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AiRuntimeConfig:
  version: int
  source: str
  enabled: bool
  api_key: str
  model: str
  max_concurrent_runs: int
  max_turns: int
  max_tool_calls: int
  run_timeout_seconds: int
  lease_seconds: int
  tracing_enabled: bool

  @property
  def configured(self) -> bool:
    return self.enabled and self.provider_configured

  @property
  def provider_configured(self) -> bool:
    return bool(self.api_key.strip())

  def for_run(
    self,
    *,
    version: int,
    snapshot: dict | None,
  ) -> "AiRuntimeConfig":
    current = AiRuntimeEditableValues(
      enabled=self.enabled,
      model=self.model,
      max_concurrent_runs=self.max_concurrent_runs,
      max_turns=self.max_turns,
      max_tool_calls=self.max_tool_calls,
      run_timeout_seconds=self.run_timeout_seconds,
    )
    values = run_values_from_snapshot(snapshot, current)
    return AiRuntimeConfig(
      version=max(0, int(version)),
      source="RUN_SNAPSHOT",
      enabled=self.enabled,
      api_key=self.api_key,
      model=values.model,
      max_concurrent_runs=self.max_concurrent_runs,
      max_turns=values.max_turns,
      max_tool_calls=values.max_tool_calls,
      run_timeout_seconds=values.run_timeout_seconds,
      lease_seconds=self.lease_seconds,
      tracing_enabled=self.tracing_enabled,
    )


def _from_effective(config: EffectiveAiRuntimeSettings) -> AiRuntimeConfig:
  return AiRuntimeConfig(
    version=config.version,
    source=config.source,
    enabled=config.values.enabled,
    api_key=settings.openai_api_key.strip(),
    model=config.values.model,
    max_concurrent_runs=config.values.max_concurrent_runs,
    max_turns=config.values.max_turns,
    max_tool_calls=config.values.max_tool_calls,
    run_timeout_seconds=config.values.run_timeout_seconds,
    lease_seconds=config.lease_seconds,
    tracing_enabled=config.tracing_enabled,
  )


def load_config() -> AiRuntimeConfig:
  return AiRuntimeConfig(
    version=0,
    source="ENVIRONMENT",
    enabled=bool(settings.ai_assistant_enabled),
    api_key=settings.openai_api_key.strip(),
    model=settings.quantx_ai_model.strip() or "gpt-5.6",
    max_concurrent_runs=settings.ai_assistant_max_concurrent_runs,
    max_turns=settings.ai_assistant_max_turns,
    max_tool_calls=settings.ai_assistant_max_tool_calls,
    run_timeout_seconds=settings.ai_assistant_run_timeout_seconds,
    lease_seconds=settings.ai_assistant_lease_seconds,
    tracing_enabled=settings.ai_assistant_tracing_enabled,
  )


async def load_effective_config() -> AiRuntimeConfig:
  async with AsyncSessionLocal() as db:
    effective = await AiRuntimeSettingsRepository(db).get_effective()
  return _from_effective(effective)


class AiRuntimeConfigController:
  """Atomic in-process view of the PostgreSQL-backed desired settings."""

  def __init__(self, initial: AiRuntimeConfig | None = None) -> None:
    self._current = initial or load_config()

  def snapshot(self) -> AiRuntimeConfig:
    return self._current

  async def refresh(self) -> AiRuntimeConfig:
    self._current = await load_effective_config()
    return self._current


def runtime_status(config: AiRuntimeConfig, *, dependencies_available: bool) -> str:
  if not config.enabled:
    return "disabled"
  if not config.provider_configured:
    return "unconfigured"
  if not dependencies_available:
    return "unavailable"
  return "ready"


async def config_refresh_loop(
  stopped: asyncio.Event,
  *,
  controller: AiRuntimeConfigController,
) -> None:
  subscription = None
  try:
    try:
      subscription = await redis_pubsub.open_subscription(
        AI_RUNTIME_SETTINGS_WAKE_CHANNEL
      )
    except Exception as exc:
      logger.warning(
        "AI Runtime config wake subscription unavailable: %s",
        exc.__class__.__name__,
      )
    while not stopped.is_set():
      try:
        await controller.refresh()
      except Exception as exc:
        logger.warning(
          "AI Runtime config refresh failed: %s",
          exc.__class__.__name__,
        )
      if subscription is not None:
        try:
          await subscription.wait_for_message(timeout=5.0)
        except Exception as exc:
          logger.warning(
            "AI Runtime config wake subscription lost: %s",
            exc.__class__.__name__,
          )
          try:
            await subscription.close()
          except Exception:
            pass
          subscription = None
      else:
        try:
          await asyncio.wait_for(stopped.wait(), timeout=5.0)
        except asyncio.TimeoutError:
          pass
  finally:
    if subscription is not None:
      await subscription.close()
