"""
监控模块配置
"""

from typing import Any, Dict


class MonitoringConfig:
  """监控配置类"""

  # 默认配置
  DEFAULT_CONFIG = {
    "metrics_enabled": True,
    "dashboard_enabled": True,
    "system_metrics_interval": 5,  # 秒
    "prometheus_port": 8000,
    "dashboard_refresh_interval": 3,  # 秒
    "max_metrics_history": 100,
    "alert_thresholds": {
      "cpu_usage": 80.0,  # %
      "memory_usage": 85.0,  # %
      "disk_usage": 90.0,  # %
      "response_time": 2.0,  # 秒
    },
  }

  def __init__(self, config: Dict[str, Any] = None):
    """初始化配置"""
    self.config = self.DEFAULT_CONFIG.copy()
    if config:
      self.config.update(config)

  @property
  def metrics_enabled(self) -> bool:
    """是否启用指标收集"""
    return self.config.get("metrics_enabled", True)

  @property
  def dashboard_enabled(self) -> bool:
    """是否启用监控仪表板"""
    return self.config.get("dashboard_enabled", True)

  @property
  def system_metrics_interval(self) -> int:
    """系统指标收集间隔（秒）"""
    return self.config.get("system_metrics_interval", 5)

  @property
  def dashboard_refresh_interval(self) -> int:
    """仪表板刷新间隔（秒）"""
    return self.config.get("dashboard_refresh_interval", 3)

  @property
  def alert_thresholds(self) -> Dict[str, float]:
    """告警阈值"""
    return self.config.get("alert_thresholds", {})

  def get_cpu_threshold(self) -> float:
    """获取CPU使用率告警阈值"""
    return self.alert_thresholds.get("cpu_usage", 80.0)

  def get_memory_threshold(self) -> float:
    """获取内存使用率告警阈值"""
    return self.alert_thresholds.get("memory_usage", 85.0)

  def get_disk_threshold(self) -> float:
    """获取磁盘使用率告警阈值"""
    return self.alert_thresholds.get("disk_usage", 90.0)

  def update_config(self, config: Dict[str, Any]):
    """更新配置"""
    self.config.update(config)


# 全局配置实例
monitoring_config = MonitoringConfig()
