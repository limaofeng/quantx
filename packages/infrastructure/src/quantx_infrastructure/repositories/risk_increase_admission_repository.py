"""Repository for durable account risk-increase admission batches."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)


class RiskIncreaseAdmissionRepository:
  def __init__(self, db: Any) -> None:
    self.db = db

  async def find_batch(
    self,
    admission_batch_id: str,
    *,
    for_update: bool = False,
  ) -> AccountRiskIncreaseAdmissionBatch | None:
    stmt = select(AccountRiskIncreaseAdmissionBatch).where(
      AccountRiskIncreaseAdmissionBatch.admission_batch_id == admission_batch_id
    )
    if for_update:
      stmt = stmt.with_for_update()
    return await self.db.scalar(stmt)

  async def latest_for_input(
    self,
    *,
    account_id: str,
    environment: str,
    input_fingerprint: str,
    for_update: bool = False,
  ) -> AccountRiskIncreaseAdmissionBatch | None:
    stmt = (
      select(AccountRiskIncreaseAdmissionBatch)
      .where(
        AccountRiskIncreaseAdmissionBatch.account_id == account_id,
        AccountRiskIncreaseAdmissionBatch.environment == environment,
        AccountRiskIncreaseAdmissionBatch.input_fingerprint == input_fingerprint,
      )
      .order_by(AccountRiskIncreaseAdmissionBatch.attempt.desc())
      .limit(1)
    )
    if for_update:
      stmt = stmt.with_for_update()
    return await self.db.scalar(stmt)

  async def next_attempt(self, *, account_id: str, environment: str) -> int:
    current = await self.db.scalar(
      select(func.max(AccountRiskIncreaseAdmissionBatch.attempt)).where(
        AccountRiskIncreaseAdmissionBatch.account_id == account_id,
        AccountRiskIncreaseAdmissionBatch.environment == environment,
      )
    )
    return int(current or 0) + 1

  async def items(
    self,
    admission_batch_id: str,
    *,
    for_update: bool = False,
  ) -> list[AccountRiskIncreaseAdmissionItem]:
    stmt = (
      select(AccountRiskIncreaseAdmissionItem)
      .where(
        AccountRiskIncreaseAdmissionItem.admission_batch_id == admission_batch_id
      )
      .order_by(AccountRiskIncreaseAdmissionItem.admission_rank)
    )
    if for_update:
      stmt = stmt.with_for_update()
    return list((await self.db.scalars(stmt)).all())


__all__ = ["RiskIncreaseAdmissionRepository"]
