"""
除权除息/复权因子数据仓储（PostgreSQL，异步）
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.divid_factor import DividFactor, DividFactorTable


class DividFactorRepository:
  """复权因子数据仓储（PostgreSQL，异步）"""

  def __init__(self, db_session: AsyncSession):
    self.db = db_session

  def _to_model(self, db_factor: DividFactorTable) -> DividFactor:
    return DividFactor(
      id=db_factor.id,
      stock_code=db_factor.stock_code,
      time=db_factor.time,
      ex_date=db_factor.ex_date,
      interest=db_factor.interest,
      stock_bonus=db_factor.stock_bonus,
      stock_gift=db_factor.stock_gift,
      allot_num=db_factor.allot_num,
      allot_price=db_factor.allot_price,
      gugai=db_factor.gugai,
      dr=db_factor.dr,
      created_at=db_factor.created_at,
      updated_at=db_factor.updated_at,
    )

  async def save(self, factor: DividFactor) -> DividFactor:
    """
    保存单个复权因子

    Args:
        factor: 复权因子对象

    Returns:
        保存后的复权因子对象
    """
    db_factor = DividFactorTable(
      stock_code=factor.stock_code,
      time=factor.time,
      ex_date=factor.ex_date,
      interest=factor.interest,
      stock_bonus=factor.stock_bonus,
      stock_gift=factor.stock_gift,
      allot_num=factor.allot_num,
      allot_price=factor.allot_price,
      gugai=factor.gugai,
      dr=factor.dr,
    )
    self.db.add(db_factor)
    await self.db.commit()
    await self.db.refresh(db_factor)

    factor.id = db_factor.id
    factor.created_at = db_factor.created_at
    factor.updated_at = db_factor.updated_at
    return factor

  async def bulk_save(self, factors: List[DividFactor]) -> int:
    """
    批量保存复权因子

    Args:
        factors: 复权因子列表

    Returns:
        保存的记录数
    """
    if not factors:
      return 0

    payload = [
      {
        "stock_code": f.stock_code,
        "time": f.time,
        "ex_date": f.ex_date,
        "interest": f.interest,
        "stock_bonus": f.stock_bonus,
        "stock_gift": f.stock_gift,
        "allot_num": f.allot_num,
        "allot_price": f.allot_price,
        "gugai": f.gugai,
        "dr": f.dr,
      }
      for f in factors
    ]
    await self.db.execute(insert(DividFactorTable), payload)
    await self.db.commit()
    return len(payload)

  async def find_by_stock_code(
    self,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
  ) -> List[DividFactor]:
    """
    根据股票代码查询复权因子

    Args:
        stock_code: 股票代码
        start_time: 开始时间
        end_time: 结束时间
        limit: 限制数量

    Returns:
        复权因子列表
    """
    query = select(DividFactorTable).filter(DividFactorTable.stock_code == stock_code)

    if start_time:
      query = query.filter(DividFactorTable.time >= start_time)
    if end_time:
      query = query.filter(DividFactorTable.time <= end_time)

    query = query.order_by(DividFactorTable.time.asc())
    if limit:
      query = query.limit(limit)

    result = await self.db.execute(query)
    db_factors = result.scalars().all()
    return [self._to_model(factor) for factor in db_factors]

  async def find_all(
    self,
    filters: dict = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
    order_by: str = "time ASC",
  ) -> List[DividFactor]:
    """
    查询复权因子

    Args:
        filters: 过滤条件
        start_time: 开始时间
        end_time: 结束时间
        limit: 限制数量
        order_by: 排序方式

    Returns:
        复权因子列表
    """
    query = select(DividFactorTable)

    if filters and "stock_code" in filters:
      query = query.filter(DividFactorTable.stock_code == filters["stock_code"])

    if start_time:
      query = query.filter(DividFactorTable.time >= start_time)
    if end_time:
      query = query.filter(DividFactorTable.time <= end_time)

    if order_by == "time ASC":
      query = query.order_by(DividFactorTable.time.asc())
    elif order_by == "time DESC":
      query = query.order_by(DividFactorTable.time.desc())

    if limit:
      query = query.limit(limit)

    result = await self.db.execute(query)
    db_factors = result.scalars().all()
    return [self._to_model(factor) for factor in db_factors]

  async def delete_by_stock_code(self, stock_code: str) -> int:
    """
    删除指定股票的复权因子

    Args:
        stock_code: 股票代码

    Returns:
        删除的记录数
    """
    result = await self.db.execute(
      delete(DividFactorTable).filter(DividFactorTable.stock_code == stock_code)
    )
    await self.db.commit()
    return result.rowcount
