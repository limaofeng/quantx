"""K 线数据仓储实现。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.timeseries_base import BaseRepository
from quantx_infrastructure.models.kline import KLine

logger = logging.getLogger(__name__)

_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
_SUPPORTED_PERIODS = {"1d", "1m", "tick"}


def _validated_period(period: str) -> str:
  normalized = str(period or "").lower()
  if normalized not in _SUPPORTED_PERIODS:
    raise ValueError(f"不支持的 K 线周期: {period}")
  return normalized


def _validated_code(stock_code: str) -> str:
  normalized = str(stock_code or "").strip().upper()
  if not _CODE_PATTERN.fullmatch(normalized):
    raise ValueError(f"无效的沪深标的代码: {stock_code}")
  return normalized


class KLineRepository(BaseRepository[KLine]):
  """K 线数据仓储。

  每个周期使用独立 measurement（例如 ``kline_1d``）。批量读取方法只接受
  经过格式校验的沪深代码，避免把调用方参数直接拼接到 Influx SQL。
  """

  model_class = KLine

  @staticmethod
  def _measurement(period: str) -> str:
    return f"kline_{_validated_period(period)}"

  def find_by_stock_code_and_period(
    self,
    stock_code: str,
    period: str,
    start: datetime,
    end: datetime,
    limit: Optional[int] = None,
    offset: int = 0,
    use_cache: bool = True,
  ) -> List[KLine]:
    """按标的、周期和时间范围查询 K 线。"""
    del use_cache
    return self.find_all(
      measurement=self._measurement(period),
      filters={
        "stock_code": _validated_code(stock_code),
        "period": _validated_period(period),
      },
      start_time=start,
      end_time=end,
      limit=limit,
      offset=offset,
      order_by="time ASC",
      as_frame=False,
    )

  def find_by_period_and_time_range(
    self,
    period: str,
    start: datetime,
    end: datetime,
    stock_codes: Optional[List[str]] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    use_cache: bool = True,
  ) -> List[KLine]:
    """按周期和时间范围查询 K 线。

    旧接口仍返回实体列表；多标的调用内部复用批量 SQL，不再依赖已经移除的
    ``influx_manager`` / ``measurement_name`` / ``_execute_query`` 成员。
    """
    normalized_period = _validated_period(period)
    if not stock_codes:
      del use_cache
      return self.find_all(
        measurement=self._measurement(normalized_period),
        filters={"period": normalized_period},
        start_time=start,
        end_time=end,
        limit=limit,
        offset=offset,
        order_by="time ASC",
        as_frame=False,
      )

    frames = self._find_batch(
      stock_codes=stock_codes,
      period=normalized_period,
      start=start,
      end=end,
      use_cache=use_cache,
    )
    entities: List[KLine] = []
    rows_seen = 0
    for code in sorted(frames):
      for row in frames[code].to_dict(orient="records"):
        if rows_seen < offset:
          rows_seen += 1
          continue
        entities.append(KLine(**row))
        rows_seen += 1
        if limit is not None and len(entities) >= limit:
          return entities
    return entities

  def find_latest_by_stock_code_and_period(
    self, stock_code: str, period: str, limit: int = 1
  ) -> List[KLine]:
    """查询指定标的和周期的最新 K 线。"""
    return self.find_all(
      measurement=self._measurement(period),
      filters={
        "stock_code": _validated_code(stock_code),
        "period": _validated_period(period),
      },
      order_by="time DESC",
      limit=max(1, int(limit)),
      as_frame=False,
    )

  def find_daily_batch(
    self,
    stock_codes: List[str],
    start: datetime,
    end: datetime,
    *,
    use_cache: bool = False,
  ) -> Dict[str, pd.DataFrame]:
    """一次读取一批标的的 ``kline_1d`` 公共历史区间并按代码分组。"""
    return self._find_batch(
      stock_codes=stock_codes,
      period="1d",
      start=start,
      end=end,
      use_cache=use_cache,
    )

  def summarize_daily_batch(
    self,
    stock_codes: List[str],
    start: datetime,
    end: datetime,
    *,
    use_cache: bool = False,
  ) -> Dict[str, Dict[str, Any]]:
    """聚合验收一批日线，避免把全部明细拉回进程。"""
    codes = list(dict.fromkeys(_validated_code(code) for code in stock_codes))
    if not codes:
      return {}
    if end < start:
      raise ValueError("K 线查询结束时间不能早于开始时间")

    quoted_codes = ", ".join(f"'{code}'" for code in codes)
    sql = (
      "SELECT stock_code, "
      "COUNT(*) AS row_count, "
      "COUNT(DISTINCT time) AS distinct_times, "
      "MIN(time) AS min_time, "
      "MAX(time) AS max_time, "
      "SUM(CASE WHEN "
      "open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL "
      "OR volume IS NULL OR amount IS NULL "
      "OR open <= 0 OR close <= 0 "
      "OR high < open OR high < close "
      "OR low > open OR low > close "
      "OR volume < 0 OR amount < 0 "
      "THEN 1 ELSE 0 END) AS invalid_rows "
      "FROM kline_1d "
      "WHERE period = '1d' "
      f"AND stock_code IN ({quoted_codes}) "
      f"AND time >= '{start.isoformat()}' "
      f"AND time <= '{end.isoformat()}' "
      "GROUP BY stock_code"
    )
    rows = self.operations.query(sql, use_cache=use_cache)
    return {
      str(row["stock_code"]).upper(): {
        "row_count": int(row.get("row_count") or 0),
        "distinct_times": int(row.get("distinct_times") or 0),
        "invalid_rows": int(row.get("invalid_rows") or 0),
        "min_time": row.get("min_time"),
        "max_time": row.get("max_time"),
      }
      for row in rows
      if row.get("stock_code")
    }

  def find_daily_keys_batch(
    self,
    stock_codes: List[str],
    start: datetime,
    end: datetime,
    *,
    use_cache: bool = False,
  ) -> Dict[str, List[Any]]:
    """读取一批日线的精确 ``(stock_code, time)`` 键用于入库验收。"""
    codes = list(dict.fromkeys(_validated_code(code) for code in stock_codes))
    if not codes:
      return {}
    if end < start:
      raise ValueError("K 线查询结束时间不能早于开始时间")

    quoted_codes = ", ".join(f"'{code}'" for code in codes)
    sql = (
      "SELECT stock_code, time "
      "FROM kline_1d "
      "WHERE period = '1d' "
      f"AND stock_code IN ({quoted_codes}) "
      f"AND time >= '{start.isoformat()}' "
      f"AND time <= '{end.isoformat()}' "
      "ORDER BY stock_code ASC, time ASC"
    )
    rows = self.operations.query(sql, use_cache=use_cache)
    result: Dict[str, List[Any]] = {}
    for row in rows:
      code = str(row.get("stock_code") or "").upper()
      if code and row.get("time") is not None:
        result.setdefault(code, []).append(row["time"])
    return result

  def _find_batch(
    self,
    *,
    stock_codes: List[str],
    period: str,
    start: datetime,
    end: datetime,
    use_cache: bool,
  ) -> Dict[str, pd.DataFrame]:
    normalized_period = _validated_period(period)
    codes = list(dict.fromkeys(_validated_code(code) for code in stock_codes))
    if not codes:
      return {}
    if end < start:
      raise ValueError("K 线查询结束时间不能早于开始时间")

    quoted_codes = ", ".join(f"'{code}'" for code in codes)
    sql = (
      "SELECT * "
      f"FROM {self._measurement(normalized_period)} "
      f"WHERE period = '{normalized_period}' "
      f"AND stock_code IN ({quoted_codes}) "
      f"AND time >= '{start.isoformat()}' "
      f"AND time <= '{end.isoformat()}' "
      "ORDER BY stock_code ASC, time ASC"
    )
    rows = self.operations.query(sql, use_cache=use_cache)
    if not rows:
      return {}

    frame = pd.DataFrame(rows)
    if "stock_code" not in frame.columns or "time" not in frame.columns:
      raise RuntimeError("InfluxDB K 线查询结果缺少 stock_code/time 字段")
    frame["stock_code"] = frame["stock_code"].astype(str).str.upper()
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["time"]).sort_values(["stock_code", "time"])
    return {
      str(code): group.reset_index(drop=True)
      for code, group in frame.groupby("stock_code", sort=False)
    }

  def get_kline_summary(
    self, stock_code: str, period: str, days: int = 30
  ) -> Dict[str, Any]:
    """获取 K 线数据摘要统计。"""
    end_time = time_utils.now()
    start_time = end_time - timedelta(days=days)
    kline_data = self.find_by_stock_code_and_period(
      stock_code, period, start_time, end_time
    )
    if not kline_data:
      return {}

    prices = [float(item.close) for item in kline_data]
    volumes = [float(item.volume) for item in kline_data]
    return {
      "count": len(kline_data),
      "period": period,
      "start_time": start_time,
      "end_time": end_time,
      "price_summary": {
        "max": max(prices),
        "min": min(prices),
        "avg": sum(prices) / len(prices),
        "first": prices[0],
        "last": prices[-1],
      },
      "volume_summary": {
        "max": max(volumes),
        "min": min(volumes),
        "avg": sum(volumes) / len(volumes),
        "total": sum(volumes),
      },
    }

  def delete_by_stock_code_and_period(
    self,
    stock_code: str,
    period: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
  ) -> bool:
    """删除指定标的和周期的 K 线。"""
    normalized_period = _validated_period(period)
    normalized_code = _validated_code(stock_code)
    conditions = [
      f"stock_code = '{normalized_code}'",
      f"period = '{normalized_period}'",
    ]
    if start:
      conditions.append(f"time >= '{start.isoformat()}'")
    if end:
      conditions.append(f"time <= '{end.isoformat()}'")
    sql = (
      f"DELETE FROM {self._measurement(normalized_period)} WHERE "
      + " AND ".join(conditions)
    )
    try:
      self.operations.query(sql, use_cache=False)
      return True
    except Exception:
      logger.exception("删除 K 线失败: %s %s", normalized_code, normalized_period)
      return False
