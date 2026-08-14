"""Persistence and effective-value projection for AI Runtime settings."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from quantx_domain.clock import utcnow
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.models.ai_runtime_settings import (
  AiRuntimeSettingsAudit,
  AiRuntimeSettingsRecord,
)

GLOBAL_SETTINGS_ID = "global"


class AiRuntimeSettingsVersionConflict(ValueError):
  def __init__(self, current_version: int):
    super().__init__(f"AI_RUNTIME_SETTINGS_VERSION_CONFLICT:{current_version}")
    self.current_version = current_version


@dataclass(frozen=True)
class AiRuntimeEditableValues:
  enabled: bool
  model: str
  max_concurrent_runs: int
  max_turns: int
  max_tool_calls: int
  run_timeout_seconds: int

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)

  def to_run_snapshot(self) -> dict[str, Any]:
    return {
      "model": self.model,
      "maxTurns": self.max_turns,
      "maxToolCalls": self.max_tool_calls,
      "runTimeoutSeconds": self.run_timeout_seconds,
    }


@dataclass(frozen=True)
class EffectiveAiRuntimeSettings:
  version: int
  source: str
  values: AiRuntimeEditableValues
  api_key_configured: bool
  tracing_enabled: bool
  lease_seconds: int
  updated_at: datetime | None = None
  updated_by_user_id: str | None = None


def deployment_editable_values() -> AiRuntimeEditableValues:
  return AiRuntimeEditableValues(
    enabled=bool(settings.ai_assistant_enabled),
    model=settings.quantx_ai_model.strip() or "gpt-5.6",
    max_concurrent_runs=int(settings.ai_assistant_max_concurrent_runs),
    max_turns=int(settings.ai_assistant_max_turns),
    max_tool_calls=int(settings.ai_assistant_max_tool_calls),
    run_timeout_seconds=int(settings.ai_assistant_run_timeout_seconds),
  )


def _values_from_record(record: AiRuntimeSettingsRecord) -> AiRuntimeEditableValues:
  return AiRuntimeEditableValues(
    enabled=bool(record.enabled),
    model=str(record.model),
    max_concurrent_runs=int(record.max_concurrent_runs),
    max_turns=int(record.max_turns),
    max_tool_calls=int(record.max_tool_calls),
    run_timeout_seconds=int(record.run_timeout_seconds),
  )


def effective_from_record(
  record: AiRuntimeSettingsRecord | None,
) -> EffectiveAiRuntimeSettings:
  return EffectiveAiRuntimeSettings(
    version=int(record.config_version) if record is not None else 0,
    source="DATABASE_OVERRIDE" if record is not None else "ENVIRONMENT",
    values=_values_from_record(record) if record is not None else deployment_editable_values(),
    api_key_configured=bool(settings.openai_api_key.strip()),
    tracing_enabled=bool(settings.ai_assistant_tracing_enabled),
    lease_seconds=int(settings.ai_assistant_lease_seconds),
    updated_at=record.updated_at if record is not None else None,
    updated_by_user_id=record.updated_by_user_id if record is not None else None,
  )


class AiRuntimeSettingsRepository:
  def __init__(self, db: AsyncSession):
    self.db = db

  async def get_record(self) -> AiRuntimeSettingsRecord | None:
    return await self.db.get(AiRuntimeSettingsRecord, GLOBAL_SETTINGS_ID)

  async def get_effective(self) -> EffectiveAiRuntimeSettings:
    return effective_from_record(await self.get_record())

  async def update(
    self,
    *,
    expected_version: int,
    values: AiRuntimeEditableValues,
    user_id: str,
    request_id: str,
  ) -> EffectiveAiRuntimeSettings:
    record = await self.db.scalar(
      select(AiRuntimeSettingsRecord)
      .where(AiRuntimeSettingsRecord.id == GLOBAL_SETTINGS_ID)
      .with_for_update()
    )
    current = effective_from_record(record)
    if expected_version != current.version:
      await self.db.rollback()
      raise AiRuntimeSettingsVersionConflict(current.version)

    next_version = current.version + 1
    now = utcnow()
    if record is None:
      record = AiRuntimeSettingsRecord(
        id=GLOBAL_SETTINGS_ID,
        config_version=next_version,
        enabled=values.enabled,
        model=values.model,
        max_concurrent_runs=values.max_concurrent_runs,
        max_turns=values.max_turns,
        max_tool_calls=values.max_tool_calls,
        run_timeout_seconds=values.run_timeout_seconds,
        updated_by_user_id=user_id,
        created_at=now,
        updated_at=now,
      )
      self.db.add(record)
    else:
      record.config_version = next_version
      record.enabled = values.enabled
      record.model = values.model
      record.max_concurrent_runs = values.max_concurrent_runs
      record.max_turns = values.max_turns
      record.max_tool_calls = values.max_tool_calls
      record.run_timeout_seconds = values.run_timeout_seconds
      record.updated_by_user_id = user_id
      record.updated_at = now

    self.db.add(
      AiRuntimeSettingsAudit(
        id=str(uuid.uuid4()),
        config_version=next_version,
        previous_values=current.values.to_dict(),
        next_values=values.to_dict(),
        user_id=user_id,
        request_id=request_id[:64],
        occurred_at=now,
      )
    )
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      latest = await self.get_record()
      raise AiRuntimeSettingsVersionConflict(
        int(latest.config_version) if latest is not None else 0
      ) from None
    await self.db.refresh(record)
    return effective_from_record(record)


def run_values_from_snapshot(
  snapshot: dict[str, Any] | None,
  fallback: AiRuntimeEditableValues,
) -> AiRuntimeEditableValues:
  """Overlay immutable per-run limits on the current operational settings."""

  payload = dict(snapshot or {})
  return AiRuntimeEditableValues(
    enabled=fallback.enabled,
    model=str(payload.get("model") or fallback.model),
    max_concurrent_runs=fallback.max_concurrent_runs,
    max_turns=int(payload.get("maxTurns") or fallback.max_turns),
    max_tool_calls=int(payload.get("maxToolCalls") or fallback.max_tool_calls),
    run_timeout_seconds=int(
      payload.get("runTimeoutSeconds") or fallback.run_timeout_seconds
    ),
  )
