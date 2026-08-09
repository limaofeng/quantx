"""
历史市场数据服务（异步版本 - 使用 PostgreSQL 复权因子）
"""

import logging
from datetime import datetime
from typing import List, Optional

import pandas as pd

from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.repositories.kline_repository import KLineRepository
from quantx_infrastructure.services.divid_factor_service import DividFactorService
from quantx_infrastructure.services.instrument_service import InstrumentService


class HistoricalMarketDataServiceAsync:
  """历史市场数据服务类（异步版本）"""

  _BASE_PERIODS = {"1m", "1d"}
  _INTRADAY_AGG_MAP = {
    "5m": "5min",
    "15m": "15min",
    "60m": "60min",
  }

  def __init__(self):
    self.logger = logging.getLogger(__name__)
    self.stock_service = InstrumentService()
    self.kline_repo = KLineRepository()
    self.divid_factor_service = DividFactorService()

  async def _apply_dividend_adjustment(
    self, klines: List[KLine], stock_code: str, dividend_type: str
  ) -> List[KLine]:
    """
    应用复权因子（异步版本）

    Args:
        klines: K线数据列表
        stock_code: 股票代码
        dividend_type: 复权类型 ('front', 'back')

    Returns:
        复权后的K线数据列表
    """
    if not klines:
      return []

    if dividend_type not in ["front", "back"]:
      return klines

    times = [kline.time for kline in klines if kline.time is not None]
    if not times:
      return klines

    start_time = min(times)
    end_time = max(times)

    factors = await self.divid_factor_service.get_divid_factors(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=None,
    )

    # DolphinDB 返回的是对象列表，需要转换为字典
    factor_rows = []
    for factor in factors:
      if hasattr(factor, 'time') and hasattr(factor, 'dr'):
        factor_rows.append({
          "time": factor.time,
          "dr": float(factor.dr) if factor.dr else 1.0
        })

    if not factors:
      self.logger.warning(f"没有找到复权因子: {stock_code}")
      return klines

    if not factor_rows:
      return klines

    factor_df = pd.DataFrame(factor_rows).sort_values("time")
    factor_df = factor_df[
      pd.to_datetime(factor_df["time"]) <= pd.Timestamp(end_time)
    ]
    factor_df["dr"] = pd.to_numeric(factor_df["dr"], errors="coerce").fillna(1.0)
    factor_df = factor_df[factor_df["dr"] > 0]

    if factor_df.empty:
      return klines

    # 计算累积复权因子
    factor_df["cum_factor"] = factor_df["dr"].cumprod()
    total_factor = float(factor_df["cum_factor"].iloc[-1])

    # 时间对齐
    kline_df = pd.DataFrame({"time": [k.time for k in klines]})
    kline_df = kline_df.sort_values("time")

    aligned = pd.merge_asof(
      kline_df,
      factor_df[["time", "cum_factor"]],
      on="time",
      direction="backward",
    )

    aligned["cum_factor"] = aligned["cum_factor"].fillna(1.0)

    # 计算复权系数
    if dividend_type == "front":
      aligned["adjust_factor"] = aligned["cum_factor"] / total_factor
    else:
      aligned["adjust_factor"] = aligned["cum_factor"]

    adjust_factors = dict(zip(aligned["time"], aligned["adjust_factor"]))

    # 应用复权因子
    adjusted = []
    for kline in klines:
      factor = adjust_factors.get(kline.time, 1.0)
      adjusted.append(
        KLine(
          stock_code=kline.stock_code,
          period=kline.period,
          time=kline.time,
          open=kline.open * factor,
          high=kline.high * factor,
          low=kline.low * factor,
          close=kline.close * factor,
          pre_close=kline.pre_close * factor,
          volume=kline.volume,
          amount=kline.amount,
          settelement_price=kline.settelement_price * factor,
          open_interest=kline.open_interest,
          suspend_flag=kline.suspend_flag,
        )
      )

    return adjusted

  async def get_adjusted_klines(
    self,
    stock_code: str,
    period: str,
    start_time: datetime,
    end_time: datetime,
    dividend_type: str = "front",
    limit: Optional[int] = None,
  ) -> List[KLine]:
    """
    获取复权后的K线数据（异步版本）

    Args:
        stock_code: 股票代码
        period: 周期
        start_time: 开始时间
        end_time: 结束时间
        dividend_type: 复权类型
        limit: 限制数量

    Returns:
        复权后的K线数据列表
    """
    try:
      # 查询原始K线数据
      klines = self.kline_repo.find_by_stock_code_and_period(
        stock_code=stock_code,
        period=period,
        start=start_time,
        end=end_time,
        limit=limit,
      )

      if not klines:
        return []

      # 应用复权
      adjusted_klines = await self._apply_dividend_adjustment(
        klines=klines,
        stock_code=stock_code,
        dividend_type=dividend_type
      )

      return adjusted_klines

    except Exception as e:
      self.logger.error(f"获取复权K线数据失败: {e}")
      return []
