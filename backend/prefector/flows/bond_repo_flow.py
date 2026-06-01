"""
国债逆回购自动交易流程

交易日收盘后触发，分析国债逆回购投资机会并自动执行购买操作
"""

import asyncio
import datetime
from typing import Any, Dict

from prefect import flow, get_run_logger
from prefect.client.schemas.schedules import CronSchedule

from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from database.relational_base import WhereBuilder
from models import Instrument
from models.enums import InstrumentType
from prefector.flows.instrument_sync_flow import instrument_sync_flow
from core.utils import time_utils
from prefector.tasks import (
  analyze_bond_repo_opportunities,
  check_account_cash,
  check_trading_day,
  execute_bond_repo_purchase,
  fetch_all_trr_codes,
  fetch_bond_repo_rates,
  generate_batch_sync_report,
  generate_trade_report,
  send_sync_notification,
)
from services.instrument_service import InstrumentService


@flow(
  name="批量国债逆回购数据同步",
  description="获取所有国债逆回购代码，并发执行每只国债逆回购的同步流程",
  retries=1,
  retry_delay_seconds=300,
  **STANDARD_FLOW_HOOKS
)
async def bond_repo_sync_flow(
  max_concurrency: int = 10,
  skip_existing: bool = False,
) -> Dict[str, Any]:
  """
  批量国债逆回购数据同步父流程

  Args:
      max_concurrency: 最大并发数，避免过度消耗资源
      skip_existing: 是否跳过已存在的国债逆回购
      trr_codes: 可选的指定国债逆回购代码列表

  Returns:
      批量同步结果报告
  """
  logger = get_run_logger()
  start_time = time_utils.now()

  logger.info("=" * 60)
  logger.info("开始批量国债逆回购数据同步任务")
  logger.info(f"并发数限制: {max_concurrency}")
  logger.info("=" * 60)

  try:
    # 步骤1: 获取国债逆回购代码列表
    logger.info("步骤1: 获取国债逆回购代码列表")
    stock_codes = await fetch_all_trr_codes()

    total_stocks = len(stock_codes)
    logger.info(f"获取到 {total_stocks} 只国债逆回购")

    if total_stocks == 0:
      logger.warning("未获取到任何国债逆回购代码")
      return {
        "status": "skipped",
        "reason": "未获取到国债逆回购代码",
        "total_trrs": 0,
        "start_time": start_time,
        "end_time": time_utils.now(),
      }

    if skip_existing:
      logger.info("启用跳过已存在数据的国债逆回购同步")
      instrument_service = InstrumentService()

      where = WhereBuilder().eq(Instrument.type, InstrumentType.TRR)
      existing_instruments = await instrument_service.find_all(where=where, limit=10000)

      existing_codes = {
        inst.code for inst in existing_instruments if inst.instrument_type == "trr"
      }
      stock_codes = [code for code in stock_codes if code not in existing_codes]
      skipped_count = total_stocks - len(stock_codes)
      total_stocks = len(stock_codes)
      logger.info(
        f"跳过已存在数据的国债逆回购 {skipped_count} 只，剩余待同步 {total_stocks} 只"
      )

    # 步骤2: 并发执行单只国债逆回购同步
    logger.info(f"步骤2: 开始并发同步 {total_stocks} 只国债逆回购")

    # 使用信号量控制并发数
    semaphore = asyncio.Semaphore(max_concurrency)

    async def sync_with_limit(trr_code: str) -> Dict[str, Any]:
      """带并发限制的单只国债逆回购同步"""
      async with semaphore:
        return await instrument_sync_flow(trr_code)

    # 创建所有同步任务
    tasks = [asyncio.create_task(sync_with_limit(code)) for code in stock_codes]

    # 等待所有任务完成
    logger.info("等待所有国债逆回购同步完成...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 步骤3: 统计结果
    logger.info("步骤3: 统计同步结果")

    success_count = 0
    failed_count = 0
    skipped_count = 0
    error_count = 0  # asyncio异常

    success_trrs = []
    failed_trrs = []
    skipped_trrs = []
    error_trrs = []

    total_duration = 0
    total_records = 0

    for i, result in enumerate(results):
      stock_code = stock_codes[i]

      if isinstance(result, Exception):
        # asyncio 级别的异常
        error_count += 1
        error_trrs.append({"trr_code": stock_code, "error": str(result)})
        logger.error(f"Task异常 {stock_code}: {result}")
        continue

      # 正常的结果字典
      status = result.get("status")
      if status == "success":
        success_count += 1
        success_trrs.append(result)
        total_duration += result.get("duration_seconds", 0)
        total_records += result.get("records_saved", 0)
      elif status == "skipped":
        skipped_count += 1
        skipped_trrs.append(result)
      else:  # failed or error
        failed_count += 1
        failed_trrs.append(result)

    # 计算整体状态
    if success_count == total_stocks:
      overall_status = "success"
    elif success_count > 0:
      overall_status = "partial"
    else:
      overall_status = "failed"

    success_rate = (success_count / total_stocks * 100) if total_stocks > 0 else 0
    avg_duration = (total_duration / success_count) if success_count > 0 else 0

    end_time = time_utils.now()
    total_elapsed = (end_time - start_time).total_seconds()

    # 步骤4: 生成详细报告
    logger.info("步骤4: 生成批量同步报告")
    report = await generate_batch_sync_report(
      task_name="批量国债逆回购数据同步",
      report_type="batch_trr_sync",
      start_time=start_time,
      end_time=end_time,
      total_elapsed_seconds=total_elapsed,
      total_stocks=total_stocks,  # 复用字段名
      success_count=success_count,
      failed_count=failed_count,
      skipped_count=skipped_count,
      error_count=error_count,
      success_rate=success_rate,
      avg_duration_per_stock=avg_duration,
      total_records_saved=total_records,
      status=overall_status,
      success_stocks=success_trrs[:10],  # 只保留前10个成功示例
      failed_stocks=failed_trrs,
      skipped_stocks=skipped_trrs,
      error_stocks=error_trrs,
      max_concurrency=max_concurrency,
    )

    # 步骤5: 发送通知（可选）
    if failed_count > 0 or error_count > 0:
      logger.info("步骤5: 发送失败通知")
      await send_sync_notification(
        notification_type="partial_failure"
        if success_count > 0
        else "complete_failure",
        report=report,
      )

    # 输出汇总信息
    logger.info("=" * 60)
    logger.info("批量国债逆回购数据同步任务完成")
    logger.info(f"总国债逆回购数: {total_stocks}")
    logger.info(
      f"成功: {success_count}, 失败: {failed_count}, 跳过: {skipped_count}, 异常: {error_count}"
    )
    logger.info(f"成功率: {success_rate:.1f}%")
    logger.info(f"总耗时: {total_elapsed:.1f}s, 平均每只: {avg_duration:.2f}s")
    logger.info(f"总保存记录数: {total_records}")
    logger.info(f"整体状态: {overall_status}")
    logger.info(f"失败国债逆回购列表: {[item['trr_code'] for item in failed_trrs]}")
    logger.info("=" * 60)

    return report

  except Exception as e:
    logger.error(f"批量国债逆回购数据同步流程失败: {e}")
    raise


@flow(
  name="国债逆回购自动交易",
  description="交易日收盘后自动分析国债逆回购机会并执行购买操作",
  retries=1,
  retry_delay_seconds=300,
  **STANDARD_FLOW_HOOKS
)
async def bond_repo_auto_trade_flow() -> Dict[str, Any]:
  """
  国债逆回购自动交易主流程

  包含：交易日检查 -> 收益率获取 -> 投资机会分析 -> 执行逆回购购买 -> 报告生成

  Returns:
      交易执行报告
  """
  logger = get_run_logger()
  start_time = time_utils.now()

  logger.info("=" * 60)
  logger.info("开始国债逆回购自动交易任务")
  logger.info("=" * 60)

  try:
    # 步骤1: 检查是否为交易日
    logger.info("步骤1: 检查交易日状态")
    is_trading_day = await check_trading_day()

    if not is_trading_day:
      logger.info("当前不是交易日，跳过国债逆回购自动交易")
      report = await generate_trade_report(
        task_name="国债逆回购自动交易",
        start_time=start_time,
        status="skipped",
        total_products=0,
        opportunities_found=0,
        trades_executed=0,
        message="非交易日，跳过交易",
      )
      return report

    # 步骤2: 检测可用资金是否满足最小购买要求
    logger.info("步骤2: 检查账户可用资金")
    is_valid = await check_account_cash(account_id="300000013250", min_cash=10000)
    if not is_valid:
      logger.info("账户可用资金不足，跳过国债逆回购自动交易")
      report = await generate_trade_report(
        task_name="国债逆回购自动交易",
        start_time=start_time,
        status="skipped",
        total_products=0,
        opportunities_found=0,
        trades_executed=0,
        message="账户可用资金不足，跳过交易",
      )
      return report

    # 步骤3: 获取国债逆回购收益率
    logger.info("步骤3: 获取国债逆回购收益率数据")
    repo_data = await fetch_bond_repo_rates()

    # 步骤4: 分析投资机会
    logger.info("步骤4: 分析国债逆回购投资机会")
    analysis = await analyze_bond_repo_opportunities(repo_data)

    if analysis["best_opportunity"] is None:
      logger.info("当前无合适的国债逆回购投资机会，结束任务")
      report = await generate_trade_report(
        task_name="国债逆回购自动交易",
        start_time=start_time,
        status="skipped",
        total_products=len(repo_data),
        opportunities_found=0,
        trades_executed=0,
        message="无合适投资机会，跳过交易",
      )
      return report

    # 步骤5: 执行逆回购操作
    logger.info("步骤5: 执行国债逆回购购买操作")
    purchase_result = await execute_bond_repo_purchase(analysis)

    # 步骤6: 生成报告
    logger.info("步骤6: 生成交易报告")

    # 收集关键信息用于报告
    profitable_count = len(analysis.get("profitable_opportunities", []))
    best_opportunity = analysis.get("best_opportunity")
    shanghai_avg = analysis["market_summary"]["shanghai"]["avg_rate"]
    shenzhen_avg = analysis["market_summary"]["shenzhen"]["avg_rate"]
    recommendations = analysis.get("recommendations", [])
    purchases = purchase_result.get("purchases", [])

    report = await generate_trade_report(
      task_name="国债逆回购自动交易",
      start_time=start_time,
      status="success",
      total_products=len(repo_data),
      opportunities_found=profitable_count,
      trades_executed=len(purchases),
      bond_repo_data={
        "best_opportunity": best_opportunity,
        "shanghai_avg_rate": shanghai_avg,
        "shenzhen_avg_rate": shenzhen_avg,
        "recommendations": recommendations,
        "analysis_time": analysis["analysis_time"],
        "purchases": purchases,
      },
    )

    logger.info("国债逆回购自动交易任务完成")
    logger.info(f"发现 {profitable_count} 个有利可图的投资机会")

    if best_opportunity:
      logger.info(
        f"最佳机会: {best_opportunity['name']}({best_opportunity['code']})，"
        f"净收益 {best_opportunity['net_profit']}元"
      )

    if purchases:
      logger.info("执行购买结果:")
      for i, purchase in enumerate(purchases, 1):
        logger.info(
          f"  {i}. {purchase['name']}({purchase['code']}) - {purchase['amount']}元"
        )

    if recommendations:
      logger.info("投资建议:")
      for i, rec in enumerate(recommendations, 1):
        logger.info(f"  {i}. {rec}")

    return report

  except Exception as e:
    logger.error(f"国债逆回购自动交易任务失败: {e}")
    raise


@flow(
  name="盘中国债逆回购监控",
  description="盘中实时监控国债逆回购收益率变化，适合收盘前使用",
  retries=0,
  **STANDARD_FLOW_HOOKS
)
async def bond_repo_monitor_flow() -> Dict[str, Any]:
  """
  盘中国债逆回购监控流程

  适合在交易时间内手动触发，快速了解当前逆回购市场情况

  Returns:
      监控结果
  """
  logger = get_run_logger()
  start_time = time_utils.now()

  logger.info("=" * 50)
  logger.info("开始盘中国债逆回购监控")
  logger.info("=" * 50)

  try:
    # 获取当前逆回购数据
    logger.info("获取当前国债逆回购收益率...")
    repo_data = await fetch_bond_repo_rates()

    # 快速分析
    logger.info("分析当前投资机会...")
    analysis = await analyze_bond_repo_opportunities(repo_data)

    # 输出关键信息
    high_yield_ops = analysis.get("high_yield_opportunities", [])
    if high_yield_ops:
      logger.info(f"发现 {len(high_yield_ops)} 个高收益机会（>3.5%）:")
      for op in high_yield_ops[:3]:  # 只显示前3个
        logger.info(f"  {op['name']}({op['code']}): {op['rate']}% ({op['duration']}天)")
    else:
      logger.info("当前暂无高收益机会（>3.5%）")

    # 显示市场平均水平
    sh_avg = analysis["market_summary"]["shanghai"]["avg_rate"]
    sz_avg = analysis["market_summary"]["shenzhen"]["avg_rate"]
    logger.info(f"市场平均收益率 - 上海: {sh_avg}%, 深圳: {sz_avg}%")

    # 返回监控结果
    return {
      "monitor_time": start_time.isoformat(),
      "status": "success",
      "total_products": len(repo_data),
      "high_yield_count": len(high_yield_ops),
      "market_summary": analysis["market_summary"],
      "top_opportunities": high_yield_ops[:5],  # 返回前5个机会
      "recommendations": analysis.get("recommendations", []),
    }

  except Exception as e:
    logger.error(f"盘中国债逆回购监控失败: {e}")
    raise


# 收盘前国债逆回购提醒流程（收盘前30分钟）
BOND_REPO_ALERT_SCHEDULE = CronSchedule(cron="30 14 * * 1-5")


@flow(
  name="收盘前国债逆回购提醒",
  description="收盘前30分钟提醒关注国债逆回购投资机会",
  retries=0,
  **STANDARD_FLOW_HOOKS
)
async def bond_repo_alert_flow() -> Dict[str, Any]:
  """
  收盘前国债逆回购提醒流程

  在收盘前30分钟触发，提醒用户关注逆回购投资机会

  Returns:
      提醒结果
  """
  logger = get_run_logger()
  start_time = time_utils.now()

  logger.info("=" * 50)
  logger.info("收盘前国债逆回购投资提醒")
  logger.info("=" * 50)

  try:
    # 检查是否为交易日
    is_trading_day = await check_trading_day()
    if not is_trading_day:
      logger.info("今日非交易日，无需提醒")
      return {"status": "skipped", "reason": "非交易日"}

    # 获取当前数据
    repo_data = await fetch_bond_repo_rates()
    analysis = await analyze_bond_repo_opportunities(repo_data)

    # 生成提醒信息
    current_time = time_utils.now().strftime("%H:%M")
    alert_message = f"🔔 {current_time} 收盘前投资提醒\n\n"

    high_yield_ops = analysis.get("high_yield_opportunities", [])
    if high_yield_ops:
      alert_message += f"📈 发现 {len(high_yield_ops)} 个高收益机会：\n"
      for op in high_yield_ops[:3]:
        alert_message += (
          f"• {op['name']}({op['code']}): {op['rate']}% ({op['duration']}天)\n"
        )

    # 收盘前特别提醒
    alert_message += "\n💡 收盘前提醒：\n"
    alert_message += "• 国债逆回购通常在收盘前收益率会上升\n"
    alert_message += "• 建议关注1天期和7天期品种\n"
    alert_message += "• 资金闲置时可考虑参与逆回购\n"

    logger.info("提醒内容:")
    logger.info(alert_message)

    return {
      "alert_time": start_time.isoformat(),
      "status": "success",
      "message": alert_message,
      "high_yield_count": len(high_yield_ops),
      "opportunities": high_yield_ops[:3],
    }

  except Exception as e:
    logger.error(f"收盘前提醒失败: {e}")
    raise
