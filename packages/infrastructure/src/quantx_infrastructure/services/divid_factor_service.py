"""
除权因子数据服务（PostgreSQL，异步）
"""

import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

import pandas as pd

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.divid_factor import DividFactor
from quantx_infrastructure.repositories.divid_factor_repository import (
  DividFactorRepository,
)

FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")


class DividFactorService:
  """除权因子服务类"""

  def __init__(self):
    self.logger = logging.getLogger(__name__)

  def _normalize_query_time(
    self, value: Optional[datetime]
  ) -> Optional[datetime]:
    if value is None:
      return None
    if isinstance(value, pd.Timestamp):
      value = value.to_pydatetime()
    if not isinstance(value, datetime):
      return None
    return time_utils.to_shanghai(value)

  def _normalize_factors(self, stock_code: str, df: pd.DataFrame) -> List[DividFactor]:
    """
    标准化复权因子数据

    Args:
        stock_code: 股票代码
        df: 原始数据DataFrame

    Returns:
        标准化后的复权因子列表
    """
    if df is None or df.empty:
      return []

    factors = df.copy()
    factors = factors.reset_index().rename(columns={"index": "ex_date"})
    factors["stock_code"] = stock_code

    if "time" not in factors.columns:
      self.logger.warning(f"{stock_code} 除权因子缺少 time 字段")
      return []

    # 转换时间（转换为上海时区后去掉时区信息，以适配 TIMESTAMP WITHOUT TIME ZONE）
    factors["time"] = pd.to_datetime(
      factors["time"], unit="ms", utc=True
    ).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)

    # 重命名字段
    factors.rename(
      columns={
        "stockBonus": "stock_bonus",
        "stockGift": "stock_gift",
        "allotNum": "allot_num",
        "allotPrice": "allot_price",
      },
      inplace=True,
    )

    # 处理数值字段
    numeric_columns = [
      "interest",
      "stock_bonus",
      "stock_gift",
      "allot_num",
      "allot_price",
      "gugai",
      "dr",
    ]
    for col in numeric_columns:
      if col in factors.columns:
        factors[col] = pd.to_numeric(factors[col], errors="coerce")
      else:
        factors[col] = 0.0

    factors[numeric_columns] = factors[numeric_columns].fillna(0.0)
    factors["ex_date"] = factors["ex_date"].astype(str)

    # 转换为 DividFactor 对象列表
    result = []
    for _, row in factors.iterrows():
      try:
        factor = DividFactor(
          stock_code=stock_code,
          time=row["time"].to_pydatetime() if pd.notna(row["time"]) else None,
          ex_date=str(row["ex_date"]) if pd.notna(row["ex_date"]) else "",
          interest=Decimal(str(row["interest"])).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          stock_bonus=Decimal(str(row["stock_bonus"])).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          stock_gift=Decimal(str(row["stock_gift"])).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          allot_num=Decimal(str(row["allot_num"])).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          allot_price=Decimal(str(row["allot_price"])).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          gugai=Decimal(str(row["gugai"])).quantize(
            FOUR_PLACES,
            rounding=ROUND_HALF_UP,
          ),
          dr=Decimal(str(row["dr"])).quantize(
            SIX_PLACES,
            rounding=ROUND_HALF_UP,
          ),
        )
        result.append(factor)
      except Exception as e:
        self.logger.error(f"转换复权因子失败: {e}, row: {row}")
        continue

    return result

  async def save_divid_factors(self, stock_code: str, df: pd.DataFrame) -> int:
    """
    保存复权因子

    Args:
        stock_code: 股票代码
        df: 复权因子数据

    Returns:
        保存的记录数
    """
    normalized = self._normalize_factors(stock_code, df)
    if not normalized:
      return 0

    async for db in get_async_db():
      repo = DividFactorRepository(db)
      return await repo.bulk_save(normalized)

    return 0

  async def save_batch_divid_factors(self, factors_map: Dict[str, pd.DataFrame]) -> int:
    """
    批量保存复权因子

    Args:
        factors_map: 股票代码到复权因子数据的映射

    Returns:
        保存的记录数
    """
    if not factors_map:
      return 0

    all_factors = []
    for stock_code, df in factors_map.items():
      normalized = self._normalize_factors(stock_code, df)
      if normalized:
        all_factors.extend(normalized)

    if not all_factors:
      return 0

    async for db in get_async_db():
      repo = DividFactorRepository(db)
      return await repo.bulk_save(all_factors)

    return 0

  async def replace_batch_divid_factors(
    self,
    factors_map: Dict[str, pd.DataFrame],
    *,
    stock_codes: List[str],
    start_ex_date: str,
    end_ex_date: str,
  ) -> dict[str, Any]:
    """Replace one exact QMT factor window, including valid empty results."""
    all_factors: List[DividFactor] = []
    for stock_code, df in factors_map.items():
      normalized = self._normalize_factors(stock_code, df)
      if normalized:
        all_factors.extend(normalized)

    async for db in get_async_db():
      repo = DividFactorRepository(db)
      return await repo.replace_range(
        all_factors,
        stock_codes=stock_codes,
        start_ex_date=start_ex_date,
        end_ex_date=end_ex_date,
      )

    raise RuntimeError("数据库会话不可用")

  async def get_divid_factors(
    self,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
  ) -> List[DividFactor]:
    """
    获取复权因子

    Args:
        stock_code: 股票代码
        start_time: 开始时间
        end_time: 结束时间
        limit: 限制数量

    Returns:
        复权因子列表
    """
    start_time = self._normalize_query_time(start_time)
    end_time = self._normalize_query_time(end_time)

    async for db in get_async_db():
      repo = DividFactorRepository(db)
      return await repo.find_by_stock_code(
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
      )

    return []

  async def delete_divid_factors(self, stock_code: str) -> int:
    """
    删除指定股票的复权因子

    Args:
        stock_code: 股票代码

    Returns:
        删除的记录数
    """
    async for db in get_async_db():
      repo = DividFactorRepository(db)
      return await repo.delete_by_stock_code(stock_code)

    return 0
