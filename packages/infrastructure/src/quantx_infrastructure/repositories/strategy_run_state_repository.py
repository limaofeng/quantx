"""
策略运行时状态仓储层

包含两个仓储：
1. StrategyRunStateRepository: 负责资金状态和自定义JSON状态
2. StrategyRunPositionRepository: 负责独立持仓表的操作
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.strategy_run_state import (
    StrategyRunPosition,
    StrategyRunState,
)


class StrategyRunStateRepository(BaseRepository[StrategyRunState]):
    """策略运行时状态仓储（资金 + 自定义状态）"""

    model_class = StrategyRunState

    def __init__(self, db_session: AsyncSession):
        super().__init__(db_session)

    async def get_state(self, run_id: str) -> Optional[StrategyRunState]:
        """获取运行状态"""
        stmt = select(StrategyRunState).filter(StrategyRunState.run_id == run_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_state(
        self,
        run_id: str,
        cash: float = 0.0,
        frozen_cash: float = 0.0,
        total_asset: float = 0.0,
        custom_state: Dict[str, Any] = None,
        expected_version: Optional[int] = None,
    ) -> bool:
        """
        更新或插入状态（支持乐观锁）

        Returns:
            是否成功
        """
        custom_state = custom_state or {}
        # 获取当前版本
        existing = await self.get_state(run_id)

        if existing:
            # 更新：检查版本号
            if expected_version is not None and existing.version != expected_version:
                return False  # 版本冲突

            new_version = existing.version + 1

            stmt = (
                update(StrategyRunState)
                .where(StrategyRunState.run_id == run_id)
                .values(
                    cash=cash,
                    frozen_cash=frozen_cash,
                    total_asset=total_asset,
                    custom_state=custom_state,
                    version=new_version,
                )
            )
            await self.db.execute(stmt)
        else:
            # 插入
            stmt = insert(StrategyRunState).values(
                run_id=run_id,
                cash=cash,
                frozen_cash=frozen_cash,
                total_asset=total_asset,
                custom_state=custom_state,
                version=1,
            )
            await self.db.execute(stmt)

        await self.db.commit()
        return True


class StrategyRunPositionRepository(BaseRepository[StrategyRunPosition]):
    """策略运行时持仓仓储（独立表）"""

    model_class = StrategyRunPosition

    def __init__(self, db_session: AsyncSession):
        super().__init__(db_session)

    async def get_all_positions(self, run_id: str) -> List[StrategyRunPosition]:
        """获取某次运行的所有持仓"""
        stmt = select(StrategyRunPosition).filter(StrategyRunPosition.run_id == run_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_position(self, run_id: str, instrument_code: str) -> Optional[StrategyRunPosition]:
        """获取特定标的持仓"""
        stmt = select(StrategyRunPosition).filter(
            StrategyRunPosition.run_id == run_id,
            StrategyRunPosition.instrument_code == instrument_code,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_position(
        self,
        run_id: str,
        instrument_code: str,
        long_volume: int = 0,
        short_volume: int = 0,
        long_avg_price: float = 0.0,
        short_avg_price: float = 0.0,
        market_value: float = 0.0,
        pnl: float = 0.0,
        last_price: float = 0.0,
    ) -> StrategyRunPosition:
        """更新或创建持仓"""
        existing = await self.get_position(run_id, instrument_code)

        if existing:
            existing.long_volume = long_volume
            existing.short_volume = short_volume
            existing.long_avg_price = long_avg_price
            existing.short_avg_price = short_avg_price
            existing.market_value = market_value
            existing.pnl = pnl
            existing.last_price = last_price
            await self.db.commit()
            await self.db.refresh(existing)
            return existing
        else:
            new_pos = StrategyRunPosition(
                run_id=run_id,
                instrument_code=instrument_code,
                long_volume=long_volume,
                short_volume=short_volume,
                long_avg_price=long_avg_price,
                short_avg_price=short_avg_price,
                market_value=market_value,
                pnl=pnl,
                last_price=last_price,
            )
            self.db.add(new_pos)
            await self.db.commit()
            await self.db.refresh(new_pos)
            return new_pos
