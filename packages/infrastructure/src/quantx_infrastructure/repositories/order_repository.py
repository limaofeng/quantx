"""
订单仓储层
处理订单相关的数据访问
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.order import Order


class OrderRepository(BaseRepository[Order]):
  """订单仓储实现"""

  model_class = Order

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_all(self, skip: int = 0, limit: int = 100) -> List[Order]:
    """获取所有订单"""
    result = await self.db.execute(
      select(Order).order_by(Order.created_at.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all())

  async def find_all_by_user(
    self, user_id: str, skip: int = 0, limit: int = 100
  ) -> List[Order]:
    """获取用户的订单"""
    result = await self.db.execute(
      select(Order)
      .filter(Order.user_id == user_id)
      .order_by(Order.created_at.desc())
      .offset(skip)
      .limit(limit)
    )
    return list(result.scalars().all())

  async def find_all_by_instrument(
    self, instrument_id: int, skip: int = 0, limit: int = 100
  ) -> List[Order]:
    """获取某只金融产品的订单"""
    result = await self.db.execute(
      select(Order)
      .filter(Order.instrument_id == instrument_id)
      .order_by(Order.created_at.desc())
      .offset(skip)
      .limit(limit)
    )
    return list(result.scalars().all())

  async def find_all_by_status(
    self, status: str, skip: int = 0, limit: int = 100
  ) -> List[Order]:
    """根据状态获取订单"""
    result = await self.db.execute(
      select(Order)
      .filter(Order.status == status)
      .order_by(Order.created_at.desc())
      .offset(skip)
      .limit(limit)
    )
    return list(result.scalars().all())

  async def find_all_pending(self, user_id: str = None) -> List[Order]:
    """获取待处理订单"""
    stmt = select(Order).filter(Order.status == "PENDING")
    if user_id:
      stmt = stmt.filter(Order.user_id == user_id)
    stmt = stmt.order_by(Order.created_at.asc())

    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def create(self, order_data: Dict[str, Any]) -> Order:
    """创建订单"""
    order = Order(**order_data)
    self.db.add(order)
    await self.db.commit()
    await self.db.refresh(order)
    return order

  async def update(self, order_id: int, order_data: Dict[str, Any]) -> Optional[Order]:
    """更新订单"""
    order = await self.find_by_id(order_id)
    if order:
      for key, value in order_data.items():
        setattr(order, key, value)
      await self.db.commit()
      await self.db.refresh(order)
    return order

  async def delete(self, order_id: int) -> bool:
    """删除订单"""
    order = await self.find_by_id(order_id)
    if order:
      await self.db.delete(order)
      await self.db.commit()
      return True
    return False

  async def cancel_order(self, order_id: int) -> bool:
    """取消订单"""
    return await self.update(order_id, {"status": "CANCELLED"}) is not None

  async def complete_order(self, order_id: int, execute_time=None) -> bool:
    """完成订单"""
    from quantx_infrastructure.core.utils import time_utils

    update_data = {"status": "COMPLETED"}
    if execute_time:
      update_data["execute_time"] = execute_time
    else:
      update_data["execute_time"] = time_utils.now()

    return await self.update(order_id, update_data) is not None

  async def find_by_id(self, order_id: int) -> Optional[Order]:
    """根据ID获取订单"""
    return await super().find_by_id(order_id)

  async def find_all_by_date_range(
    self, account_id: str, start_date: str, end_date: str
  ) -> List[Order]:
    """根据日期范围查询委托"""
    from sqlalchemy import and_

    start_at = datetime.strptime(start_date, "%Y-%m-%d")
    end_before = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    result = await self.db.execute(
      select(Order)
      .filter(
        and_(
          Order.account_id == account_id,
          Order.time >= start_at,
          Order.time < end_before,
        )
      )
      .order_by(Order.time.desc())
    )
    return list(result.scalars().all())
