"""
成交仓储层
处理成交记录相关的数据访问
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models.enums import OrderType
from models.trade import Trade


class TradeRepository(BaseRepository[Trade]):
  """成交仓储实现"""

  model_class = Trade

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_all(self, skip: int = 0, limit: int = 100) -> List[Trade]:
    """获取所有成交记录"""
    result = await self.db.execute(
      select(Trade).order_by(Trade.time.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all())

  async def find_all_by_account(
    self, account_id: str, skip: int = 0, limit: int = 100
  ) -> List[Trade]:
    """获取账户的成交记录"""
    result = await self.db.execute(
      select(Trade)
      .filter(Trade.account_id == account_id)
      .order_by(Trade.time.desc())
      .offset(skip)
      .limit(limit)
    )
    return list(result.scalars().all())

  async def find_all_by_date(
    self, trade_date: str, account_id: str = None
  ) -> List[Trade]:
    """根据交易日期获取成交记录"""
    query = select(Trade).filter(func.date(Trade.time) == trade_date)

    if account_id:
      query = query.filter(Trade.account_id == account_id)

    result = await self.db.execute(query.order_by(Trade.time.desc()))
    return list(result.scalars().all())

  async def find_all_by_stock_code(
    self, stock_code: str, account_id: str = None, skip: int = 0, limit: int = 100
  ) -> List[Trade]:
    """获取某只股票的成交记录"""
    query = select(Trade).filter(Trade.stock_code == stock_code)

    if account_id:
      query = query.filter(Trade.account_id == account_id)

    result = await self.db.execute(
      query.order_by(Trade.time.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all())

  async def find_all_by_order_id(self, order_id: int) -> List[Trade]:
    """获取订单的成交记录"""
    result = await self.db.execute(
      select(Trade).filter(Trade.order_id == order_id).order_by(Trade.time.desc())
    )
    return list(result.scalars().all())

  async def find_by_trade_id(self, trade_id: str) -> Optional[Trade]:
    """根据交易所成交编号获取成交记录"""
    if not trade_id:
      return None

    result = await self.db.execute(select(Trade).filter(Trade.id == trade_id))
    return result.scalar_one_or_none()

  async def find_all_by_date_range(
    self, start_date: str, end_date: str, account_id: str = None
  ) -> List[Trade]:
    """获取日期范围内的成交记录"""
    query = select(Trade).filter(
      and_(func.date(Trade.time) >= start_date, func.date(Trade.time) <= end_date)
    )

    if account_id:
      query = query.filter(Trade.account_id == account_id)

    result = await self.db.execute(query.order_by(Trade.time.desc()))
    return list(result.scalars().all())

  async def find_summary(self, account_id: str, trade_date: str) -> Dict[str, Any]:
    """获取成交汇总统计"""
    trades = await self.find_all_by_date(trade_date, account_id)

    buy_trades = [t for t in trades if int(t.order_type) == int(OrderType.BUY)]
    sell_trades = [t for t in trades if int(t.order_type) == int(OrderType.SELL)]

    return {
      "total_count": len(trades),
      "buy_count": len(buy_trades),
      "sell_count": len(sell_trades),
      "buy_amount": sum(float(t.amount) for t in buy_trades),
      "sell_amount": sum(float(t.amount) for t in sell_trades),
    }
