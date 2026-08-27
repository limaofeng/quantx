"""
XTQuant 配置管理
"""

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


class XTQuantConfig:
  """XTQuant配置管理类"""

  def __init__(self, config_file: str = None):
    self.config_file = config_file or os.path.join(
      os.path.dirname(__file__), "config.json"
    )
    self.config = self._load_config()

  def _load_config(self) -> Dict[str, Any]:
    """加载配置文件"""
    default_config = {
      "xtquant": {
        "data_server": {
          "host": "127.0.0.1",
          "port": 58610,
          "username": "",
          "password": "",
        },
        "trading_server": {
          "host": "127.0.0.1",
          "port": 58611,
          "username": "",
          "password": "",
        },
        "account": {"account_id": "", "account_type": "stock"},
      },
      "data": {
        "cache_enabled": True,
        "cache_dir": "./cache",
        "update_interval": 1000,  # 毫秒
        "max_retry": 3,
      },
      "trading": {
        "risk_control": {
          "max_position_ratio": 0.95,  # 最大仓位比例
          "max_single_stock_ratio": 0.20,  # 单只股票最大仓位比例
          "stop_loss_ratio": 0.05,  # 止损比例
          "take_profit_ratio": 0.15,  # 止盈比例
        },
        "order_settings": {
          "default_price_type": "limit",  # 默认价格类型
          "order_timeout": 60,  # 订单超时时间（秒）
          "max_order_size": 10000,  # 最大单笔订单数量
        },
      },
      "strategy": {
        "max_instances": 50,  # 最大策略实例数
        "execution_interval": 1,  # 策略执行间隔（秒）
        "log_level": "INFO",
      },
      "database": {
        "enabled": False,
        "type": "sqlite",
        "connection_string": "sqlite:///quantx.db",
      },
    }

    if os.path.exists(self.config_file):
      try:
        with open(self.config_file, "r", encoding="utf-8") as f:
          config = json.load(f)
          # 合并默认配置
          default_config.update(config)
          return default_config
      except Exception as exc:
        logger.error("加载配置文件失败: error=%s", exc.__class__.__name__)
        return default_config
    else:
      # 创建默认配置文件
      self._save_config(default_config)
      return default_config

  def _save_config(self, config: Dict[str, Any]):
    """保存配置文件"""
    try:
      os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
      with open(self.config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
      logger.info("配置文件已保存")
    except Exception as exc:
      logger.error("保存配置文件失败: error=%s", exc.__class__.__name__)

  def get(self, key: str, default=None) -> Any:
    """获取配置值"""
    keys = key.split(".")
    value = self.config

    for k in keys:
      if isinstance(value, dict) and k in value:
        value = value[k]
      else:
        return default

    return value

  def set(self, key: str, value: Any):
    """设置配置值"""
    keys = key.split(".")
    config = self.config

    for k in keys[:-1]:
      if k not in config:
        config[k] = {}
      config = config[k]

    config[keys[-1]] = value
    self._save_config(self.config)

  def get_xtquant_config(self) -> Dict[str, Any]:
    """获取XTQuant相关配置"""
    return self.get("xtquant", {})

  def get_data_config(self) -> Dict[str, Any]:
    """获取数据相关配置"""
    return self.get("data", {})

  def get_trading_config(self) -> Dict[str, Any]:
    """获取交易相关配置"""
    return self.get("trading", {})

  def get_strategy_config(self) -> Dict[str, Any]:
    """获取策略相关配置"""
    return self.get("strategy", {})

  def update_account_config(self, account_id: str, account_type: str = "stock"):
    """更新账户配置"""
    self.set("xtquant.account.account_id", account_id)
    self.set("xtquant.account.account_type", account_type)

  def is_risk_control_enabled(self) -> bool:
    """是否启用风控"""
    return self.get("trading.risk_control.enabled", True)

  def get_max_position_ratio(self) -> float:
    """获取最大仓位比例"""
    return self.get("trading.risk_control.max_position_ratio", 0.95)

  def get_max_single_stock_ratio(self) -> float:
    """获取单只股票最大仓位比例"""
    return self.get("trading.risk_control.max_single_stock_ratio", 0.20)

  def save(self):
    """保存当前配置到文件"""
    self._save_config(self.config)

  def validate(self) -> bool:
    """验证配置的有效性"""
    try:
      # 验证必要的配置项
      xtquant_config = self.get("xtquant", {})
      data_config = self.get("data", {})
      trading_config = self.get("trading", {})

      # 验证数据服务器配置
      data_server = xtquant_config.get("data_server", {})
      if not isinstance(data_server.get("host"), str):
        return False
      if not isinstance(data_server.get("port"), int) or data_server.get("port") <= 0:
        return False

      # 验证交易服务器配置
      trading_server = xtquant_config.get("trading_server", {})
      if not isinstance(trading_server.get("host"), str):
        return False
      if (
        not isinstance(trading_server.get("port"), int)
        or trading_server.get("port") <= 0
      ):
        return False

      # 验证数据配置
      if not isinstance(data_config.get("cache_enabled"), bool):
        return False
      if (
        not isinstance(data_config.get("max_retry"), int)
        or data_config.get("max_retry") < 0
      ):
        return False

      # 验证交易风控配置
      risk_control = trading_config.get("risk_control", {})
      max_position_ratio = risk_control.get("max_position_ratio", 0)
      if (
        not isinstance(max_position_ratio, (int, float))
        or max_position_ratio <= 0
        or max_position_ratio > 1
      ):
        return False

      max_single_stock_ratio = risk_control.get("max_single_stock_ratio", 0)
      if (
        not isinstance(max_single_stock_ratio, (int, float))
        or max_single_stock_ratio <= 0
        or max_single_stock_ratio > 1
      ):
        return False

      return True

    except Exception as exc:
      logger.error("配置验证失败: error=%s", exc.__class__.__name__)
      return False


# 全局配置实例
xt_config = XTQuantConfig()
