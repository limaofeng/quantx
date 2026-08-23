"""Durable API-to-Engine control-plane commands."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, is_dataclass
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


class EngineCommandIdempotencyError(ValueError):
  """A client reused an idempotency key for a different command."""

  code = "IDEMPOTENCY_KEY_REUSED"

  def __init__(
    self,
    *,
    idempotency_key: str,
    requested_command_type: str,
    existing_command_type: str,
    requested_aggregate_id: Optional[str],
    existing_aggregate_id: Optional[str],
  ) -> None:
    self.idempotency_key = idempotency_key
    self.requested_command_type = requested_command_type
    self.existing_command_type = existing_command_type
    self.requested_aggregate_id = requested_aggregate_id
    self.existing_aggregate_id = existing_aggregate_id
    super().__init__(
      f"{self.code}: idempotency key {idempotency_key!r} is already bound "
      "to a different engine command"
    )


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
    canonical_payload, canonical_payload_json = self._canonical_payload(payload)
    async with AsyncSessionLocal() as db:
      command = EngineCommandOutbox(
        message_id=message_id,
        idempotency_key=business_key,
        command_type=command_type,
        aggregate_id=aggregate_id,
        payload=canonical_payload,
        processing_status="PENDING",
        available_at=utcnow(),
      )
      db.add(command)
      try:
        await db.commit()
      except IntegrityError as integrity_error:
        await db.rollback()
        existing = None
        # Under concurrent inserts the winning transaction may not be
        # visible to this session until its commit completes.  The unique
        # constraint has already serialized the identity; briefly retry the
        # lookup before treating the integrity error as unrelated.
        for attempt in range(5):
          existing = await db.scalar(
            select(EngineCommandOutbox).where(
              EngineCommandOutbox.idempotency_key == business_key
            )
          )
          if existing is not None or attempt == 4:
            break
          await asyncio.sleep(0.01)
        if existing is None:
          raise integrity_error
        _, existing_payload_json = self._canonical_payload(existing.payload)
        if (
          existing.command_type != command_type
          or existing.aggregate_id != aggregate_id
          or existing_payload_json != canonical_payload_json
        ):
          raise EngineCommandIdempotencyError(
            idempotency_key=business_key,
            requested_command_type=command_type,
            existing_command_type=existing.command_type,
            requested_aggregate_id=aggregate_id,
            existing_aggregate_id=existing.aggregate_id,
          )
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
    if is_dataclass(value) and not isinstance(value, type):
      return cls._json_value(asdict(value))
    if isinstance(value, Enum):
      return value.value
    if isinstance(value, datetime):
      return value.isoformat()
    if isinstance(value, dict):
      return {str(key): cls._json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
      return [cls._json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
      normalized_items = [cls._json_value(item) for item in value]
      try:
        return sorted(
          normalized_items,
          key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
          ),
        )
      except (TypeError, ValueError):
        # _canonical_payload will raise the useful JSON validation error for
        # unsupported values; keep sorting deterministic enough to reach it.
        return sorted(normalized_items, key=repr)
    return value

  @classmethod
  def _canonical_payload(cls, payload: Optional[dict[str, Any]]) -> tuple[Any, str]:
    """Normalize and serialize a command payload for durable identity checks."""

    normalized = cls._json_value(payload if payload is not None else {})
    try:
      encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
      )
    except (TypeError, ValueError) as exc:
      raise ValueError(
        "engine command payload must contain only finite JSON values"
      ) from exc
    return json.loads(encoded), encoded


engine_command_service = EngineCommandService()
