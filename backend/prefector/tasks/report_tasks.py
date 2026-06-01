"""
报告生成相关的原子任务

包含各种报告的生成和保存任务
"""

import datetime
import json
from pathlib import Path
from typing import Any, Dict

import aiofiles
from prefect import get_run_logger, task
from core.utils import time_utils

# 报告目录
REPORT_DIR = "logs/sync_reports"


def serialize_datetimes(obj: Any) -> Any:
  """递归地将 datetime 对象转换为 ISO 字符串"""
  if isinstance(obj, datetime.datetime):
    return obj.isoformat()
  elif isinstance(obj, dict):
    return {k: serialize_datetimes(v) for k, v in obj.items()}
  elif isinstance(obj, list):
    return [serialize_datetimes(item) for item in obj]
  elif isinstance(obj, tuple):
    return tuple(serialize_datetimes(item) for item in obj)
  else:
    return obj


@task(name="生成任务报告", description="生成任务执行报告", retries=1)
async def generate_task_report(
  task_name: str, start_time: datetime.datetime, status: str = "success", **kwargs
) -> Dict[str, Any]:
  """生成任务执行报告"""
  logger = get_run_logger()

  end_time = time_utils.now()
  duration = (end_time - start_time).total_seconds()

  report = {
    "task_name": task_name,
    "report_time": end_time.isoformat(),
    "duration_seconds": duration,
    "status": status,
    **kwargs,
  }

  # 保存报告到文件
  report_file = await save_report_to_file(report, task_name.replace(" ", "_").lower())

  logger.info(f"任务 {task_name} 完成 - 状态: {status}, 耗时: {duration:.1f}秒")
  logger.info(f"报告已保存: {report_file}")

  return report


@task(name="保存报告文件", description="将报告保存到文件系统", retries=1)
async def save_report_to_file(report: Dict[str, Any], report_type: str) -> str:
  """保存报告到文件"""
  # 创建报告目录
  report_dir = Path(REPORT_DIR)
  report_dir.mkdir(parents=True, exist_ok=True)

  # 生成文件名
  timestamp = time_utils.now().strftime("%Y%m%d_%H%M%S")
  report_file = report_dir / f"{report_type}_{timestamp}.json"

  # 保存报告
  async with aiofiles.open(report_file, "w", encoding="utf-8") as f:
    await f.write(json.dumps(serialize_datetimes(report), ensure_ascii=False, indent=2))

  return str(report_file)


@task(name="生成同步报告", description="生成数据同步专用报告", retries=1)
async def generate_sync_report(
  task_name: str,
  start_time: datetime.datetime,
  fetched_count: int,
  saved_count: int,
  status: str = "success",
  **kwargs,
) -> Dict[str, Any]:
  """生成数据同步报告"""
  logger = get_run_logger()

  end_time = time_utils.now()
  duration = (end_time - start_time).total_seconds()
  success_rate = (saved_count / max(fetched_count, 1)) * 100

  report = {
    "task_name": task_name,
    "report_time": end_time.isoformat(),
    "duration_seconds": duration,
    "status": status,
    "fetched_count": fetched_count,
    "saved_count": saved_count,
    "success_rate": success_rate,
    **kwargs,
  }

  # 保存报告到文件
  report_file = await save_report_to_file(report, task_name.replace(" ", "_").lower())

  logger.info(f"同步任务 {task_name} 完成")
  logger.info(
    f"获取: {fetched_count}, 保存: {saved_count}, 成功率: {success_rate:.1f}%"
  )
  logger.info(f"报告已保存: {report_file}")

  return report


@task(name="生成批量同步报告", description="生成批量股票同步的详细报告", retries=1)
async def generate_batch_sync_report(
  task_name: str,
  start_time: datetime.datetime,
  end_time: datetime.datetime,
  total_elapsed_seconds: float,
  total_stocks: int,
  success_count: int,
  failed_count: int,
  skipped_count: int,
  error_count: int,
  success_rate: float,
  avg_duration_per_stock: float,
  total_records_saved: int,
  status: str,
  report_type: str,
  **kwargs,
) -> Dict[str, Any]:
  """生成批量股票同步报告"""
  logger = get_run_logger()

  report = {
    "task_name": task_name,
    "report_type": report_type,
    "report_time": end_time.isoformat(),
    "start_time": start_time.isoformat(),
    "end_time": end_time.isoformat(),
    "total_elapsed_seconds": total_elapsed_seconds,
    "statistics": {
      "total_stocks": total_stocks,
      "success_count": success_count,
      "failed_count": failed_count,
      "skipped_count": skipped_count,
      "error_count": error_count,
      "success_rate": success_rate,
      "avg_duration_per_stock": avg_duration_per_stock,
      "total_records_saved": total_records_saved,
    },
    "status": status,
    "performance": {
      "stocks_per_second": total_stocks / total_elapsed_seconds
      if total_elapsed_seconds > 0
      else 0,
      "records_per_second": total_records_saved / total_elapsed_seconds
      if total_elapsed_seconds > 0
      else 0,
    },
    **kwargs,
  }

  # 递归序列化整个报告，将所有 datetime 对象转换为字符串
  report = serialize_datetimes(report)

  # 保存详细报告到文件
  report_file = await save_report_to_file(report, report_type)

  logger.info(f"批量同步报告已生成: {report_file}")
  logger.info(f"总计: {total_stocks} 股票, 成功: {success_count}, 失败: {failed_count}")
  logger.info(
    f"成功率: {success_rate:.1f}%, 平均耗时: {avg_duration_per_stock:.2f}s/股"
  )

  return report


@task(name="发送同步通知", description="发送同步结果通知（邮件、钉钉等）", retries=1)
async def send_sync_notification(
  notification_type: str, report: Dict[str, Any]
) -> bool:
  """发送同步通知"""
  logger = get_run_logger()

  try:
    if notification_type == "complete_failure":
      logger.info("发送完全失败通知")
      message = f"""
            批量股票同步完全失败

            任务: {report.get("task_name")}
            时间: {report.get("report_time")}
            状态: {report.get("status")}
            错误: {report.get("error", "未知错误")}

            请检查系统状态和网络连接。
            """
    elif notification_type == "partial_failure":
      stats = report.get("statistics", {})
      logger.info("发送部分失败通知")
      message = f"""
            批量股票同步部分失败

            任务: {report.get("task_name")}
            时间: {report.get("report_time")}

            统计:
            - 总股票数: {stats.get("total_stocks")}
            - 成功: {stats.get("success_count")}
            - 失败: {stats.get("failed_count")}
            - 跳过: {stats.get("skipped_count")}
            - 异常: {stats.get("error_count")}
            - 成功率: {stats.get("success_rate", 0):.1f}%

            请检查失败的股票并考虑重新同步。
            """
    else:
      logger.info(f"发送成功通知: {notification_type}")
      message = f"批量股票同步成功完成 - {report.get('task_name')}"

    # 这里应该集成实际的通知服务
    # 例如: 邮件、钉钉、企业微信、Slack等
    logger.info(f"通知内容: {message}")

    # 模拟发送成功
    return True

  except Exception as e:
    logger.error(f"发送通知失败: {e}")
    return False


@task(name="生成交易执行报告", description="生成交易执行的详细报告", retries=1)
async def generate_trade_report(
  task_name: str,
  start_time: datetime.datetime,
  status: str = "success",
  total_products: int = 0,
  opportunities_found: int = 0,
  trades_executed: int = 0,
  error: str = None,
  **kwargs,
) -> Dict[str, Any]:
  """生成交易执行报告"""
  logger = get_run_logger()

  end_time = time_utils.now()
  duration = (end_time - start_time).total_seconds()

  report = {
    "task_name": task_name,
    "report_time": end_time.isoformat(),
    "duration_seconds": duration,
    "status": status,
    "total_products": total_products,
    "opportunities_found": opportunities_found,
    "trades_executed": trades_executed,
    **kwargs,
  }

  if error:
    report["error"] = error

  # 保存报告到文件
  report_file = await save_report_to_file(report, task_name.replace(" ", "_").lower())

  logger.info(f"交易任务 {task_name} 完成")
  logger.info(
    f"产品数量: {total_products}, 发现机会: {opportunities_found}, 执行交易: {trades_executed}"
  )
  logger.info(f"报告已保存: {report_file}")

  return report


@task(name="生成交易数据同步报告", description="生成交易数据同步的详细报告", retries=1)
async def generate_trading_sync_report(
  task_name: str,
  start_time: datetime.datetime,
  status: str = "success",
  orders_count: int = 0,
  trades_count: int = 0,
  positions_count: int = 0,
  orders_saved: int = 0,
  trades_saved: int = 0,
  positions_updated: int = 0,
  account_id: str = None,
  trade_date: str = None,
  error: str = None,
  skip_reason: str = None,
  **kwargs,
) -> Dict[str, Any]:
  """生成交易数据同步报告"""
  logger = get_run_logger()

  end_time = time_utils.now()
  duration = (end_time - start_time).total_seconds()

  report = {
    "task_name": task_name,
    "report_time": end_time.isoformat(),
    "start_time": start_time.isoformat(),
    "end_time": end_time.isoformat(),
    "duration_seconds": duration,
    "status": status,
    "account_id": account_id,
    "trade_date": trade_date,
    "data_summary": {
      "orders": {
        "fetched": orders_count,
        "saved": orders_saved,
        "success_rate": orders_saved / orders_count if orders_count > 0 else 0,
      },
      "trades": {
        "fetched": trades_count,
        "saved": trades_saved,
        "success_rate": trades_saved / trades_count if trades_count > 0 else 0,
      },
      "positions": {
        "fetched": positions_count,
        "updated": positions_updated,
        "success_rate": positions_updated / positions_count
        if positions_count > 0
        else 0,
      },
    },
    **kwargs,
  }

  if error:
    report["error"] = error
    report["status"] = "failed"

  if skip_reason:
    report["skip_reason"] = skip_reason
    report["status"] = "skipped"

  # 保存报告到文件
  report_file = await save_report_to_file(report, "trading_data_sync")

  # 记录日志
  if status == "skipped":
    logger.info(f"交易数据同步任务 {task_name} 已跳过")
    logger.info(f"跳过原因: {skip_reason}")
    logger.info(f"账户: {account_id}, 交易日期: {trade_date}")
  else:
    logger.info(f"交易数据同步任务 {task_name} 完成")
    logger.info(f"账户: {account_id}, 交易日期: {trade_date}")
    logger.info(f"委托: 获取{orders_count}个，保存{orders_saved}个")
    logger.info(f"成交: 获取{trades_count}个，保存{trades_saved}个")
    logger.info(f"持仓: 获取{positions_count}个，更新{positions_updated}个")
  logger.info(f"执行时间: {duration:.2f}秒")
  logger.info(f"报告已保存: {report_file}")

  return report
