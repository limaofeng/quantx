"""
Tasks 包初始化

统一导出所有原子任务
"""

from .bond_tasks import (
  analyze_bond_repo_opportunities,
  check_trading_day,
  execute_bond_repo_purchase,
  fetch_bond_repo_rates,
)
from .market_data_tasks import (
  # K线数据相关
  download_market_data,
  save_market_data,
)
from .divid_factor_tasks import (
  fetch_divid_factors,
  fetch_divid_factors_batch,
  save_divid_factors,
  save_divid_factors_batch,
)
from .market_tasks import (
  download_sector_data,
  fetch_market_indices,
  fetch_sector_data,
  save_market_indices,
  save_sector_data,
)
from .position_tasks import (
  fetch_account_positions,
  generate_position_sync_report,
  save_positions,
)
from .report_tasks import (
  # 新增：批量同步报告
  generate_batch_sync_report,
  generate_sync_report,
  generate_task_report,
  generate_trade_report,
  generate_trading_sync_report,
  save_report_to_file,
  send_sync_notification,
)
from .stock_tasks import (
  # 新增：单股票和批量处理相关
  fetch_instrument_codes,
  fetch_all_trr_codes,
  fetch_single_stock_price,
  fetch_stock_financial_data,
  fetch_stock_info,
  fetch_stock_list,
  fetch_stock_prices,
  save_single_stock_data,
  save_stock_data,
  update_price_cache,
  validate_stock_data,
  # 批量任务
  fetch_batch_instrument_infos,
  save_batch_stock_data,
  fetch_batch_financial_data,
  save_batch_financial_data,
  sync_instruments_batch_task,
  sync_financial_batch_task,
)
from .trading_tasks import (
  check_account_cash,
  create_daily_asset_snapshots,
  fetch_daily_orders,
  fetch_daily_trades,
  fetch_latest_positions,
  save_orders_data,
  save_trades_data,
  update_positions_data,
)

__all__ = [
  # 股票任务
  "fetch_stock_list",
  "fetch_stock_prices",
  "save_stock_data",
  "update_price_cache",
  # 新增：单股票和批量处理
  "fetch_instrument_codes",
  "fetch_all_trr_codes",
  "fetch_stock_info",
  "fetch_stock_financial_data",
  "fetch_single_stock_price",
  "validate_stock_data",
  "save_single_stock_data",
  # 批量任务
  "fetch_batch_instrument_infos",
  "save_batch_stock_data",
  "fetch_batch_financial_data",
  "save_batch_financial_data",
  "sync_instruments_batch_task",
  "sync_financial_batch_task",
  # 市场任务
  "download_sector_data",
  "fetch_market_indices",
  "fetch_sector_data",
  "save_market_indices",
  "save_sector_data",
  # 国债逆回购任务
  "fetch_bond_repo_rates",
  "analyze_bond_repo_opportunities",
  "execute_bond_repo_purchase",
  "check_trading_day",
  # 持仓同步任务
  "fetch_account_positions",
  "save_positions",
  "generate_position_sync_report",
  # 交易数据任务
  "check_account_cash",
  "create_daily_asset_snapshots",
  "fetch_daily_orders",
  "fetch_daily_trades",
  "fetch_latest_positions",
  "save_orders_data",
  "save_trades_data",
  "update_positions_data",
  # 报告任务
  "generate_task_report",
  "generate_sync_report",
  "generate_trade_report",
  "generate_trading_sync_report",
  "save_report_to_file",
  # 新增：批量同步报告
  "generate_batch_sync_report",
  "send_sync_notification",
  # K线数据相关
  "download_market_data",
  "save_market_data",
  # 除权因子任务
  "fetch_divid_factors",
  "fetch_divid_factors_batch",
  "save_divid_factors",
  "save_divid_factors_batch",
]
