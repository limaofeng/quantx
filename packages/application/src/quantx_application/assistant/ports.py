"""Ports implemented by the assistant runtime and infrastructure package."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .contracts import (
  AssistantEventRecord,
  AssistantExecutionContext,
  AssistantToolMetadata,
  AssistantToolResult,
)


class AssistantTool(Protocol):
  metadata: AssistantToolMetadata

  async def invoke(
    self,
    context: AssistantExecutionContext,
    arguments: Mapping[str, Any],
  ) -> AssistantToolResult: ...


class AssistantSessionStore(Protocol):
  async def load_items(self, thread_id: str) -> list[dict[str, Any]]: ...

  async def append_items(
    self,
    thread_id: str,
    items: Sequence[Mapping[str, Any]],
  ) -> None: ...


class AssistantEventPublisher(Protocol):
  async def append(self, event: AssistantEventRecord) -> int: ...
