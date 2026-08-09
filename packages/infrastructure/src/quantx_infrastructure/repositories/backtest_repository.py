"""
回测结果仓库层
"""

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.strategy_backtest import StrategyBacktest
from quantx_infrastructure.models.strategy_grid_book_snapshot import (
    StrategyGridBookSnapshot,
)
from quantx_infrastructure.models.strategy_performance_sample import (
    StrategyPerformanceSample,
)

logger = logging.getLogger(__name__)


class BacktestRepository:
    """回测结果仓库"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_backtest(
        self,
        backtest_id: str,
        strategy_run_id: str,
        parameters: dict,
        instruments: list,
        backtest_start_time: Optional[datetime] = None,
        backtest_end_time: Optional[datetime] = None,
    ) -> StrategyBacktest:
        """创建新的回测记录"""
        # 获取当前 run 的最大版本号
        stmt = select(func.max(StrategyBacktest.version)).where(
            StrategyBacktest.strategy_run_id == strategy_run_id
        )
        result = await self.db.execute(stmt)
        max_version = result.scalar() or 0
        new_version = max_version + 1

        result_path = f"backtests/{strategy_run_id}/v{new_version}/manifest.json"
        backtest = StrategyBacktest(
            id=backtest_id,
            strategy_run_id=strategy_run_id,
            version=new_version,
            parameters=parameters,
            instruments=instruments,
            backtest_start_time=backtest_start_time.replace(tzinfo=None) if backtest_start_time else None,
            backtest_end_time=backtest_end_time.replace(tzinfo=None) if backtest_end_time else None,
            status="PENDING",
            result_path=result_path,
        )
        self.db.add(backtest)
        await self.db.commit()
        await self.db.refresh(backtest)
        return backtest

    async def get_backtest(self, backtest_id: str) -> Optional[StrategyBacktest]:
        """获取单个回测记录"""
        stmt = select(StrategyBacktest).where(StrategyBacktest.id == backtest_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_backtest_by_run_version(
        self,
        strategy_run_id: str,
        version: int,
    ) -> Optional[StrategyBacktest]:
        """获取某个 StrategyRun 下的指定回测版本"""
        stmt = select(StrategyBacktest).where(
            StrategyBacktest.strategy_run_id == strategy_run_id,
            StrategyBacktest.version == version,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_backtests_by_run(
        self, strategy_run_id: str
    ) -> List[StrategyBacktest]:
        """获取某个 StrategyRun 下的所有回测历史"""
        stmt = (
            select(StrategyBacktest)
            .where(StrategyBacktest.strategy_run_id == strategy_run_id)
            .order_by(StrategyBacktest.version.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_backtest_status(
        self,
        backtest_id: str,
        status: str,
        metrics: Optional[dict] = None,
        error_message: Optional[str] = None,
        end_time: Optional[datetime] = None,
    ) -> Optional[StrategyBacktest]:
        """更新回测状态和结果"""
        backtest = await self.get_backtest(backtest_id)
        if not backtest:
            return None

        backtest.status = status
        if metrics is not None:
            backtest.metrics = metrics
        if error_message is not None:
            backtest.error_message = error_message
        if end_time is not None:
            backtest.end_time = end_time

        await self.db.commit()
        await self.db.refresh(backtest)
        return backtest

    async def update_backtest_start(
        self, backtest_id: str, start_time: datetime
    ) -> Optional[StrategyBacktest]:
        """标记回测开始执行"""
        backtest = await self.get_backtest(backtest_id)
        if not backtest:
            return None

        backtest.status = "RUNNING"
        backtest.start_time = start_time
        await self.db.commit()
        await self.db.refresh(backtest)
        return backtest

    async def delete_backtest(self, backtest_id: str) -> bool:
        """删除回测记录"""
        backtest = await self.get_backtest(backtest_id)
        if not backtest:
            return False

        await self.db.execute(
            delete(StrategyGridBookSnapshot).where(
                StrategyGridBookSnapshot.backtest_id == backtest_id
            )
        )
        await self.db.execute(
            delete(StrategyPerformanceSample).where(
                StrategyPerformanceSample.backtest_id == backtest_id
            )
        )
        await self.db.delete(backtest)
        await self.db.commit()
        return True
