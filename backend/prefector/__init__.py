"""
Prefector 包

重构为 flows 和 tasks 分离的架构
- flows: 业务流程编排
- tasks: 原子任务单元
"""

# 导入主要流程
# 导入新的 flow_manager 注册表
from .flow_manager import PrefectManagerRegistry
from .flows import (
  REALTIME_SYNC_SCHEDULE,
  bond_repo_sync_flow,
  bond_repo_auto_trade_flow,
  daily_indicator_snapshot_flow,
  daily_market_data_sync_flow,
  market_sync_flow,
  realtime_price_sync_flow,
  sector_data_sync_flow,
)

# 保留原有的 prefect_manager
from .prefect_manager import prefect_manager

# 导入原子任务（如需单独使用）
from .tasks import (
  fetch_account_positions,
  fetch_market_indices,
  fetch_stock_list,
  fetch_stock_prices,
  generate_sync_report,
  generate_task_report,
  generate_trade_report,
  save_market_indices,
  save_sector_data,
  save_stock_data,
  update_price_cache,
)

__all__ = [
  # 主要流程
  "realtime_price_sync_flow",
  "sector_data_sync_flow",
  "market_sync_flow",
  "daily_indicator_snapshot_flow",
  "daily_market_data_sync_flow",
  # 国债逆回购流程
  "bond_repo_sync_flow",
  "bond_repo_auto_trade_flow",
  # 调度配置
  "REALTIME_SYNC_SCHEDULE",
  # 原子任务
  "fetch_stock_list",
  "fetch_stock_prices",
  "save_stock_data",
  "update_price_cache",
  "fetch_market_indices",
  "save_market_indices",
  "save_sector_data",
  "generate_task_report",
  "generate_sync_report",
  "generate_trade_report",
  "fetch_account_positions",
  # 原有模块
  "prefect_manager",
  # 新的流程管理器
  "PrefectManagerRegistry",
]
