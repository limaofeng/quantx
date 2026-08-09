"""Runtime ports. Implementations live in quantx-infrastructure."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from quantx_contracts import AgentEnvelope, TradeCommandPayload


class AgentMessageStore(Protocol):
  async def enqueue_command(self, command: TradeCommandPayload) -> str:
    ...

  async def persist_report(self, envelope: AgentEnvelope) -> bool:
    """Persist a report, returning False when it is a duplicate."""
    ...

  def pending_commands(self, device_id: str) -> AsyncIterator[AgentEnvelope]:
    ...


class CommandDispatcher(Protocol):
  async def notify_command_available(self, device_id: str) -> None:
    ...


class EngineLease(Protocol):
  async def acquire(self) -> bool:
    ...

  async def renew(self) -> bool:
    ...

  async def release(self) -> None:
    ...
