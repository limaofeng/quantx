"""Append-only configuration versions for the independent T assistant."""

from __future__ import annotations

from typing import Optional

from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig


class TAssistantConfigConflict(RuntimeError):
  pass


class TAssistantConfigRepository:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def get_version(
    self,
    config_version_id: str,
  ) -> Optional[TAssistantConfigVersionRecord]:
    return await self.db.get(TAssistantConfigVersionRecord, config_version_id)

  async def append_version(
    self,
    version: TAssistantConfigVersion,
  ) -> TAssistantConfigVersionRecord:
    existing = await self.get_version(version.config_version_id)
    if existing is not None:
      if (
        existing.config_id != version.config_id
        or int(existing.version) != version.version
        or existing.config_snapshot_hash != version.config_snapshot_hash
        or existing.config_schema_version != version.config_schema_version
        or dict(existing.canonical_payload) != dict(version.canonical_payload)
        or existing.entry_authorization != version.entry_authorization.value
        or existing.rollout_stage != version.rollout_stage.value
        or existing.policy_version != version.policy_version
        or int(existing.feature_schema_version) != version.feature_schema_version
        or existing.scorer_mode != version.scorer_mode.value
        or (
          dict(existing.model_runtime_binding)
          if existing.model_runtime_binding is not None
          else None
        )
        != (
          dict(version.model_runtime_binding)
          if version.model_runtime_binding is not None
          else None
        )
      ):
        raise TAssistantConfigConflict("T_ASSISTANT_CONFIG_VERSION_CONFLICT")
      return existing
    duplicate_result = await self.db.execute(
      select(TAssistantConfigVersionRecord).where(
        TAssistantConfigVersionRecord.config_id == version.config_id,
        TAssistantConfigVersionRecord.version == version.version,
      )
    )
    duplicate = duplicate_result.scalar_one_or_none()
    if duplicate is not None:
      if (
        duplicate.config_snapshot_hash != version.config_snapshot_hash
        or duplicate.config_schema_version != version.config_schema_version
        or dict(duplicate.canonical_payload) != dict(version.canonical_payload)
        or duplicate.entry_authorization != version.entry_authorization.value
        or duplicate.rollout_stage != version.rollout_stage.value
        or duplicate.policy_version != version.policy_version
        or int(duplicate.feature_schema_version) != version.feature_schema_version
        or duplicate.scorer_mode != version.scorer_mode.value
        or (
          dict(duplicate.model_runtime_binding)
          if duplicate.model_runtime_binding is not None
          else None
        )
        != (
          dict(version.model_runtime_binding)
          if version.model_runtime_binding is not None
          else None
        )
      ):
        raise TAssistantConfigConflict("T_ASSISTANT_CONFIG_VERSION_CONFLICT")
      return duplicate
    record = TAssistantConfigVersionRecord(
      config_version_id=version.config_version_id,
      config_id=version.config_id,
      version=version.version,
      config_schema_version=version.config_schema_version,
      canonical_payload=dict(version.canonical_payload),
      config_snapshot_hash=version.config_snapshot_hash,
      entry_authorization=version.entry_authorization.value,
      rollout_stage=version.rollout_stage.value,
      policy_version=version.policy_version,
      feature_schema_version=version.feature_schema_version,
      scorer_mode=version.scorer_mode.value,
      model_runtime_binding=(
        dict(version.model_runtime_binding)
        if version.model_runtime_binding is not None
        else None
      ),
    )
    self.db.add(record)
    await self.db.flush()
    return record

  async def activate_version(
    self,
    *,
    config_id: str,
    config_version_id: str,
    desired_environment: str,
    expected_state_version: int,
  ) -> TTradeGlobalConfig:
    version = await self.get_version(config_version_id)
    if version is None or version.config_id != config_id:
      raise TAssistantConfigConflict("T_ASSISTANT_CONFIG_VERSION_NOT_FOUND")
    environment = str(desired_environment or "").upper()
    if environment not in {"PAPER", "LIVE"}:
      raise ValueError("T-assistant desired environment must be PAPER or LIVE")
    statement = (
      update(TTradeGlobalConfig)
      .where(
        TTradeGlobalConfig.id == config_id,
        TTradeGlobalConfig.config_version == int(version.version),
        TTradeGlobalConfig.state_version == expected_state_version,
      )
      .values(
        active_config_version_id=config_version_id,
        desired_environment=environment,
        state_version=expected_state_version + 1,
      )
    )
    result = await self.db.execute(statement)
    if int(result.rowcount or 0) != 1:
      raise TAssistantConfigConflict("T_ASSISTANT_CONFIG_HEAD_CONFLICT")
    await self.db.flush()
    head = await self.db.get(TTradeGlobalConfig, config_id)
    if head is None:
      raise TAssistantConfigConflict("T_ASSISTANT_CONFIG_NOT_FOUND")
    return head


__all__ = ["TAssistantConfigConflict", "TAssistantConfigRepository"]
