"""Persistence operations for exact managed-entry authorization grants."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.entry_plan_authorization import (
  EntryAutomationGate,
  EntryPlanAuthorizationConsumption,
  EntryPlanAuthorizationEvent,
  EntryPlanAuthorizationGrant,
)


class EntryPlanAuthorizationRepository:
  def __init__(self, db: AsyncSession):
    self.db = db

  async def find_grant(
    self, grant_id: str, *, for_update: bool = False
  ) -> Optional[EntryPlanAuthorizationGrant]:
    stmt = select(EntryPlanAuthorizationGrant).where(
      EntryPlanAuthorizationGrant.grant_id == grant_id
    )
    if for_update:
      stmt = stmt.with_for_update()
    return (await self.db.execute(stmt)).scalar_one_or_none()

  async def find_current_for_plan(
    self,
    plan_id: str,
    *,
    for_update: bool = False,
  ) -> Optional[EntryPlanAuthorizationGrant]:
    stmt = (
      select(EntryPlanAuthorizationGrant)
      .where(
        EntryPlanAuthorizationGrant.plan_id == plan_id,
        EntryPlanAuthorizationGrant.revoked_at.is_(None),
        EntryPlanAuthorizationGrant.invalidated_at.is_(None),
      )
      .order_by(EntryPlanAuthorizationGrant.authorized_at.desc())
      .limit(1)
    )
    if for_update:
      stmt = stmt.with_for_update()
    return (await self.db.execute(stmt)).scalar_one_or_none()

  async def find_consumption(
    self, trade_business_key: str
  ) -> Optional[EntryPlanAuthorizationConsumption]:
    return await self.db.scalar(
      select(EntryPlanAuthorizationConsumption).where(
        EntryPlanAuthorizationConsumption.trade_business_key == trade_business_key
      )
    )

  async def find_gate(
    self, account_fingerprint: str, *, for_update: bool = False
  ) -> Optional[EntryAutomationGate]:
    stmt = select(EntryAutomationGate).where(
      EntryAutomationGate.account_fingerprint == account_fingerprint
    )
    if for_update:
      stmt = stmt.with_for_update()
    return (await self.db.execute(stmt)).scalar_one_or_none()

  def add_gate(self, gate: EntryAutomationGate) -> None:
    self.db.add(gate)

  async def consumed_on_date(self, plan_id: str, trade_date: date) -> Decimal:
    value = await self.db.scalar(
      select(
        func.coalesce(func.sum(EntryPlanAuthorizationConsumption.filled_amount_cny), 0)
      ).where(
        EntryPlanAuthorizationConsumption.plan_id == plan_id,
        EntryPlanAuthorizationConsumption.trade_date == trade_date,
      )
    )
    return Decimal(str(value or 0))

  async def consumed_for_plan(self, plan_id: str) -> Decimal:
    value = await self.db.scalar(
      select(
        func.coalesce(func.sum(EntryPlanAuthorizationConsumption.filled_amount_cny), 0)
      ).where(EntryPlanAuthorizationConsumption.plan_id == plan_id)
    )
    return Decimal(str(value or 0))

  async def consumed_volume_for_plan(self, plan_id: str) -> int:
    value = await self.db.scalar(
      select(
        func.coalesce(func.sum(EntryPlanAuthorizationConsumption.filled_volume), 0)
      ).where(EntryPlanAuthorizationConsumption.plan_id == plan_id)
    )
    return int(value or 0)

  def add_grant(self, grant: EntryPlanAuthorizationGrant) -> None:
    self.db.add(grant)

  def add_consumption(self, consumption: EntryPlanAuthorizationConsumption) -> None:
    self.db.add(consumption)

  async def add_event_once(
    self,
    *,
    event_id: str,
    business_key: str,
    plan_id: str,
    grant_id: Optional[str],
    event_type: str,
    reason_code: Optional[str],
    subject_fingerprint: Optional[str],
    created_at: datetime,
  ) -> None:
    existing = await self.db.scalar(
      select(EntryPlanAuthorizationEvent.event_id).where(
        EntryPlanAuthorizationEvent.business_key == business_key
      )
    )
    if existing is None:
      self.db.add(
        EntryPlanAuthorizationEvent(
          event_id=event_id,
          business_key=business_key,
          plan_id=plan_id,
          grant_id=grant_id,
          event_type=event_type,
          reason_code=reason_code,
          subject_fingerprint=subject_fingerprint,
          created_at=created_at,
        )
      )


__all__ = ["EntryPlanAuthorizationRepository"]
