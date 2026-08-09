"""Repository for strategy performance samples."""

from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.strategy_performance_sample import (
  StrategyPerformanceSample,
)


class StrategyPerformanceSampleRepository(BaseRepository[StrategyPerformanceSample]):
  """Data access for event-level strategy performance samples."""

  model_class = StrategyPerformanceSample

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def bulk_create(self, samples: List[Dict[str, Any]]) -> int:
    if not samples:
      return 0
    self.db.add_all([StrategyPerformanceSample(**sample) for sample in samples])
    await self.db.commit()
    return len(samples)

  async def list_by_run(
    self,
    run_id: str,
    *,
    backtest_id: Optional[str] = None,
    cursor: Optional[int] = None,
    limit: Optional[int] = None,
  ) -> List[StrategyPerformanceSample]:
    stmt = select(StrategyPerformanceSample).where(
      StrategyPerformanceSample.run_id == run_id
    )
    if backtest_id:
      stmt = stmt.where(StrategyPerformanceSample.backtest_id == backtest_id)
    if cursor is not None:
      stmt = stmt.where(StrategyPerformanceSample.sequence > cursor)
    stmt = stmt.order_by(StrategyPerformanceSample.sequence.asc())
    if limit:
      stmt = stmt.limit(limit)
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def list_by_backtest(
    self,
    backtest_id: str,
    *,
    cursor: Optional[int] = None,
    limit: Optional[int] = None,
  ) -> List[StrategyPerformanceSample]:
    stmt = select(StrategyPerformanceSample).where(
      StrategyPerformanceSample.backtest_id == backtest_id
    )
    if cursor is not None:
      stmt = stmt.where(StrategyPerformanceSample.sequence > cursor)
    stmt = stmt.order_by(StrategyPerformanceSample.sequence.asc())
    if limit:
      stmt = stmt.limit(limit)
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def delete_by_backtest(self, backtest_id: str) -> int:
    result = await self.db.execute(
      delete(StrategyPerformanceSample).where(
        StrategyPerformanceSample.backtest_id == backtest_id
      )
    )
    await self.db.commit()
    return int(result.rowcount or 0)
