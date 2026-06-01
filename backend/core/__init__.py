"""QuantX core package.

Core exports are loaded lazily so importing a low-level module such as
``core.brokers.base`` does not initialize realtime, GraphQL, Prefect, or broker
integrations.
"""

__version__ = "1.0.0"

_LAZY_EXPORTS = {
  "COMMON_PARAMETER_SCHEMAS": ("core.config", "COMMON_PARAMETER_SCHEMAS"),
  "ParameterManager": ("core.config", "ParameterManager"),
  "parameter_manager": ("core.config", "parameter_manager"),
  "RealTimeDataManager": ("core.realtime_manager", "RealTimeDataManager"),
  "realtime_manager": ("core.realtime_manager", "realtime_manager"),
  "ExecutionStatus": ("core.strategy_executor", "ExecutionStatus"),
  "StrategyExecutor": ("core.strategy_executor", "StrategyExecutor"),
  "StrategyRuntime": ("core.strategy_executor", "StrategyRuntime"),
  "StrategyManager": ("core.strategy_manager", "StrategyManager"),
  "strategy_manager": ("core.strategy_manager", "strategy_manager"),
}


def __getattr__(name):
  if name not in _LAZY_EXPORTS:
    raise AttributeError(f"module 'core' has no attribute {name!r}")
  from importlib import import_module

  module_name, attr_name = _LAZY_EXPORTS[name]
  value = getattr(import_module(module_name), attr_name)
  globals()[name] = value
  return value


__all__ = [
  # 策略管理
  "StrategyManager",
  "strategy_manager",
  # 策略执行
  "StrategyExecutor",
  "ExecutionStatus",
  "StrategyRuntime",
  # 参数管理
  "ParameterManager",
  "parameter_manager",
  "COMMON_PARAMETER_SCHEMAS",
  # 实时数据管理
  "RealTimeDataManager",
  "realtime_manager",
]
