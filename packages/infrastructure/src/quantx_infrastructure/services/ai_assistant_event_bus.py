"""Redis wake-ups for assistant work and durable event streams."""

from __future__ import annotations

from quantx_infrastructure.database.redis_pubsub import redis_pubsub

AI_ASSISTANT_RUN_WAKE_CHANNEL = "ai-assistant:run:wakeup"


def ai_assistant_event_channel(thread_id: str) -> str:
  return f"ai-assistant:thread:{thread_id}"


async def notify_ai_assistant_run(run_id: str) -> None:
  await redis_pubsub.publish(AI_ASSISTANT_RUN_WAKE_CHANNEL, {"runId": run_id})


async def notify_ai_assistant_event(
  *,
  thread_id: str,
  sequence: int,
) -> None:
  await redis_pubsub.publish(
    ai_assistant_event_channel(thread_id),
    {"threadId": thread_id, "sequence": sequence},
  )
