"""Repository for durable strategy decision audit records."""

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.strategy_decision_trace_record import (
  StrategyDecisionTraceRecord,
)


class StrategyDecisionTraceRepository(BaseRepository[StrategyDecisionTraceRecord]):
  """Strategy decision trace repository."""

  model_class = StrategyDecisionTraceRecord

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def create_trace(self, trace_data: Dict[str, Any]) -> StrategyDecisionTraceRecord:
    payload = dict(trace_data or {})
    payload.setdefault("id", str(uuid.uuid4()))
    record = StrategyDecisionTraceRecord(**payload)
    self.db.add(record)
    await self.db.commit()
    await self.db.refresh(record)
    return record

  async def find_by_strategy_run(
    self,
    strategy_run_id: str,
    *,
    limit: int = 50,
    cursor: Optional[str] = None,
  ) -> List[StrategyDecisionTraceRecord]:
    stmt = (
      select(StrategyDecisionTraceRecord)
      .filter(StrategyDecisionTraceRecord.strategy_run_id == strategy_run_id)
      .order_by(desc(StrategyDecisionTraceRecord.decided_at), desc(StrategyDecisionTraceRecord.created_at))
      .limit(max(1, min(int(limit or 50), 200)))
    )
    if cursor:
      stmt = stmt.filter(StrategyDecisionTraceRecord.id < cursor)
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_by_trace_id(
    self,
    strategy_run_id: str,
    trace_id: str,
  ) -> List[StrategyDecisionTraceRecord]:
    stmt = (
      select(StrategyDecisionTraceRecord)
      .filter(StrategyDecisionTraceRecord.strategy_run_id == strategy_run_id)
      .filter(StrategyDecisionTraceRecord.trace_id == trace_id)
      .order_by(desc(StrategyDecisionTraceRecord.decided_at), desc(StrategyDecisionTraceRecord.created_at))
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())
