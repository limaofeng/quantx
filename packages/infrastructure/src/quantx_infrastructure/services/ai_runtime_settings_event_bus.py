"""Best-effort wake-up channel for durable AI Runtime configuration."""

from quantx_infrastructure.database.redis_pubsub import redis_pubsub

AI_RUNTIME_SETTINGS_WAKE_CHANNEL = "quantx:ai-runtime-settings"


async def notify_ai_runtime_settings(version: int) -> None:
  await redis_pubsub.publish(
    AI_RUNTIME_SETTINGS_WAKE_CHANNEL,
    {"version": max(0, int(version))},
  )
