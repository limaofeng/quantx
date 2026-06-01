"""
每日交易数据同步流程

收盘后同步当日交易数据：委托、成交、持仓
"""

from typing import Any, Dict

from prefect import flow, get_run_logger

from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from core.utils import time_utils
from prefector.tasks import (
  check_trading_day,
  create_daily_asset_snapshots,
  fetch_daily_orders,
  fetch_daily_trades,
  fetch_latest_positions,
  generate_trading_sync_report,
  save_orders_data,
  save_trades_data,
  update_positions_data,
)


@flow(
  name="每日交易数据同步",
  description="收盘后同步当日交易数据（委托、成交、持仓）",
  retries=1,
  retry_delay_seconds=300,
  **STANDARD_FLOW_HOOKS
)
async def daily_trading_sync_flow(account_id: str = "300000013250") -> Dict[str, Any]:
  """
  每日交易数据同步主流程

  包含：委托数据同步 -> 成交数据同步 -> 持仓数据更新 -> 报告生成

  Args:
      account_id: 交易账户ID

  Returns:
      同步结果报告
  """
  logger = get_run_logger()
  start_time = time_utils.now()

  # 使用当前日期作为交易日期
  trade_date = start_time.strftime("%Y-%m-%d")

  logger.info("=" * 60)
  logger.info("开始每日交易数据同步任务")
  logger.info(f"账户ID: {account_id}")
  logger.info(f"交易日期: {trade_date}")
  logger.info("=" * 60)

  # 步骤0: 检查是否为交易日
  logger.info("步骤0: 检查是否为交易日")
  is_trading_day = await check_trading_day(trade_date)

  if not is_trading_day:
    logger.info(f"{trade_date} 不是交易日，跳过同步任务")

    # 生成跳过报告
    skip_report = await generate_trading_sync_report(
      task_name="每日交易数据同步",
      start_time=start_time,
      status="skipped",
      orders_count=0,
      trades_count=0,
      positions_count=0,
      orders_saved=0,
      trades_saved=0,
      positions_updated=0,
      account_id=account_id,
      trade_date=trade_date,
      skip_reason="非交易日",
    )

    logger.info("=" * 60)
    logger.info("交易数据同步任务已跳过（非交易日）")
    logger.info("=" * 60)

    return skip_report

  logger.info(f"{trade_date} 是交易日，继续执行同步任务")

  # 初始化统计数据
  orders_count = 0
  trades_count = 0
  positions_count = 0
  orders_saved = 0
  trades_saved = 0
  positions_updated = 0
  asset_snapshot_result: Dict[str, Any] = {}

  try:
    # 步骤1: 获取当日委托数据
    logger.info("步骤1: 获取当日委托数据")
    orders_data = await fetch_daily_orders(account_id)
    orders_count = len(orders_data)
    logger.info(f"获取到 {orders_count} 个委托记录")

    # 步骤2: 保存委托数据
    if orders_data:
      logger.info("步骤2: 保存委托数据到数据库")
      order_save_result = await save_orders_data(orders_data)
      orders_saved = order_save_result.get("saved_count", 0)
      logger.info(f"成功保存 {orders_saved} 个委托记录")
    else:
      logger.info("步骤2: 无委托数据需要保存")

    # 步骤3: 获取当日成交数据
    logger.info("步骤3: 获取当日成交数据")
    trades_data = await fetch_daily_trades(account_id)
    trades_count = len(trades_data)
    logger.info(f"获取到 {trades_count} 个成交记录")

    # 步骤4: 保存成交数据
    if trades_data:
      logger.info("步骤4: 保存成交数据到数据库")
      trade_save_result = await save_trades_data(trades_data)
      trades_saved = trade_save_result.get("saved_count", 0)
      logger.info(f"成功保存 {trades_saved} 个成交记录")
    else:
      logger.info("步骤4: 无成交数据需要保存")

    # 步骤5: 获取最新持仓数据
    logger.info("步骤5: 获取最新持仓数据")
    positions_data = await fetch_latest_positions(account_id)
    positions_count = len(positions_data)
    logger.info(f"获取到 {positions_count} 个持仓记录")

    # 步骤6: 更新持仓数据
    if positions_data:
      logger.info("步骤6: 更新持仓数据到数据库")
      position_update_result = await update_positions_data(positions_data)
      positions_updated = position_update_result.get("updated_count", 0)
      logger.info(f"成功更新 {positions_updated} 个持仓记录")
    else:
      logger.info("步骤6: 无持仓数据需要更新")

    # 步骤7: 生成收盘资产快照
    logger.info("步骤7: 生成收盘资产快照")
    asset_snapshot_result = await create_daily_asset_snapshots(
      account_id=account_id,
      trade_date=trade_date,
      positions_data=positions_data,
    )
    logger.info(
      "收盘资产快照完成: "
      f"账户快照={asset_snapshot_result.get('account_snapshot_id')}, "
      f"策略快照数={asset_snapshot_result.get('strategy_snapshot_count', 0)}"
    )

    # 步骤8: 生成同步报告
    logger.info("步骤8: 生成同步报告")
    report = await generate_trading_sync_report(
      task_name="每日交易数据同步",
      start_time=start_time,
      status="success",
      orders_count=orders_count,
      trades_count=trades_count,
      positions_count=positions_count,
      orders_saved=orders_saved,
      trades_saved=trades_saved,
      positions_updated=positions_updated,
      account_id=account_id,
      trade_date=trade_date,
      asset_snapshots=asset_snapshot_result,
    )

    # 计算总体成功率
    total_fetched = orders_count + trades_count + positions_count
    total_saved = orders_saved + trades_saved + positions_updated
    success_rate = total_saved / total_fetched if total_fetched > 0 else 0

    logger.info("=" * 60)
    logger.info("每日交易数据同步任务完成")
    logger.info(f"总计: 获取{total_fetched}条，保存{total_saved}条")
    logger.info(f"成功率: {success_rate:.2%}")
    logger.info(
      f"执行时间: {(time_utils.now() - start_time).total_seconds():.2f}秒"
    )
    logger.info("=" * 60)

    return report

  except Exception as e:
    error_msg = f"每日交易数据同步失败: {str(e)}"
    logger.error(error_msg)
    return await generate_trading_sync_report(
      task_name="每日交易数据同步",
      start_time=start_time,
      status="failed",
      orders_count=orders_count,
      trades_count=trades_count,
      positions_count=positions_count,
      orders_saved=orders_saved,
      trades_saved=trades_saved,
      positions_updated=positions_updated,
      account_id=account_id,
      trade_date=trade_date,
      error=error_msg,
      asset_snapshots=asset_snapshot_result,
    )
