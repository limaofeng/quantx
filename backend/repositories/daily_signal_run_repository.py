"""日级信号运行日志仓储。"""

from datetime import date
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models.daily_signal_run import DailySignalRun


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
    stmt = select(DailySignalRun).where(DailySignalRun.status == "success")
    if snapshot_date is not None:
      stmt = stmt.where(DailySignalRun.snapshot_date == snapshot_date)
    stmt = stmt.order_by(
      DailySignalRun.snapshot_date.desc(),
      DailySignalRun.completed_at.desc(),
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
