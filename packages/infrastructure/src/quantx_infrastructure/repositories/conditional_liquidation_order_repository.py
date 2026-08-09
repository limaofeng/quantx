"""条件清仓单仓储层。"""

from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder,
  ConditionalLiquidationStatus,
)


class ConditionalLiquidationOrderRepository(
  BaseRepository[ConditionalLiquidationOrder]
):
  """条件清仓单仓储实现。"""

  model_class = ConditionalLiquidationOrder

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_by_id(
    self, order_id: str
  ) -> Optional[ConditionalLiquidationOrder]:
    result = await self.db.execute(
      select(ConditionalLiquidationOrder).filter(
        ConditionalLiquidationOrder.id == order_id
      )
    )
    return result.scalar_one_or_none()

  async def find_latest_for_position(
    self,
    *,
    account_id: str,
    stock_code: str,
  ) -> Optional[ConditionalLiquidationOrder]:
    result = await self.db.execute(
      select(ConditionalLiquidationOrder)
      .filter(ConditionalLiquidationOrder.account_id == account_id)
      .filter(ConditionalLiquidationOrder.stock_code == stock_code)
      .filter(
        ConditionalLiquidationOrder.status.notin_(
          [
            ConditionalLiquidationStatus.CANCELLED,
            ConditionalLiquidationStatus.SUBMITTED,
          ]
        )
      )
      .order_by(desc(ConditionalLiquidationOrder.updated_at))
      .limit(1)
    )
    return result.scalar_one_or_none()

  async def find_all(
    self,
    *,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    include_cancelled: bool = False,
    limit: int = 100,
  ) -> List[ConditionalLiquidationOrder]:
    stmt = select(ConditionalLiquidationOrder)
    if account_id:
      stmt = stmt.filter(ConditionalLiquidationOrder.account_id == account_id)
    if stock_code:
      stmt = stmt.filter(ConditionalLiquidationOrder.stock_code == stock_code)
    if not include_cancelled:
      stmt = stmt.filter(
        ConditionalLiquidationOrder.status
        != ConditionalLiquidationStatus.CANCELLED
      )
    stmt = stmt.order_by(desc(ConditionalLiquidationOrder.updated_at)).limit(
      max(1, min(int(limit or 100), 500))
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_active(
    self,
    *,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
  ) -> List[ConditionalLiquidationOrder]:
    stmt = (
      select(ConditionalLiquidationOrder)
      .filter(ConditionalLiquidationOrder.enabled == True)  # noqa: E712
      .filter(
        ConditionalLiquidationOrder.status == ConditionalLiquidationStatus.ACTIVE
      )
    )
    if account_id:
      stmt = stmt.filter(ConditionalLiquidationOrder.account_id == account_id)
    if stock_code:
      stmt = stmt.filter(ConditionalLiquidationOrder.stock_code == stock_code)
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def create_order(
    self, order: ConditionalLiquidationOrder
  ) -> ConditionalLiquidationOrder:
    self.db.add(order)
    await self.db.commit()
    await self.db.refresh(order)
    return order

  async def update_order(
    self, order_id: str, updates: Dict[str, Any]
  ) -> Optional[ConditionalLiquidationOrder]:
    order = await self.find_by_id(order_id)
    if order:
      for key, value in dict(updates or {}).items():
        setattr(order, key, value)
      await self.db.commit()
      await self.db.refresh(order)
    return order
