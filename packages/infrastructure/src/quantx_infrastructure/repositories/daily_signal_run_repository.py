"""日级信号运行日志仓储。"""

from datetime import date
from typing import Any, Dict, List, Optional

from quantx_domain.factors import FACTOR_VERSION
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.daily_signal_run import DailySignalRun


def _latest_current_success() -> ColumnElement[bool]:
  """A later attempt revokes the older run's certification for that date.

  The autoincrement run ID records creation order. started_at can be supplied
  by a caller and completed_at/updated_at change after creation, so none of
  those timestamps may let an older successful attempt overtake a newer run.
  """
  candidate = aliased(DailySignalRun)
  latest_id = (
    select(func.max(candidate.id))
    .where(
      candidate.snapshot_date == DailySignalRun.snapshot_date,
      candidate.signal_version == FACTOR_VERSION,
    )
    .correlate(DailySignalRun)
    .scalar_subquery()
  )
  return and_(
    DailySignalRun.signal_version == FACTOR_VERSION,
    DailySignalRun.status == "success",
    DailySignalRun.id == latest_id,
  )


class DailySignalRunRepository(BaseRepository[DailySignalRun]):
  """日级信号运行日志仓储"""

  model_class = DailySignalRun

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def create_run(self, data: Dict[str, Any]) -> DailySignalRun:
    run = DailySignalRun(**data)
    self.db.add(run)
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def update_run(self, run_id: int, data: Dict[str, Any]) -> Optional[DailySignalRun]:
    run = await self.find_by_id(run_id)
    if run is None:
      return None
    for key, value in data.items():
      setattr(run, key, value)
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def find_latest_completed(
    self, snapshot_date: Optional[date] = None
  ) -> Optional[DailySignalRun]:
    stmt = select(DailySignalRun).where(_latest_current_success())
    if snapshot_date is not None:
      stmt = stmt.where(DailySignalRun.snapshot_date == snapshot_date)
    stmt = stmt.order_by(
      DailySignalRun.snapshot_date.desc(),
      DailySignalRun.id.desc(),
    ).limit(1)
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def find_latest(
    self, snapshot_date: Optional[date] = None
  ) -> Optional[DailySignalRun]:
    """获取最近一次信号运行记录，不限定运行状态。"""
    stmt = select(DailySignalRun)
    if snapshot_date is not None:
      stmt = stmt.where(DailySignalRun.snapshot_date == snapshot_date)
    stmt = stmt.order_by(
      DailySignalRun.snapshot_date.desc(),
      DailySignalRun.started_at.desc(),
      DailySignalRun.id.desc(),
    ).limit(1)
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def find_completed_dates(
    self, start_date: date, end_date: date
  ) -> List[date]:
    """只返回当前因子版本最后一次运行成功的交易日。"""
    result = await self.db.execute(
      select(DailySignalRun.snapshot_date)
      .where(
        _latest_current_success(),
        DailySignalRun.snapshot_date >= start_date,
        DailySignalRun.snapshot_date <= end_date,
      )
      .distinct()
      .order_by(DailySignalRun.snapshot_date.asc())
    )
    return list(result.scalars().all())

  async def delete_older_than(self, cutoff_date: date) -> int:
    """清理保留窗口以前的运行日志。"""
    result = await self.db.execute(
      delete(DailySignalRun).where(
        DailySignalRun.snapshot_date < cutoff_date
      )
    )
    await self.db.commit()
    return int(result.rowcount or 0)
