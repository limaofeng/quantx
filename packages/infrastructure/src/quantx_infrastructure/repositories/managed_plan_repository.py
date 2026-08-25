"""Persistence operations for stable plans and immutable config revisions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.managed_plan import (
  ManagedPlanConfigRevision,
  ManagedPlanRecord,
)


def managed_plan_config_fingerprint(snapshot: Mapping[str, Any]) -> str:
  canonical = json.dumps(
    dict(snapshot or {}),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
  )
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ManagedPlanRepository:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def find(self, plan_id: str, *, for_update: bool = False) -> Optional[ManagedPlanRecord]:
    stmt = select(ManagedPlanRecord).where(ManagedPlanRecord.plan_id == plan_id)
    if for_update:
      stmt = stmt.with_for_update()
    return (await self.db.execute(stmt)).scalar_one_or_none()

  async def current_revision(
    self,
    plan_id: str,
    *,
    for_update: bool = False,
  ) -> Optional[ManagedPlanConfigRevision]:
    plan = await self.find(plan_id, for_update=for_update)
    if plan is None:
      return None
    stmt = select(ManagedPlanConfigRevision).where(
      ManagedPlanConfigRevision.plan_id == plan.plan_id,
      ManagedPlanConfigRevision.config_version == plan.current_config_version,
    )
    if for_update:
      stmt = stmt.with_for_update()
    return (await self.db.execute(stmt)).scalar_one_or_none()

  async def create_plan(
    self,
    *,
    plan_id: str,
    plan_kind: str,
    account_id: str,
    instrument_code: str,
    config_snapshot: Mapping[str, Any],
    state_migration_policy: str,
    created_by_user_id: Optional[str] = None,
    last_command_id: Optional[str] = None,
  ) -> tuple[ManagedPlanRecord, ManagedPlanConfigRevision]:
    if await self.find(plan_id, for_update=True) is not None:
      raise ValueError(f"managed plan already exists: {plan_id}")
    snapshot = dict(config_snapshot or {})
    now = datetime.now()
    plan = ManagedPlanRecord(
      plan_id=plan_id,
      plan_kind=str(plan_kind or "").upper(),
      account_id=account_id,
      instrument_code=instrument_code.upper(),
      status="DRAFT",
      current_config_version=1,
      last_command_id=last_command_id,
    )
    revision = ManagedPlanConfigRevision(
      revision_id=str(uuid.uuid4()),
      plan_id=plan_id,
      config_version=1,
      config_snapshot=snapshot,
      config_fingerprint=managed_plan_config_fingerprint(snapshot),
      state_migration_policy=state_migration_policy,
      created_by_user_id=created_by_user_id,
      created_at=now,
    )
    self.db.add_all([plan, revision])
    await self.db.flush()
    return plan, revision

  async def append_revision(
    self,
    *,
    plan_id: str,
    expected_version: int,
    config_snapshot: Mapping[str, Any],
    state_migration_policy: str,
    supersedes_run_id: Optional[str],
    created_by_user_id: Optional[str] = None,
    last_command_id: Optional[str] = None,
  ) -> tuple[ManagedPlanRecord, ManagedPlanConfigRevision]:
    plan = await self.find(plan_id, for_update=True)
    if plan is None:
      raise ValueError("托管计划不存在")
    if int(plan.current_config_version or 0) != int(expected_version):
      raise ValueError(
        f"CONFIG_VERSION_CONFLICT: current={plan.current_config_version}"
      )
    version = int(expected_version) + 1
    snapshot = dict(config_snapshot or {})
    revision = ManagedPlanConfigRevision(
      revision_id=str(uuid.uuid4()),
      plan_id=plan_id,
      config_version=version,
      config_snapshot=snapshot,
      config_fingerprint=managed_plan_config_fingerprint(snapshot),
      state_migration_policy=state_migration_policy,
      supersedes_run_id=supersedes_run_id,
      created_by_user_id=created_by_user_id,
      created_at=datetime.now(),
    )
    plan.current_config_version = version
    plan.current_run_id = None
    plan.status = "DRAFT"
    plan.last_command_id = last_command_id
    plan.last_error = None
    self.db.add(revision)
    await self.db.flush()
    return plan, revision

  async def bind_run(
    self,
    *,
    plan_id: str,
    config_version: int,
    run_id: str,
    status: str,
  ) -> ManagedPlanRecord:
    plan = await self.find(plan_id, for_update=True)
    if plan is None or int(plan.current_config_version or 0) != int(config_version):
      raise ValueError("托管计划绑定运行时版本已变化")
    revision = await self.current_revision(plan_id, for_update=True)
    if revision is None:
      raise ValueError("托管计划配置版本不存在")
    if revision.run_id and revision.run_id != run_id:
      raise ValueError("托管计划配置版本已经绑定其他运行")
    revision.run_id = run_id
    plan.current_run_id = run_id
    plan.status = str(status or "PAUSED").upper()
    await self.db.flush()
    return plan
