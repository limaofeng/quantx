"""Durable API-to-Engine control-plane commands."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from quantx_domain.clock import utcnow
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox


@dataclass(frozen=True)
class EngineCommandReceipt:
  message_id: str
  command_type: str
  aggregate_id: Optional[str]
  status: str
  result: Optional[dict[str, Any]] = None
  error: Optional[str] = None


class EngineCommandService:
  """Enqueue idempotent commands and optionally await their durable result."""

  async def enqueue(
    self,
    command_type: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    aggregate_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
  ) -> EngineCommandReceipt:
    message_id = str(uuid.uuid4())
    business_key = idempotency_key or f"{command_type}:{aggregate_id or message_id}"
    async with AsyncSessionLocal() as db:
      command = EngineCommandOutbox(
        message_id=message_id,
        idempotency_key=business_key,
        command_type=command_type,
        aggregate_id=aggregate_id,
        payload=self._json_value(payload or {}),
        processing_status="PENDING",
        available_at=utcnow(),
      )
      db.add(command)
      try:
        await db.commit()
      except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
          select(EngineCommandOutbox).where(
            EngineCommandOutbox.idempotency_key == business_key
          )
        )
        if existing is None:
          raise
        return self._receipt(existing)
      return self._receipt(command)

  async def get(self, message_id: str) -> Optional[EngineCommandReceipt]:
    async with AsyncSessionLocal() as db:
      command = await db.get(EngineCommandOutbox, message_id)
      return self._receipt(command) if command is not None else None

  async def wait(
    self,
    message_id: str,
    *,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.1,
  ) -> EngineCommandReceipt:
    deadline = asyncio.get_running_loop().time() + max(0.1, timeout_seconds)
    while True:
      receipt = await self.get(message_id)
      if receipt is None:
        raise ValueError(f"Engine command does not exist: {message_id}")
      if receipt.status in {"SUCCEEDED", "FAILED"}:
        return receipt
      if asyncio.get_running_loop().time() >= deadline:
        return receipt
      await asyncio.sleep(max(0.02, poll_seconds))

  async def request(
    self,
    command_type: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    aggregate_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    timeout_seconds: float = 8.0,
  ) -> EngineCommandReceipt:
    receipt = await self.enqueue(
      command_type,
      payload,
      aggregate_id=aggregate_id,
      idempotency_key=idempotency_key,
    )
    if receipt.status in {"SUCCEEDED", "FAILED"}:
      return receipt
    return await self.wait(receipt.message_id, timeout_seconds=timeout_seconds)

  @staticmethod
  def _receipt(command: EngineCommandOutbox) -> EngineCommandReceipt:
    return EngineCommandReceipt(
      message_id=command.message_id,
      command_type=command.command_type,
      aggregate_id=command.aggregate_id,
      status=command.processing_status,
      result=dict(command.result or {}) if command.result is not None else None,
      error=command.processing_error,
    )

  @classmethod
  def _json_value(cls, value: Any) -> Any:
    if isinstance(value, Enum):
      return value.value
    if isinstance(value, datetime):
      return value.isoformat()
    if isinstance(value, dict):
      return {str(key): cls._json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
      return [cls._json_value(item) for item in value]
    return value


engine_command_service = EngineCommandService()
