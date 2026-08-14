"""Persistence helpers for financial synchronization health."""

from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.financial_sync_code_audit import (
  FinancialSyncCodeAudit,
)
from quantx_infrastructure.models.financial_sync_run import FinancialSyncRun


class FinancialSyncRunRepository(BaseRepository[FinancialSyncRun]):
  model_class = FinancialSyncRun

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def create_run(self, data: Dict[str, Any]) -> FinancialSyncRun:
    run = FinancialSyncRun(**data)
    self.db.add(run)
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def update_run(
    self,
    run_id: int,
    data: Dict[str, Any],
  ) -> Optional[FinancialSyncRun]:
    run = await self.find_by_id(run_id)
    if run is None:
      return None
    for key, value in data.items():
      setattr(run, key, value)
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def upsert_code_audits(self, records: list[Dict[str, Any]]) -> int:
    if not records:
      return 0
    statement = insert(FinancialSyncCodeAudit).values(records)
    statement = statement.on_conflict_do_update(
      constraint="uq_financial_sync_code_audits_run_code",
      set_={
        "window_start": statement.excluded.window_start,
        "window_end": statement.excluded.window_end,
        "status": statement.excluded.status,
        "statement_rows": statement.excluded.statement_rows,
        "metric_rows": statement.excluded.metric_rows,
        "verified_at": statement.excluded.verified_at,
        "details": statement.excluded.details,
        "updated_at": func.now(),
      },
    )
    await self.db.execute(statement)
    await self.db.commit()
    return len(records)

  async def find_latest(self) -> Optional[FinancialSyncRun]:
    result = await self.db.execute(
      select(FinancialSyncRun)
      .order_by(FinancialSyncRun.started_at.desc(), FinancialSyncRun.id.desc())
      .limit(1)
    )
    return result.scalar_one_or_none()

  async def find_latest_success(self) -> Optional[FinancialSyncRun]:
    result = await self.db.execute(
      select(FinancialSyncRun)
      .where(FinancialSyncRun.status == "success")
      .order_by(
        FinancialSyncRun.completed_at.desc(),
        FinancialSyncRun.id.desc(),
      )
      .limit(1)
    )
    return result.scalar_one_or_none()
