"""Persistence for independent T-assistant execution identity and events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Optional

from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantEntryReadiness,
  TAssistantEntryReadinessProjection,
  TAssistantExecution,
  TAssistantExecutionEvent,
  TAssistantExecutionStatus,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)


class TAssistantExecutionConflict(RuntimeError):
  pass


def _aware(value: datetime) -> datetime:
  return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _storage_datetime(value: Optional[datetime]) -> Optional[datetime]:
  """Persist one canonical instant even on timezone-less SQLite test stores."""

  if value is None:
    return None
  if value.tzinfo is None:
    raise ValueError("T-assistant execution time must be timezone-aware")
  return value.astimezone(UTC)


class TAssistantExecutionRepository:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def get(self, execution_id: str) -> Optional[TAssistantExecutionRecord]:
    return await self.db.get(TAssistantExecutionRecord, execution_id)

  async def get_domain(self, execution_id: str) -> Optional[TAssistantExecution]:
    record = await self.get(execution_id)
    return _execution_from_record(record) if record is not None else None

  async def find_active_paper_shadow(
    self,
    *,
    account_id: str,
    config_version_id: str,
  ) -> Optional[TAssistantExecutionRecord]:
    result = await self.db.execute(
      select(TAssistantExecutionRecord)
      .where(
        TAssistantExecutionRecord.account_id == account_id,
        TAssistantExecutionRecord.environment == ExecutionEnvironment.PAPER.value,
        TAssistantExecutionRecord.config_version_id == config_version_id,
        TAssistantExecutionRecord.status.in_(
          (
            TAssistantExecutionStatus.CREATED.value,
            TAssistantExecutionStatus.WARMING.value,
            TAssistantExecutionStatus.RUNNING.value,
            TAssistantExecutionStatus.DRAINING.value,
            TAssistantExecutionStatus.RECONCILE_REQUIRED.value,
          )
        ),
      )
      .order_by(TAssistantExecutionRecord.created_at.desc())
      .limit(1)
    )
    return result.scalar_one_or_none()

  async def find_active_paper_for_account(
    self,
    account_id: str,
  ) -> Optional[TAssistantExecutionRecord]:
    result = await self.db.execute(
      select(TAssistantExecutionRecord)
      .where(
        TAssistantExecutionRecord.account_id == str(account_id).strip(),
        TAssistantExecutionRecord.environment == ExecutionEnvironment.PAPER.value,
        TAssistantExecutionRecord.status.in_(
          (
            TAssistantExecutionStatus.CREATED.value,
            TAssistantExecutionStatus.WARMING.value,
            TAssistantExecutionStatus.RUNNING.value,
            TAssistantExecutionStatus.DRAINING.value,
            TAssistantExecutionStatus.RECONCILE_REQUIRED.value,
          )
        ),
      )
      .order_by(TAssistantExecutionRecord.created_at.desc())
      .limit(1)
    )
    return result.scalar_one_or_none()

  async def list_active_paper_for_account(
    self,
    account_id: str,
  ) -> list[TAssistantExecutionRecord]:
    result = await self.db.execute(
      select(TAssistantExecutionRecord)
      .where(
        TAssistantExecutionRecord.account_id == str(account_id).strip(),
        TAssistantExecutionRecord.environment == ExecutionEnvironment.PAPER.value,
        TAssistantExecutionRecord.status.in_(
          (
            TAssistantExecutionStatus.CREATED.value,
            TAssistantExecutionStatus.WARMING.value,
            TAssistantExecutionStatus.RUNNING.value,
            TAssistantExecutionStatus.DRAINING.value,
            TAssistantExecutionStatus.RECONCILE_REQUIRED.value,
          )
        ),
      )
      .order_by(TAssistantExecutionRecord.created_at.asc())
      .with_for_update()
    )
    return list(result.scalars().all())

  async def ensure_paper_shadow(
    self,
    *,
    account_id: str,
    version: TAssistantConfigVersion,
    now: datetime,
  ) -> TAssistantExecutionRecord:
    if now.tzinfo is None:
      raise ValueError("T-assistant execution time must be timezone-aware")
    existing = await self.find_active_paper_shadow(
      account_id=account_id,
      config_version_id=version.config_version_id,
    )
    if existing is not None:
      return existing
    execution_id = str(
      uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"quantx:t-assistant:paper-shadow:{account_id}:{version.config_version_id}",
      )
    )
    record = await self.get(execution_id)
    if record is not None:
      attempt = int(
        await self.db.scalar(
          select(func.count(TAssistantExecutionRecord.execution_id)).where(
            TAssistantExecutionRecord.account_id == account_id,
            TAssistantExecutionRecord.environment
            == ExecutionEnvironment.PAPER.value,
            TAssistantExecutionRecord.config_version_id
            == version.config_version_id,
          )
        )
        or 0
      ) + 1
      execution_id = str(
        uuid.uuid5(
          uuid.NAMESPACE_URL,
          "quantx:t-assistant:paper-shadow:"
          f"{account_id}:{version.config_version_id}:{attempt}",
        )
      )
    record = TAssistantExecutionRecord(
      execution_id=execution_id,
      config_id=version.config_id,
      config_version_id=version.config_version_id,
      frozen_config_version=version.version,
      config_snapshot_hash=version.config_snapshot_hash,
      account_id=account_id,
      environment=ExecutionEnvironment.PAPER.value,
      entry_authorization=version.entry_authorization.value,
      rollout_stage=version.rollout_stage.value,
      status=TAssistantExecutionStatus.WARMING.value,
      entry_readiness=TAssistantEntryReadiness.WARMING.value,
      entry_readiness_reasons=["T_REWARM_REQUIRED"],
      entry_readiness_as_of=_storage_datetime(now),
      policy_version=version.policy_version,
      feature_schema_version=version.feature_schema_version,
      scorer_mode=version.scorer_mode.value,
      model_runtime_binding=(
        dict(version.model_runtime_binding)
        if version.model_runtime_binding is not None
        else None
      ),
      universe_revision=0,
      last_assigned_cycle_sequence=0,
      last_committed_cycle_sequence=0,
      checkpoint_revision=0,
      state_version=1,
    )
    self.db.add(record)
    await self.append_event(
      TAssistantExecutionEvent(
        execution_id=execution_id,
        event_key=f"execution-created:{execution_id}",
        event_type="EXECUTION_CREATED",
        occurred_at=now,
        payload={
          "environment": ExecutionEnvironment.PAPER.value,
          "config_version_id": version.config_version_id,
          "config_snapshot_hash": version.config_snapshot_hash,
          "shadow_only": True,
        },
      )
    )
    await self.db.flush()
    return record

  async def append_event(
    self,
    event: TAssistantExecutionEvent,
  ) -> TAssistantExecutionEventRecord:
    result = await self.db.execute(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.execution_id == event.execution_id,
        TAssistantExecutionEventRecord.event_key == event.event_key,
      )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
      if existing.event_type != event.event_type or dict(existing.payload) != dict(
        event.payload
      ):
        raise TAssistantExecutionConflict("T_ASSISTANT_EVENT_CONFLICT")
      return existing
    record = TAssistantExecutionEventRecord(
      event_id=str(uuid.uuid4()),
      execution_id=event.execution_id,
      event_key=event.event_key,
      event_type=event.event_type,
      occurred_at=_storage_datetime(event.occurred_at),
      source_type=event.source_type,
      source_id=event.source_id,
      payload=dict(event.payload),
    )
    self.db.add(record)
    await self.db.flush()
    return record

  async def save_transition(
    self,
    execution: TAssistantExecution,
    *,
    expected_state_version: int,
  ) -> TAssistantExecutionRecord:
    values = _execution_values(execution)
    statement = (
      update(TAssistantExecutionRecord)
      .where(
        TAssistantExecutionRecord.execution_id == execution.execution_id,
        TAssistantExecutionRecord.state_version == expected_state_version,
      )
      .values(**values)
    )
    result = await self.db.execute(statement)
    if int(result.rowcount or 0) != 1:
      raise TAssistantExecutionConflict("T_ASSISTANT_EXECUTION_STATE_CONFLICT")
    await self.db.flush()
    record = await self.get(execution.execution_id)
    if record is None:
      raise TAssistantExecutionConflict("T_ASSISTANT_EXECUTION_NOT_FOUND")
    return record

  async def save_transition_with_event(
    self,
    execution: TAssistantExecution,
    *,
    expected_state_version: int,
    event: TAssistantExecutionEvent,
  ) -> TAssistantExecutionRecord:
    if execution.state_version != expected_state_version + 1:
      raise TAssistantExecutionConflict("T_ASSISTANT_STATE_VERSION_INVALID")
    if event.execution_id != execution.execution_id:
      raise TAssistantExecutionConflict("T_ASSISTANT_EVENT_OWNER_CONFLICT")
    record = await self.save_transition(
      execution,
      expected_state_version=expected_state_version,
    )
    await self.append_event(event)
    return record


def _execution_values(execution: TAssistantExecution) -> dict:
  return {
    "status": execution.status.value,
    "entry_readiness": execution.readiness.readiness.value,
    "entry_readiness_reasons": list(execution.readiness.reasons),
    "entry_readiness_as_of": _storage_datetime(execution.readiness.as_of),
    "universe_revision": execution.universe_revision,
    "last_assigned_cycle_sequence": execution.last_assigned_cycle_sequence,
    "last_committed_cycle_sequence": execution.last_committed_cycle_sequence,
    "checkpoint_revision": execution.checkpoint_revision,
    "started_at": _storage_datetime(execution.started_at),
    "drain_requested_at": _storage_datetime(execution.drain_requested_at),
    "completed_at": _storage_datetime(execution.completed_at),
    "state_version": execution.state_version,
  }


def _execution_from_record(record: TAssistantExecutionRecord) -> TAssistantExecution:
  return TAssistantExecution(
    execution_id=record.execution_id,
    config_id=record.config_id,
    config_version_id=record.config_version_id,
    frozen_config_version=int(record.frozen_config_version),
    config_snapshot_hash=record.config_snapshot_hash,
    account_id=record.account_id,
    environment=ExecutionEnvironment(record.environment),
    entry_authorization=record.entry_authorization,
    rollout_stage=record.rollout_stage,
    status=record.status,
    readiness=TAssistantEntryReadinessProjection(
      readiness=record.entry_readiness,
      reasons=tuple(record.entry_readiness_reasons or ()),
      as_of=_aware(record.entry_readiness_as_of),
    ),
    policy_version=record.policy_version,
    feature_schema_version=int(record.feature_schema_version),
    scorer_mode=record.scorer_mode,
    universe_revision=int(record.universe_revision),
    last_assigned_cycle_sequence=int(record.last_assigned_cycle_sequence),
    last_committed_cycle_sequence=int(record.last_committed_cycle_sequence),
    checkpoint_revision=int(record.checkpoint_revision),
    state_version=int(record.state_version),
    model_runtime_binding=(
      dict(record.model_runtime_binding)
      if record.model_runtime_binding is not None
      else None
    ),
    started_at=_aware(record.started_at) if record.started_at else None,
    drain_requested_at=(
      _aware(record.drain_requested_at) if record.drain_requested_at else None
    ),
    completed_at=_aware(record.completed_at) if record.completed_at else None,
  )


__all__ = [
  "TAssistantExecutionConflict",
  "TAssistantExecutionRepository",
]
