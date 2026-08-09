"""
XTQuant 工具函数
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Union

import numpy as np
import pandas as pd
from quantx_qmt_agent import clock

from ..manager_registry import XTDataManagerRegistry

logger = logging.getLogger(__name__)


def get_stock_name(stock_code: str) -> str:
  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()
  detail = data_manager.get_instrument_detail(stock_code)

  if detail is None:
    print("无法获取股票名称，可能是无效的股票代码", stock_code)
    return ""

  return detail.get("InstrumentName", "")


def normalize_stock_code(code: str) -> str:
  """
  标准化股票代码格式

  Args:
      code: 原始股票代码

  Returns:
      str: 标准化后的股票代码（如 '000001.SZ'）
  """
  code = str(code).strip().upper()

  # 如果已经包含后缀，直接返回
  if "." in code:
    return code

  # 根据代码前缀判断市场
  if code.startswith(("000", "002", "003", "300")):
    return f"{code}.SZ"  # 深圳
  elif code.startswith(("600", "601", "603", "605", "688")):
    return f"{code}.SH"  # 上海
  elif code.startswith(
    ("430", "831", "832", "833", "834", "835", "836", "837", "838", "839")
  ):
    return f"{code}.BJ"  # 北京
  else:
    # 默认深圳
    return f"{code}.SZ"


def batch_normalize_stock_codes(codes: List[str]) -> List[str]:
  """
  批量标准化股票代码

  Args:
      codes: 股票代码列表

  Returns:
      List: 标准化后的股票代码列表
  """
  return [normalize_stock_code(code) for code in codes]


def format_timestamp(timestamp: Union[str, int, float, datetime]) -> str:
  """
  格式化时间戳

  Args:
      timestamp: 时间戳

  Returns:
      str: 格式化后的时间字符串
  """
  if isinstance(timestamp, str):
    return timestamp
  elif isinstance(timestamp, datetime):
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")
  elif isinstance(timestamp, (int, float)):
    if timestamp > 1e10:  # 毫秒时间戳
      timestamp = timestamp / 1000
    return clock.to_shanghai(
      datetime.fromtimestamp(timestamp, timezone.utc)
    ).strftime("%Y-%m-%d %H:%M:%S")
  else:
    return str(timestamp)


def calculate_trading_days(start_date: str, end_date: str) -> int:
  """
  计算交易日天数（简单估算，不考虑节假日）

  Args:
      start_date: 开始日期
      end_date: 结束日期

  Returns:
      int: 交易日天数
  """
  try:
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)

    # 生成日期范围
    date_range = pd.date_range(start=start, end=end, freq="B")  # B表示工作日
    return len(date_range)
  except Exception as e:
    logger.error(f"计算交易日失败: {e}")
    return 0


def get_trading_calendar(year: int = None) -> List[str]:
  """
  获取交易日历（简化版本，实际使用时应该从交易所获取）

  Args:
      year: 年份，默认当前年份

  Returns:
      List: 交易日列表
  """
  if year is None:
    year = clock.now().year

  # 生成全年工作日
  start_date = f"{year}-01-01"
  end_date = f"{year}-12-31"

  trading_days = pd.date_range(start=start_date, end=end_date, freq="B")

  # 简单的节假日过滤（实际应该使用更准确的节假日数据）
  holidays = [
    f"{year}-01-01",  # 元旦
    f"{year}-05-01",  # 劳动节
    f"{year}-10-01",  # 国庆节
    # 可以添加更多节假日
  ]

  holidays = pd.to_datetime(holidays)
  trading_days = trading_days.difference(holidays)

  return [d.strftime("%Y-%m-%d") for d in trading_days]


def calculate_returns(prices: pd.Series) -> pd.Series:
  """
  计算收益率

  Args:
      prices: 价格序列

  Returns:
      Series: 收益率序列
  """
  return prices.pct_change()


def calculate_cumulative_returns(returns: pd.Series) -> pd.Series:
  """
  计算累计收益率

  Args:
      returns: 收益率序列

  Returns:
      Series: 累计收益率序列
  """
  return (1 + returns).cumprod() - 1


def calculate_max_drawdown(returns: pd.Series) -> float:
  """
  计算最大回撤

  Args:
      returns: 收益率序列

  Returns:
      float: 最大回撤比例
  """
  cumulative = calculate_cumulative_returns(returns)
  running_max = cumulative.expanding().max()
  drawdown = (cumulative - running_max) / (1 + running_max)
  return drawdown.min()


def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.03) -> float:
  """
  计算夏普比率

  Args:
      returns: 收益率序列
      risk_free_rate: 无风险利率（年化）

  Returns:
      float: 夏普比率
  """
  excess_returns = returns - risk_free_rate / 252  # 假设252个交易日
  return excess_returns.mean() / excess_returns.std() * np.sqrt(252)


def calculate_volatility(returns: pd.Series) -> float:
  """
  计算波动率（年化）

  Args:
      returns: 收益率序列

  Returns:
      float: 年化波动率
  """
  return returns.std() * np.sqrt(252)


def resample_data(
  df: pd.DataFrame, freq: str, price_col: str = "close"
) -> pd.DataFrame:
  """
  重采样数据到指定频率

  Args:
      df: 原始数据
      freq: 目标频率（如 '1H', '1D', '1W'）
      price_col: 价格列名

  Returns:
      DataFrame: 重采样后的数据
  """
  try:
    # 确保索引是日期时间类型
    if not isinstance(df.index, pd.DatetimeIndex):
      df.index = pd.to_datetime(df.index)

    # 重采样规则
    agg_dict = {
      price_col: "last",  # 收盘价取最后一个值
    }

    # 如果有OHLCV数据，使用相应的聚合方法
    if "open" in df.columns:
      agg_dict["open"] = "first"
    if "high" in df.columns:
      agg_dict["high"] = "max"
    if "low" in df.columns:
      agg_dict["low"] = "min"
    if "volume" in df.columns:
      agg_dict["volume"] = "sum"

    return df.resample(freq).agg(agg_dict).dropna()

  except Exception as e:
    logger.error(f"重采样数据失败: {e}")
    return df


def validate_stock_code(code: str) -> bool:
  """
  验证股票代码格式

  Args:
      code: 股票代码

  Returns:
      bool: 是否为有效格式
  """
  import re

  # 标准化代码
  normalized_code = normalize_stock_code(code)

  # 验证格式：6位数字.市场代码
  pattern = r"^\d{6}\.(SH|SZ|BJ)$"
  return bool(re.match(pattern, normalized_code))


def batch_validate_stock_codes(codes: List[str]) -> Dict[str, bool]:
  """
  批量验证股票代码

  Args:
      codes: 股票代码列表

  Returns:
      Dict: 验证结果字典
  """
  return {code: validate_stock_code(code) for code in codes}


def format_money(amount: float, currency: str = "¥") -> str:
  """
  格式化金额显示

  Args:
      amount: 金额
      currency: 货币符号

  Returns:
      str: 格式化后的金额字符串
  """
  if amount >= 1e8:
    return f"{currency}{amount / 1e8:.2f}亿"
  elif amount >= 1e4:
    return f"{currency}{amount / 1e4:.2f}万"
  else:
    return f"{currency}{amount:.2f}"


def retry_on_exception(max_retries: int = 3, delay: float = 1.0):
  """
  重试装饰器

  Args:
      max_retries: 最大重试次数
      delay: 重试间隔（秒）
  """

  def decorator(func):
    def wrapper(*args, **kwargs):
      for attempt in range(max_retries + 1):
        try:
          return func(*args, **kwargs)
        except Exception as e:
          if attempt == max_retries:
            logger.error(f"函数 {func.__name__} 执行失败，已达到最大重试次数: {e}")
            raise
          else:
            logger.warning(
              f"函数 {func.__name__} 执行失败，第 {attempt + 1} 次重试: {e}"
            )
            time.sleep(delay)
      return None

    return wrapper

  return decorator


class DataValidator:
  """数据验证器"""

  @staticmethod
  def validate_ohlcv_data(df: pd.DataFrame) -> bool:
    """验证OHLCV数据完整性"""
    required_columns = ["open", "high", "low", "close", "volume"]

    # 检查必要列是否存在
    if not all(col in df.columns for col in required_columns):
      return False

    # 检查数据是否为空
    if df.empty:
      return False

    # 检查价格逻辑关系
    price_check = (
      (df["high"] >= df["low"])
      & (df["high"] >= df["open"])
      & (df["high"] >= df["close"])
      & (df["low"] <= df["open"])
      & (df["low"] <= df["close"])
      & (df["volume"] >= 0)
    )

    return price_check.all()

  @staticmethod
  def clean_ohlcv_data(df: pd.DataFrame) -> pd.DataFrame:
    """清理OHLCV数据"""
    if df.empty:
      return df

    # 删除空值行
    df = df.dropna()

    # 修复价格逻辑错误
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)

    # 确保成交量非负
    df["volume"] = df["volume"].clip(lower=0)

    return df
