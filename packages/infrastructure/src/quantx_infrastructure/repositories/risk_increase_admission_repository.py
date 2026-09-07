"""Repository for durable account risk-increase admission batches."""

from __future__ import annotations

from typing import Any

from quantx_contracts import ExecutionEnvironment
from sqlalchemy import func, select

from quantx_infrastructure.models.paper_execution import PaperExecutionAccountRecord
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)


class RiskIncreaseAdmissionRepository:
  def __init__(
    self,
    db: Any,
    *,
    environment=ExecutionEnvironment.LIVE,
    paper_execution_id: str | None = None,
  ) -> None:
    self.db = db
    self.environment = ExecutionEnvironment(environment)
    self.paper_execution_id = paper_execution_id
    if self.environment not in {ExecutionEnvironment.LIVE, ExecutionEnvironment.PAPER}:
      raise ValueError("RISK_ADMISSION_ENVIRONMENT_INVALID")
    if (
      self.environment is ExecutionEnvironment.LIVE
      and paper_execution_id is not None
      or self.environment is ExecutionEnvironment.PAPER
      and (
        not isinstance(paper_execution_id, str)
        or not paper_execution_id.strip()
        or paper_execution_id != paper_execution_id.strip()
      )
    ):
      raise ValueError("RISK_ADMISSION_PAPER_SCOPE_INVALID")

  def _scope(self):
    return (
      AccountRiskIncreaseAdmissionBatch.environment == self.environment.value,
      AccountRiskIncreaseAdmissionBatch.paper_execution_id == self.paper_execution_id,
    )

  async def _validate_account(self, account_id):
    if self.environment is ExecutionEnvironment.PAPER:
      account = await self.db.scalar(
        select(PaperExecutionAccountRecord)
        .where(
          PaperExecutionAccountRecord.execution_id == self.paper_execution_id,
        )
        .execution_options(populate_existing=True)
      )
      if (
        account is None
        or account.account_id != account_id
        or account.environment != "PAPER"
      ):
        raise ValueError("RISK_ADMISSION_PAPER_ACCOUNT_SCOPE_CONFLICT")

  async def find_batch(
    self,
    admission_batch_id: str,
    *,
    for_update: bool = False,
  ) -> AccountRiskIncreaseAdmissionBatch | None:
    stmt = select(AccountRiskIncreaseAdmissionBatch).where(
      AccountRiskIncreaseAdmissionBatch.admission_batch_id == admission_batch_id,
      *self._scope(),
    )
    if for_update:
      stmt = stmt.with_for_update()
    batch = await self.db.scalar(stmt.execution_options(populate_existing=True))
    if batch is not None:
      await self._validate_account(batch.account_id)
    return batch

  async def latest_for_input(
    self,
    *,
    account_id: str,
    environment: str,
    input_fingerprint: str,
    for_update: bool = False,
  ) -> AccountRiskIncreaseAdmissionBatch | None:
    if environment != self.environment.value:
      raise ValueError("RISK_ADMISSION_ENVIRONMENT_CONFLICT")
    await self._validate_account(account_id)
    stmt = (
      select(AccountRiskIncreaseAdmissionBatch)
      .where(
        AccountRiskIncreaseAdmissionBatch.account_id == account_id,
        AccountRiskIncreaseAdmissionBatch.environment == environment,
        AccountRiskIncreaseAdmissionBatch.input_fingerprint == input_fingerprint,
        *self._scope(),
      )
      .order_by(AccountRiskIncreaseAdmissionBatch.attempt.desc())
      .limit(1)
    )
    if for_update:
      stmt = stmt.with_for_update()
    return await self.db.scalar(stmt)

  async def next_attempt(self, *, account_id: str, environment: str) -> int:
    if environment != self.environment.value:
      raise ValueError("RISK_ADMISSION_ENVIRONMENT_CONFLICT")
    await self._validate_account(account_id)
    current = await self.db.scalar(
      select(func.max(AccountRiskIncreaseAdmissionBatch.attempt)).where(
        AccountRiskIncreaseAdmissionBatch.account_id == account_id,
        AccountRiskIncreaseAdmissionBatch.environment == environment,
        *self._scope(),
      )
    )
    return int(current or 0) + 1

  async def items(
    self,
    admission_batch_id: str,
    *,
    for_update: bool = False,
  ) -> list[AccountRiskIncreaseAdmissionItem]:
    if await self.find_batch(admission_batch_id, for_update=for_update) is None:
      raise ValueError("RISK_ADMISSION_BATCH_NOT_FOUND")
    stmt = (
      select(AccountRiskIncreaseAdmissionItem)
      .where(AccountRiskIncreaseAdmissionItem.admission_batch_id == admission_batch_id)
      .order_by(AccountRiskIncreaseAdmissionItem.admission_rank)
    )
    if for_update:
      stmt = stmt.with_for_update()
    return list((await self.db.scalars(stmt)).all())


__all__ = ["RiskIncreaseAdmissionRepository"]
