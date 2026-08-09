"""
策略仓储层 - 只处理 Strategy 相关操作
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.enums import StrategyStatus
from quantx_infrastructure.models.strategy import Strategy


class StrategyRepository(BaseRepository[Strategy]):
  """策略仓储实现"""

  model_class = Strategy

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_all_active(self) -> List[Strategy]:
    """获取所有活跃策略"""
    result = await self.db.execute(
      select(Strategy).filter(
        Strategy.status.in_([StrategyStatus.ACTIVE, StrategyStatus.UPGRADING])
      )
    )
    return list(result.scalars().all())

  async def find_by_name(self, name: str) -> Optional[Strategy]:
    """根据名称获取策略"""
    result = await self.db.execute(
      select(Strategy).filter(
        Strategy.name == name, Strategy.status == StrategyStatus.ACTIVE
      )
    )
    return result.scalar_one_or_none()

  async def create(self, strategy_data: Dict[str, Any]) -> Strategy:
    """创建策略"""
    strategy = Strategy(**strategy_data)
    self.db.add(strategy)
    await self.db.commit()
    await self.db.refresh(strategy)
    return strategy

  async def update(
    self, strategy_id: int, strategy_data: Dict[str, Any]
  ) -> Optional[Strategy]:
    """更新策略"""
    strategy = await self.find_by_id(strategy_id)
    if strategy:
      for key, value in strategy_data.items():
        setattr(strategy, key, value)
      await self.db.commit()
      await self.db.refresh(strategy)
    return strategy

  async def delete(self, strategy_id: int) -> bool:
    """软删除策略（设置为已弃用）"""
    strategy = await self.find_by_id(strategy_id)
    if strategy:
      strategy.status = StrategyStatus.DEPRECATED
      await self.db.commit()
      return True
    return False

  async def get_all_strategies(self) -> List[Strategy]:
    """获取所有活跃策略（为了与 resolver 兼容）"""
    return await self.find_all_active()

  async def get_strategy(self, strategy_id: int) -> Optional[Strategy]:
    """获取单个策略（为了与 resolver 兼容）"""
    return await self.find_by_id(strategy_id)

  async def create_strategy(self, strategy_data: Dict[str, Any]) -> Strategy:
    """创建策略（为了与 resolver 兼容）"""
    return await self.create(strategy_data)

  async def update_strategy(
    self, strategy_id: int, strategy_data: Dict[str, Any]
  ) -> Optional[Strategy]:
    """更新策略（为了与 resolver 兼容）"""
    return await self.update(strategy_id, strategy_data)

  async def delete_strategy(self, strategy_id: int) -> bool:
    """删除策略（为了与 resolver 兼容）"""
    return await self.delete(strategy_id)

  async def find_by_class_name(self, class_name: str) -> Optional[Strategy]:
    """根据类名获取策略"""
    result = await self.db.execute(
      select(Strategy).filter(Strategy.class_name == class_name)
    )
    return result.scalar_one_or_none()

  async def find_active_by_version(
    self, class_name: str, version: str
  ) -> Optional[Strategy]:
    """根据类名和版本获取激活的策略"""
    result = await self.db.execute(
      select(Strategy).filter(
        Strategy.class_name == class_name,
        Strategy.version == version,
        Strategy.status == StrategyStatus.ACTIVE,
      )
    )
    return result.scalar_one_or_none()

  async def find_all_by_status(self, status: str) -> List[Strategy]:
    """根据状态获取策略列表"""
    result = await self.db.execute(select(Strategy).filter(Strategy.status == status))
    return list(result.scalars().all())

  async def update_status(self, strategy_id: int, status: str) -> Optional[Strategy]:
    """更新策略状态"""
    strategy = await self.find_by_id(strategy_id)
    if strategy:
      strategy.status = status
      await self.db.commit()
      await self.db.refresh(strategy)
    return strategy
