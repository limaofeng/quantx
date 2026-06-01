"""
服务配置
"""

from services.divid_factor_service import DividFactorService
from services.historical_market_data_service import HistoricalMarketDataService
from services.historical_market_data_service_async import HistoricalMarketDataServiceAsync

# ============================================
# 服务切换配置
# ============================================

# 复权因子服务版本（仅保留 PostgreSQL 异步版本）
USE_POSTGRESQL_DIVID_FACTOR = True

# 历史数据服务版本
USE_ASYNC_HISTORICAL_SERVICE = True  # True: 异步版本, False: 同步版本


def get_divid_factor_service():
  """
  获取复权因子服务实例

  Returns:
      DividFactorService
  """
  return DividFactorService()


def get_historical_market_data_service():
  """
  获取历史市场数据服务实例

  Returns:
      HistoricalMarketDataServiceAsync 或 HistoricalMarketDataService
  """
  if USE_ASYNC_HISTORICAL_SERVICE:
    return HistoricalMarketDataServiceAsync()
  else:
    return HistoricalMarketDataService()


# ============================================
# 使用示例
# ============================================

# 方式1: 直接使用配置
def example_usage_1():
  """使用示例1"""
  divid_service = get_divid_factor_service()
  market_service = get_historical_market_data_service()

  # 根据配置，可能是同步或异步版本
  # 如果是异步版本，需要用 await
  # if USE_ASYNC_HISTORICAL_SERVICE:
  #     klines = await market_service.get_adjusted_klines(...)
  # else:
  #     klines = market_service.get_adjusted_klines(...)


# 方式2: 显式指定
def example_usage_2():
  """使用示例2"""
  # 使用 PostgreSQL 版本
  divid_service = DividFactorService()

  # 使用异步版本
  market_service_async = HistoricalMarketDataServiceAsync()


# 方式3: 依赖注入
class TradingStrategy:
  """交易策略类"""

  def __init__(self, divid_service=None, market_service=None):
    self.divid_service = divid_service or get_divid_factor_service()
    self.market_service = market_service or get_historical_market_data_service()
