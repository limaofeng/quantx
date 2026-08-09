"""Pure use case for durable, asynchronous broker commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class TradeCommand:
  idempotency_key: str
  account_id: str
  command_kind: str
  expires_at: datetime
  payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueuedCommand:
  message_id: str
  client_order_id: str
  status: str = "QUEUED"


class TradeCommandQueue(Protocol):
  async def enqueue(self, command: TradeCommand) -> QueuedCommand:
    ...


class QueueTradeCommand:
  def __init__(self, queue: TradeCommandQueue) -> None:
    self.queue = queue

  async def execute(self, command: TradeCommand) -> QueuedCommand:
    if not command.account_id.strip():
      raise ValueError("TradeCommand requires account_id")
    if command.expires_at <= datetime.now(command.expires_at.tzinfo):
      raise ValueError("TradeCommand is already expired")
    return await self.queue.enqueue(command)
