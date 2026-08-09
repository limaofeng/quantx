"""
除权除息/复权因子数据仓储（PostgreSQL，异步）
"""

from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.divid_factor import DividFactor, DividFactorTable


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

  async def replace_range(
    self,
    factors: List[DividFactor],
    *,
    stock_codes: List[str],
    start_ex_date: str,
    end_ex_date: str,
  ) -> dict[str, Any]:
    """Atomically replace an authoritative QMT factor window.

    ``divid_factors`` predates a uniqueness constraint, so a delete followed by
    a plain insert is the only idempotent write available on existing
    installations. Both operations and the exact-key verification are kept in
    one transaction; any mismatch rolls the deletion back.
    """
    codes = sorted(
      {
        str(code).strip().upper()
        for code in stock_codes
        if str(code).strip()
      }
    )
    if not codes:
      raise ValueError("stock_codes must not be empty")
    for label, value in (
      ("start_ex_date", start_ex_date),
      ("end_ex_date", end_ex_date),
    ):
      if len(value) != 8 or not value.isdigit():
        raise ValueError(f"{label} must be YYYYMMDD")
    if end_ex_date < start_ex_date:
      raise ValueError("end_ex_date precedes start_ex_date")

    expected_keys: list[tuple[str, str]] = []
    payload: list[dict[str, Any]] = []
    for factor in factors:
      code = str(factor.stock_code or "").strip().upper()
      ex_date = str(factor.ex_date or "").strip()
      if code not in codes:
        raise ValueError(f"factor code is outside replacement scope: {code}")
      if ex_date < start_ex_date or ex_date > end_ex_date:
        raise ValueError(
          f"factor ex_date is outside replacement scope: {code}/{ex_date}"
        )
      if factor.time is None or factor.dr is None or factor.dr <= 0:
        raise ValueError(f"factor time/dr is invalid: {code}/{ex_date}")
      expected_keys.append((code, ex_date))
      payload.append(
        {
          "stock_code": code,
          "time": factor.time,
          "ex_date": ex_date,
          "interest": factor.interest,
          "stock_bonus": factor.stock_bonus,
          "stock_gift": factor.stock_gift,
          "allot_num": factor.allot_num,
          "allot_price": factor.allot_price,
          "gugai": factor.gugai,
          "dr": factor.dr,
        }
      )
    if len(set(expected_keys)) != len(expected_keys):
      raise ValueError("replacement payload contains duplicate code/ex_date keys")

    scope = (
      DividFactorTable.stock_code.in_(codes),
      DividFactorTable.ex_date >= start_ex_date,
      DividFactorTable.ex_date <= end_ex_date,
    )
    try:
      prior = (
        await self.db.execute(
          select(
            func.count(DividFactorTable.id),
            func.min(DividFactorTable.ex_date),
            func.max(DividFactorTable.ex_date),
          ).where(*scope)
        )
      ).one()
      deleted = await self.db.execute(delete(DividFactorTable).where(*scope))
      if payload:
        await self.db.execute(insert(DividFactorTable), payload)
      persisted_keys = (
        await self.db.execute(
          select(
            DividFactorTable.stock_code,
            DividFactorTable.ex_date,
          )
          .where(*scope)
          .order_by(
            DividFactorTable.stock_code.asc(),
            DividFactorTable.ex_date.asc(),
          )
        )
      ).all()
      actual_keys = [
        (str(stock_code), str(ex_date))
        for stock_code, ex_date in persisted_keys
      ]
      sorted_expected = sorted(expected_keys)
      if actual_keys != sorted_expected:
        raise RuntimeError(
          "divid factor replacement exact-key verification failed: "
          f"expected={len(sorted_expected)} actual={len(actual_keys)}"
        )
      await self.db.commit()
    except Exception:
      await self.db.rollback()
      raise

    return {
      "stock_count": len(codes),
      "prior_count": int(prior[0] or 0),
      "prior_min_ex_date": str(prior[1] or ""),
      "prior_max_ex_date": str(prior[2] or ""),
      "deleted_count": int(deleted.rowcount or 0),
      "inserted_count": len(payload),
      "verified_count": len(actual_keys),
      "start_ex_date": start_ex_date,
      "end_ex_date": end_ex_date,
    }

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
