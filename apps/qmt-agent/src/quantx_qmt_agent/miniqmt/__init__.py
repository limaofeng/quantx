"""
XTQuant API 封装主模块
"""

# 优先导入配置和工具函数
try:
  from .config.config_manager import XTQuantConfig, xt_config
except ImportError as e:
  print(f"Warning: Failed to import config manager: {e}")
  XTQuantConfig = None
  xt_config = None

try:
  from .utils.helpers import (
    DataValidator,
    batch_normalize_stock_codes,
    batch_validate_stock_codes,
    calculate_cumulative_returns,
    calculate_max_drawdown,
    calculate_returns,
    calculate_sharpe_ratio,
    calculate_trading_days,
    calculate_volatility,
    format_money,
    format_timestamp,
    get_trading_calendar,
    normalize_stock_code,
    resample_data,
    retry_on_exception,
    validate_stock_code,
  )
except ImportError as e:
  print(f"Warning: Failed to import utils: {e}")

from .data.data_manager import XTDataManager
from .manager_registry import XTDataManagerRegistry, XTTradingManagerRegistry
from .trading.trading_manager import (
  OrderPriceType,
  OrderType,
  XTTradingManager,
)

__version__ = "1.0.0"
__author__ = "QuantX Team"

__all__ = [
  # 数据管理
  "XTDataManager",
  # 交易管理
  "XTTradingManager",
  "OrderType",
  "OrderPriceType",
  "XTTradingManagerRegistry",
  "XTDataManagerRegistry",
  # 配置管理
  "XTQuantConfig",
  "xt_config",
  # 工具函数
  "normalize_stock_code",
  "batch_normalize_stock_codes",
  "format_timestamp",
  "calculate_trading_days",
  "get_trading_calendar",
  "calculate_returns",
  "calculate_cumulative_returns",
  "calculate_max_drawdown",
  "calculate_sharpe_ratio",
  "calculate_volatility",
  "resample_data",
  "validate_stock_code",
  "batch_validate_stock_codes",
  "format_money",
  "retry_on_exception",
  "DataValidator",
]
