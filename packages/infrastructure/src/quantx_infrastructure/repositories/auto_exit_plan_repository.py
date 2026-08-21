"""Repository for persistent Engine-owned automatic exit plans."""

from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)

RESERVING_EXIT_PLAN_STATUSES = (
  "PENDING_ENTRY",
  "ACTIVE",
  "EXIT_PENDING",
  "PARTIALLY_EXITED",
  "PAUSED",
  "ERROR",
)


class AutoExitPlanRepository:
  def __init__(self, db: AsyncSession):
    self.db = db

  async def find_by_id(
    self, plan_id: str, *, for_update: bool = False
  ) -> Optional[AutoExitPlanRecord]:
    stmt = select(AutoExitPlanRecord).where(AutoExitPlanRecord.plan_id == plan_id)
    if for_update:
      stmt = stmt.with_for_update()
    return (await self.db.execute(stmt)).scalar_one_or_none()

  async def find_by_source(
    self, source_type: str, source_id: str
  ) -> Optional[AutoExitPlanRecord]:
    return (
      await self.db.execute(
        select(AutoExitPlanRecord)
        .where(AutoExitPlanRecord.source_type == source_type)
        .where(AutoExitPlanRecord.source_id == source_id)
      )
    ).scalar_one_or_none()

  async def find_active(
    self,
    *,
    account_id: Optional[str] = None,
    instrument_code: Optional[str] = None,
  ) -> list[AutoExitPlanRecord]:
    stmt = (
      select(AutoExitPlanRecord)
      .where(AutoExitPlanRecord.enabled == True)  # noqa: E712
      .where(
        AutoExitPlanRecord.status.in_(("ACTIVE", "PARTIALLY_EXITED", "EXIT_PENDING"))
      )
    )
    if account_id:
      stmt = stmt.where(AutoExitPlanRecord.account_id == account_id)
    if instrument_code:
      stmt = stmt.where(AutoExitPlanRecord.instrument_code == instrument_code)
    return list((await self.db.execute(stmt)).scalars().all())

  async def find_all(
    self,
    *,
    account_id: Optional[str] = None,
    instrument_code: Optional[str] = None,
    statuses: Optional[list[str]] = None,
    source_type: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    limit: int = 200,
  ) -> list[AutoExitPlanRecord]:
    stmt = select(AutoExitPlanRecord)
    if account_id:
      stmt = stmt.where(AutoExitPlanRecord.account_id == account_id)
    if instrument_code:
      stmt = stmt.where(AutoExitPlanRecord.instrument_code == instrument_code)
    if statuses:
      stmt = stmt.where(AutoExitPlanRecord.status.in_(statuses))
    if source_type:
      stmt = stmt.where(AutoExitPlanRecord.source_type == source_type)
    if strategy_run_id:
      stmt = stmt.where(AutoExitPlanRecord.strategy_run_id == strategy_run_id)
    stmt = stmt.order_by(desc(AutoExitPlanRecord.updated_at)).limit(
      max(1, min(int(limit or 200), 500))
    )
    return list((await self.db.execute(stmt)).scalars().all())

  async def find_reserving(
    self,
    *,
    account_id: str,
    instrument_code: str,
    for_update: bool = False,
  ) -> list[AutoExitPlanRecord]:
    stmt = (
      select(AutoExitPlanRecord)
      .where(AutoExitPlanRecord.account_id == account_id)
      .where(AutoExitPlanRecord.instrument_code == instrument_code)
      .where(AutoExitPlanRecord.status.in_(RESERVING_EXIT_PLAN_STATUSES))
      .order_by(AutoExitPlanRecord.created_at, AutoExitPlanRecord.plan_id)
    )
    if for_update:
      stmt = stmt.with_for_update()
    return list((await self.db.execute(stmt)).scalars().all())

  async def find_events(
    self,
    *,
    plan_id: str,
    limit: int = 200,
  ) -> list[AutoExitPlanEvent]:
    stmt = (
      select(AutoExitPlanEvent)
      .where(AutoExitPlanEvent.plan_id == plan_id)
      .order_by(desc(AutoExitPlanEvent.created_at))
      .limit(max(1, min(int(limit or 200), 500)))
    )
    return list((await self.db.execute(stmt)).scalars().all())

  async def save(self, record: AutoExitPlanRecord) -> AutoExitPlanRecord:
    self.db.add(record)
    await self.db.commit()
    await self.db.refresh(record)
    return record
