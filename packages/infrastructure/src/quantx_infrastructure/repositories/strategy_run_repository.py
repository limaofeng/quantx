"""
策略运行仓储层 - 处理 StrategyRun 相关操作
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus
from quantx_infrastructure.models.strategy import Strategy
from quantx_infrastructure.models.strategy_backtest import StrategyBacktest
from quantx_infrastructure.models.strategy_decision_trace_record import (
  StrategyDecisionTraceRecord,
)
from quantx_infrastructure.models.strategy_grid_book_snapshot import (
  StrategyGridBookSnapshot,
)
from quantx_infrastructure.models.strategy_performance_sample import (
  StrategyPerformanceSample,
)
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.strategy_run_state import (
  StrategyRunPosition,
  StrategyRunState,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord


class StrategyRunRepository(BaseRepository[StrategyRun]):
  """策略运行仓储实现"""

  model_class = StrategyRun

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_run_by_id(self, run_id: str) -> Optional[StrategyRun]:
    """根据ID获取策略运行"""
    result = await self.db.execute(
      select(StrategyRun)
      .options(selectinload(StrategyRun.strategy))
      .filter(StrategyRun.id == run_id)
    )
    return result.scalar_one_or_none()

  async def find_run_by_id_for_update(self, run_id: str) -> Optional[StrategyRun]:
    """Lock one run while checking and changing its configuration version."""
    result = await self.db.execute(
      select(StrategyRun)
      .options(selectinload(StrategyRun.strategy))
      .filter(StrategyRun.id == run_id)
      .with_for_update()
    )
    return result.scalar_one_or_none()

  async def find_runs_by_ids(self, run_ids: List[str]) -> List[StrategyRun]:
    """Batch-load strategy runs while preserving repository loading rules."""
    if not run_ids:
      return []
    result = await self.db.execute(
      select(StrategyRun)
      .options(selectinload(StrategyRun.strategy))
      .filter(StrategyRun.id.in_(run_ids))
    )
    return list(result.scalars().all())

  async def find_all_runs_by_strategy(self, strategy_id: int) -> List[StrategyRun]:
    """获取策略的所有运行"""
    result = await self.db.execute(
      select(StrategyRun).filter(StrategyRun.strategy_id == strategy_id)
    )
    return list(result.scalars().all())

  async def find_all_strategy_runs(self, user_id: str = None) -> List[StrategyRun]:
    """获取策略运行列表"""
    stmt = select(StrategyRun).options(selectinload(StrategyRun.strategy))
    if user_id:
      stmt = stmt.filter(StrategyRun.user_id == user_id)

    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_all_running_runs(self, user_id: str = None) -> List[StrategyRun]:
    """获取运行中的策略运行"""
    stmt = select(StrategyRun).filter(StrategyRun.status == StrategyRunStatus.RUNNING)
    if user_id:
      stmt = stmt.filter(StrategyRun.user_id == user_id)

    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_all_active_runs(self, user_id: str = None) -> List[StrategyRun]:
    """获取所有活跃的策略运行（RUNNING、PAUSED、PENDING）
    
    这些状态的运行实例需要在服务启动时加载到 executor 中。
    """
    active_statuses = [
      StrategyRunStatus.RUNNING,
      StrategyRunStatus.PAUSED,
      StrategyRunStatus.PENDING,
    ]
    stmt = (
      select(StrategyRun)
      .options(selectinload(StrategyRun.strategy))
      .filter(StrategyRun.status.in_(active_statuses))
    )
    if user_id:
      stmt = stmt.filter(StrategyRun.user_id == user_id)

    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_active_runs_by_strategy_class(
    self,
    class_name: str,
  ) -> List[StrategyRun]:
    """Return non-backtest active runs for one strategy class, newest first."""
    active_statuses = [
      StrategyRunStatus.RUNNING,
      StrategyRunStatus.PAUSED,
      StrategyRunStatus.PENDING,
    ]
    stmt = (
      select(StrategyRun)
      .join(StrategyRun.strategy)
      .options(selectinload(StrategyRun.strategy))
      .filter(
        Strategy.class_name == class_name,
        StrategyRun.status.in_(active_statuses),
        StrategyRun.mode != StrategyRunMode.BACKTEST,
      )
      .order_by(
        StrategyRun.updated_at.desc(),
        StrategyRun.created_at.desc(),
        StrategyRun.id.desc(),
      )
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def create_strategy_run(self, run_data: Dict[str, Any]) -> StrategyRun:
    """创建策略运行"""
    run = StrategyRun(**run_data)
    self.db.add(run)
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def update_strategy_run_status(
    self, run_id: str, status: str, error_message: str = None
  ) -> Optional[StrategyRun]:
    """更新策略运行状态"""
    run = await self.find_run_by_id(run_id)
    if run:
      run.status = status
      if error_message:
        run.error_message = error_message
      await self.db.commit()
      await self.db.refresh(run)
    return run

  async def delete_run(self, run_id: str) -> bool:
    """删除策略运行及其所有相关数据"""
    try:
      # 1. 删除关联的回测记录
      await self.db.execute(
        delete(StrategyGridBookSnapshot).where(
          StrategyGridBookSnapshot.strategy_run_id == run_id
        )
      )

      await self.db.execute(
        delete(StrategyPerformanceSample).where(
          StrategyPerformanceSample.run_id == run_id
        )
      )

      # 2. 删除关联的回测记录
      await self.db.execute(
        delete(StrategyBacktest).where(StrategyBacktest.strategy_run_id == run_id)
      )
      
      # 3. 删除关联的交易意图
      await self.db.execute(
        delete(TradeIntentRecord).where(TradeIntentRecord.strategy_run_id == run_id)
      )

      # 4. 删除关联的决策审计
      await self.db.execute(
        delete(StrategyDecisionTraceRecord).where(
          StrategyDecisionTraceRecord.strategy_run_id == run_id
        )
      )
      
      # 5. 删除关联的持仓状态
      await self.db.execute(
        delete(StrategyRunPosition).where(StrategyRunPosition.run_id == run_id)
      )
      
      # 6. 删除关联的运行状态
      await self.db.execute(
        delete(StrategyRunState).where(StrategyRunState.run_id == run_id)
      )
      
      # 7. 删除主表记录
      # 注意：即便主表记录不存在（orphan），我们也返回 True，因为我们确保了清理动作执行了
      await self.db.execute(
        delete(StrategyRun).where(StrategyRun.id == run_id)
      )
      
      await self.db.commit()
      return True
    except Exception as e:
      await self.db.rollback()
      raise e

  async def create_run(self, run_data: Dict[str, Any]) -> StrategyRun:
    """创建策略运行"""
    run = StrategyRun(**run_data)
    self.db.add(run)
    await self.db.commit()
    await self.db.refresh(run)
    return run

  async def update_run(
    self, run_id: str, run_data: Dict[str, Any]
  ) -> Optional[StrategyRun]:
    """更新策略运行"""
    run = await self.find_run_by_id(run_id)
    if run:
      for key, value in run_data.items():
        setattr(run, key, value)
      await self.db.commit()
      await self.db.refresh(run)
    return run

  async def find_by_status(self, status: str, user_id: str = None) -> List[StrategyRun]:
    """根据状态查找策略运行"""
    stmt = select(StrategyRun).filter(StrategyRun.status == status)
    if user_id:
      stmt = stmt.filter(StrategyRun.user_id == user_id)

    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_by_mode(self, mode: str, user_id: str = None) -> List[StrategyRun]:
    """根据运行模式查找策略运行"""
    stmt = select(StrategyRun).filter(StrategyRun.mode == mode)
    if user_id:
      stmt = stmt.filter(StrategyRun.user_id == user_id)

    result = await self.db.execute(stmt)
    return list(result.scalars().all())
