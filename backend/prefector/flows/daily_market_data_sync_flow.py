"""
市场数据同步流程

支持股票（包括指数、ETF等）K线与tick数据的批量同步
"""

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from prefect import flow, get_run_logger
from prefect.runtime import flow_run as flow_run_runtime

from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from database.connection import redis_client
from miniqmt.manager_registry import XTDataManagerRegistry
from core.utils import time_utils
from prefector.tasks import (
  download_market_data,
  generate_sync_report,
  save_market_data,
  save_report_to_file,
)
from repositories.instrument_where_builder import InstrumentWhereBuilder
from services.instrument_service import InstrumentService
from prefector.flows.daily_indicator_snapshot_flow import daily_indicator_snapshot_flow


SYNC_LOCK_TTL_SECONDS = 12 * 60 * 60


def _validate_periods(periods: List[str]) -> None:
  """
  验证periods参数，只允许 tick、1m 和 1d

  存储策略（方案C）:
  - 1m: 日内分钟数据，可聚合为 5m / 15m / 30m / 60m
  - 1d: 日线数据，可聚合为 1w / 1mon / 1q / 1hy / 1y

  Args:
      periods: 数据周期列表

  Raises:
      ValueError: 当包含不支持的周期时
  """
  allowed_periods = ["tick", "1m", "1d"]
  invalid_periods = [p for p in periods if p not in allowed_periods]
  if invalid_periods:
    raise ValueError(
      f"不支持的periods: {invalid_periods}. 只支持: {allowed_periods}. "
      f"其他维度请通过1m/1d数据聚合计算得到。"
    )


def _parse_date(date_str: str) -> datetime:
  """
  解析日期字符串

  Args:
      date_str: 日期字符串，支持 'YYYYMMDD' 或 'YYYYMMDD HHMMSS' 格式

  Returns:
      datetime: 解析后的日期对象
  """
  # 移除空格并标准化
  date_str = date_str.strip().replace(" ", "")

  try:
    if len(date_str) == 8:  # YYYYMMDD
      return datetime.strptime(date_str, "%Y%m%d")
    elif len(date_str) >= 14:  # YYYYMMDDHHMMSS
      return datetime.strptime(date_str[:14], "%Y%m%d%H%M%S")
    else:
      raise ValueError(f"不支持的日期格式: {date_str}")
  except Exception as e:
    raise ValueError(f"日期解析失败: {date_str}, 错误: {e}")


def _format_cache_key_for_log(cache_key: str, max_length: int = 160) -> str:
  """
  缓存键日志格式化，避免超长输出

  Args:
      cache_key: 完整缓存键
      max_length: 最大输出长度

  Returns:
      str: 格式化后的缓存键
  """
  if len(cache_key) <= max_length:
    return cache_key

  head_len = max(0, max_length // 2 - 10)
  tail_len = max(0, max_length - head_len - 5)
  return f"{cache_key[:head_len]}...{cache_key[-tail_len:]}"


def _get_prefect_scheduled_start_time() -> Optional[datetime]:
  """Return Prefect's scheduled start time when available."""
  try:
    return flow_run_runtime.get_scheduled_start_time()
  except Exception:
    return None


def _resolve_time_range_from_schedule(
  start_time: str,
  end_time: str,
) -> Dict[str, str]:
  """
  Resolve empty sync dates from the current Prefect run's scheduled time.

  For scheduled deployments this prevents catch-up runs from downloading the
  worker recovery date. Manual calls can still override either side explicitly.
  """
  start_time = (start_time or "").strip()
  end_time = (end_time or "").strip()

  if start_time and end_time:
    return {
      "start_time": start_time,
      "end_time": end_time,
      "source": "explicit",
    }

  if start_time:
    return {
      "start_time": start_time,
      "end_time": start_time,
      "source": "explicit_start",
    }

  if end_time:
    return {
      "start_time": end_time,
      "end_time": end_time,
      "source": "explicit_end",
    }

  scheduled_start = _get_prefect_scheduled_start_time()
  if scheduled_start is None:
    scheduled_start = time_utils.now_aware()

  scheduled_start = time_utils.to_shanghai(scheduled_start)
  target_day = scheduled_start.strftime("%Y%m%d")
  return {
    "start_time": target_day,
    "end_time": target_day,
    "source": "prefect_scheduled_start_time",
  }


def _build_sync_lock_key(cache_key: str) -> str:
  digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
  return f"daily_market_data_sync_lock:{digest}"


def _acquire_sync_lock(lock_key: str, lock_token: str) -> bool:
  return bool(
    redis_client.set(
      lock_key,
      lock_token,
      ex=SYNC_LOCK_TTL_SECONDS,
      nx=True,
    )
  )


def _release_sync_lock(lock_key: str, lock_token: str) -> None:
  if redis_client.get(lock_key) == lock_token:
    redis_client.delete(lock_key)


def _split_date_ranges(start_time: str, end_time: str) -> List[str]:
  """
  将时间范围按天分割

  Args:
      start_time: 开始时间字符串（已处理默认值）
      end_time: 结束时间字符串（已处理默认值）

  Returns:
      List[str]: 单日时间字符串列表
  """
  # 解析开始和结束日期
  start_date = _parse_date(start_time)
  end_date = _parse_date(end_time)

  # 只比较日期部分
  start_day = start_date.date()
  end_day = end_date.date()
  
  # 检查当天是否已收盘（A股收盘时间为 15:00）
  now = time_utils.now()
  today = now.date()
  market_close_hour = 15  # A股收盘时间
  is_market_closed_today = now.hour >= market_close_hour
  
  # 如果结束日期是今天且未收盘，则将结束日期调整为昨天
  if end_day == today and not is_market_closed_today:
    end_day = today - timedelta(days=1)
    # 如果调整后结束日期早于开始日期，返回空列表
    if end_day < start_day:
      return []

  if start_day == end_day:
    return [start_day.strftime("%Y%m%d")]

  # 按天分割
  date_list = []
  current_day = start_day

  while current_day <= end_day:
    # 构造当天的时间字符串
    day_time = current_day.strftime("%Y%m%d")

    # 如果是开始日期且有具体时间，保持原始时间
    if current_day == start_day and len(start_time.replace(" ", "")) > 8:
      day_time = start_time
    # 如果是结束日期且有具体时间，保持原始时间
    elif current_day == end_day and len(end_time.replace(" ", "")) > 8:
      day_time = end_time

    date_list.append(day_time)
    current_day += timedelta(days=1)

  # 反转列表，让最近的时间在前面
  date_list.reverse()
  return date_list


def _filter_completed_stocks(
  stock_list: List[str], period: str, single_day_time: str
) -> List[str]:
  """
  过滤已完成的股票，返回未完成的股票列表

  Args:
      stock_list: 股票代码列表
      period: 数据周期
      single_day_time: 单天时间

  Returns:
      List[str]: 未完成的股票列表
  """
  uncompleted_stocks = []
  completed_count = 0

  for stock_code in stock_list:
    cache_key = f"daily_market_data_stock:{stock_code}:{single_day_time}:{period}"
    if not redis_client.exists(cache_key):
      uncompleted_stocks.append(stock_code)
    else:
      completed_count += 1

  if completed_count > 0:
    logger = get_run_logger()
    logger.info(
      f"周期 {period} 日期 {single_day_time}: 跳过 {completed_count} 个已完成股票，待处理 {len(uncompleted_stocks)} 个"
    )

  return uncompleted_stocks


def _mark_stocks_completed(
  stock_list: List[str], period: str, single_day_time: str
) -> None:
  """
  标记股票为已完成

  Args:
      stock_list: 已完成的股票代码列表
      period: 数据周期
      single_day_time: 单天时间
  """
  for stock_code in stock_list:
    cache_key = f"daily_market_data_stock:{stock_code}:{single_day_time}:{period}"
    redis_client.set(cache_key, "done")


def _stocks_with_downloaded_rows(market_data: Any, stock_list: List[str]) -> List[str]:
  """Return stocks that actually produced rows after download."""
  if not isinstance(market_data, dict):
    return list(stock_list) if market_data is not None else []

  completed: List[str] = []
  for stock_code in stock_list:
    data = market_data.get(stock_code)
    if data is None:
      continue
    if hasattr(data, "empty") and data.empty:
      continue
    try:
      if len(data) <= 0:
        continue
    except TypeError:
      pass
    completed.append(stock_code)
  return completed


async def _process_single_day_data(
  stock_list: List[str],
  single_day_time: str,
  periods: List[str],
  skip_download: bool = False,
) -> Dict[str, Any]:
  """
  处理单日市场数据同步

  Args:
      stock_list: 股票列表
      single_day_time: 日期时间（开始和结束时间相同）
      periods: 时间周期列表

  Returns:
      单日处理结果
  """
  logger = get_run_logger()

  if stock_list is None:
    raise ValueError("stock_list 不能为空")

  final_stock_list = stock_list
  total_stocks = len(final_stock_list)
  logger.info(f"待同步股票数量: {total_stocks}")

  # 初始化聚合统计
  aggregate_results = {
    "stock_count": total_stocks,
    "start_time": time_utils.now(),
    "chunk_results": [],
    "success_count": 0,
    "failed_count": 0,
    "errors": [],
  }

  # 分片处理
  def chunks(lst, n):
    for i in range(0, len(lst), n):
      yield lst[i : i + n]

  chunk_size_settings = {
    "tick": 100,
    "1m": 200,
    "1d": 500,  # 日线数据量小，可使用更大的分片
  }

  for period in periods:
    logger.info(f"开始处理数据周期: {period}")

    chunk_size = chunk_size_settings.get(period, 500)
    total_chunks = (total_stocks + chunk_size - 1) // chunk_size
    logger.info(
      f"数据周期 {period} 日期 {single_day_time} 使用分片大小: {chunk_size}, 预计分片数量: {total_chunks}"
    )

    processed_chunks = 0
    for idx, chunk in enumerate(chunks(final_stock_list, chunk_size), start=1):
      logger.info(
        f"数据周期 {period} 日期 {single_day_time} 开始处理分片 {idx}/{total_chunks}, 包含 {len(chunk)} 只股票"
      )

      # 过滤已完成的股票
      uncompleted_chunk = _filter_completed_stocks(chunk, period, single_day_time)

      if not uncompleted_chunk:
        logger.info(
          f"数据周期 {period} 日期 {single_day_time} 分片 {idx}/{total_chunks} 所有股票已完成，跳过"
        )
        continue

      processed_chunks += 1
      logger.info(
        f"数据周期 {period} 日期 {single_day_time} 分片 {idx}/{total_chunks} 开始处理，原始 {len(chunk)} 只，待处理 {len(uncompleted_chunk)} 只"
      )

      # 步骤1: 下载该分片数据
      try:
        if not skip_download:
          logger.info(
            f"数据周期 {period} 日期 {single_day_time} 分片 {idx}/{total_chunks} 开始下载..."
          )
          await download_market_data(
            stock_list=uncompleted_chunk,
            period=period,
            date_time=single_day_time,
          )
        else:
          logger.info(
            f"数据周期 {period} 日期 {single_day_time} 分片 {idx}/{total_chunks} 跳过下载步骤"
          )
      except Exception as e:
        msg = f"数据周期 {period} 日期 {single_day_time} 分片 {idx}/{total_chunks} 下载失败: {e}"
        logger.error(msg)
        aggregate_results["errors"].append(msg)
        # 将分片全部记为失败并继续下个分片
        chunk_result = {
          "chunk_idx": idx,
          "period": period,
          "date": single_day_time,
          "results": [
            {"stock_code": s, "status": "failed", "error": "下载失败"}
            for s in uncompleted_chunk
          ],
        }
        aggregate_results["chunk_results"].append(chunk_result)
        aggregate_results["failed_count"] += len(uncompleted_chunk)
        raise e

      # 步骤2: 保存数据
      data_registry = XTDataManagerRegistry()
      data_manager = data_registry.get_manager()

      market_data = data_manager.get_market_data(
        stock_list=uncompleted_chunk,
        period=period,
        start_time=single_day_time,
        end_time=single_day_time,
        dividend_type="none"
      )
      period_data = market_data  # 保留引用以便后续删除
      logger.info(
        f"数据周期 {period} 日期 {single_day_time} 分片 {idx}/{total_chunks} 数据已获取, 准备保存..."
      )

      saved = 0
      save_result = await save_market_data(period=period, market_data=market_data)
      saved = save_result.get("saved_count", 0)

      logger.info(
        f"数据周期 {period} 日期 {single_day_time} 分片 {idx}/{total_chunks} 保存数据完成, 成功保存 {saved} 条记录"
      )

      completed_stocks = (
        _stocks_with_downloaded_rows(market_data, uncompleted_chunk)
        if saved > 0
        else []
      )
      failed_stocks = [
        stock_code
        for stock_code in uncompleted_chunk
        if stock_code not in set(completed_stocks)
      ]

      if completed_stocks:
        _mark_stocks_completed(completed_stocks, period, single_day_time)
      if failed_stocks:
        msg = (
          f"数据周期 {period} 日期 {single_day_time} 分片 {idx}/{total_chunks} "
          f"未获取到有效数据: {failed_stocks}"
        )
        logger.warning(msg)
        aggregate_results["errors"].append(msg)

      # 更新统计
      aggregate_results["success_count"] += len(completed_stocks)
      aggregate_results["failed_count"] += len(failed_stocks)

      # 添加分片报告
      chunk_result = {
        "chunk_idx": idx,
        "period": period,
        "date": single_day_time,
        "original_count": len(chunk),
        "processed_count": len(uncompleted_chunk),
        "saved_count": saved,
        "results": [
          {"stock_code": s, "status": "success"}
          for s in completed_stocks
        ]
        + [
          {"stock_code": s, "status": "failed", "error": "empty_market_data"}
          for s in failed_stocks
        ],
      }
      aggregate_results["chunk_results"].append(chunk_result)

      # 释放分片数据
      try:
        del period_data
      except Exception:
        pass
      logger.info(
        f"数据周期 {period} 日期 {single_day_time} 分片 {idx}/{total_chunks} 处理完成"
      )

    # 周期处理完成日志
    logger.info(
      f"数据周期 {period} 日期 {single_day_time} 处理完成，共处理 {processed_chunks} 个分片"
    )

  # 设置结束时间
  aggregate_results["end_time"] = time_utils.now()
  sync_duration = (
    aggregate_results["end_time"] - aggregate_results["start_time"]
  ).total_seconds()
  aggregate_results["duration_seconds"] = sync_duration

  return aggregate_results


@flow(
  name="市场数据同步流程",
  description="分步骤同步K线与tick数据：下载→获取→保存→报告。支持按天分割处理大时间跨度。",
  retries=1,
  retry_delay_seconds=60,
  **STANDARD_FLOW_HOOKS
)
async def daily_market_data_sync_flow(
  sectors: Optional[List[str]] = None,
  stock_list: Optional[List[str]] = None,
  start_time: str = "",
  end_time: str = "",
  periods: Optional[List[str]] = ["1m"],
  skip_download: bool = False,
  compute_daily_signals: bool = True,
) -> Dict[str, Any]:
  """
  市场数据同步主流程

  支持全市场同步：当 stock_list 为 None 时，自动获取全市场股票代码并分片处理。
  支持大时间跨度：当时间跨度超过1天时，自动按天分割处理，避免数据量过大。

  执行步骤:
  1. 校验 periods 参数，确保仅支持 tick 和 1m
  2. 按天拆分时间范围
  3. 准备待同步的股票列表并检查整体缓存
  4. 初始化全局聚合统计结构
  5. 获取交易日并过滤非交易日
  6. 调用单日处理逻辑并汇总结果
  7. 生成同步报告并输出

  Args:
      sectors: 板块名称列表，用于获取板块内股票
      stock_list: 股票代码列表，如 ["000001.SZ", "600000.SH", "510050.SH"]。若为 None，则同步全市场
      periods: 数据周期列表，只支持 ['tick', '1m']，其他维度通过1m计算得到
      start_time: 开始时间，格式 'YYYYMMDD' 或 'YYYYMMDD HHMMSS'
      end_time: 结束时间，格式 'YYYYMMDD' 或 'YYYYMMDD HHMMSS'

  Returns:
      同步结果统计
  """
  logger = get_run_logger()
  start_sync_time = time_utils.now()
  periods = periods or ["1m"]

  time_range = _resolve_time_range_from_schedule(start_time, end_time)
  start_time = time_range["start_time"]
  end_time = time_range["end_time"]

  logger.info("=" * 60)
  logger.info("开始市场数据同步流程")
  logger.info(f"数据周期: {periods}")
  logger.info(f"时间范围: {start_time} ~ {end_time}")
  logger.info(f"时间范围来源: {time_range['source']}")
  logger.info("=" * 60)

  # 1. 验证periods参数
  try:
    _validate_periods(periods)
    logger.info(f"periods参数验证通过: {periods}")
  except ValueError as e:
    logger.error(f"periods参数验证失败: {e}")
    return {
      "status": "failed",
      "reason": "invalid_periods",
      "error": str(e),
      "start_time": start_sync_time,
      "end_time": time_utils.now(),
      "periods": periods,
    }

  # 2. 时间拆分处理
  try:
    date_list = _split_date_ranges(start_time, end_time)
    # 优化日志输出：日期过多时只显示首尾
    if len(date_list) > 6:
      preview = f"{date_list[:3]} ... {date_list[-3:]}"
    else:
      preview = str(date_list)
    logger.info(f"时间拆分结果: 共 {len(date_list)} 个日期 - {preview}")
  except ValueError as e:
    logger.error(f"时间拆分失败: {e}")
    return {
      "status": "failed",
      "reason": "invalid_time_range",
      "error": str(e),
      "start_time": start_sync_time,
      "end_time": time_utils.now(),
      "periods": periods,
    }

  # 3. 准备待同步股票列表
  normalized_sectors = None
  if sectors:
    normalized_sectors = [s.strip() for s in sectors if s and s.strip()]
    normalized_sectors = list(dict.fromkeys(normalized_sectors))
  cache_sectors = sorted(normalized_sectors) if normalized_sectors else None

  if normalized_sectors:
    instrument_service = InstrumentService()
    where = InstrumentWhereBuilder().by_sectors(normalized_sectors)
    instrument_list = await instrument_service.find_all(where=where)
    resolved_stock_list = [inst.id for inst in instrument_list]
    stock_source_desc = f"根据板块 {', '.join(normalized_sectors)} 获取股票"
  elif stock_list is not None:
    resolved_stock_list = list(stock_list)
    stock_source_desc = "使用传入的股票列表"
  else:
    error_msg = "必须提供 sectors 或 stock_list"
    logger.error(error_msg)
    return {
      "status": "failed",
      "reason": "missing_target",
      "error": error_msg,
      "start_time": start_sync_time,
      "end_time": time_utils.now(),
      "periods": periods,
    }

  resolved_stock_list = list(dict.fromkeys(resolved_stock_list))
  logger.info(f"{stock_source_desc}，共 {len(resolved_stock_list)} 只股票")

  overall_cache_key = (
    f"daily_market_data_sync_complete:"
    f"{'|'.join(cache_sectors) if cache_sectors else ''}"
    f"{''.join(sorted(resolved_stock_list)) if resolved_stock_list else ''}:"
    f"{start_time}-{end_time}:{''.join(sorted(periods))}"
  )

  if redis_client.exists(overall_cache_key):
    cache_key_log = _format_cache_key_for_log(overall_cache_key)
    logger.info(
      "检测到该时间范围与股票集合已完成同步，将跳过执行。"
      f" 缓存键: {cache_key_log}"
    )
    return {
      "status": "skipped",
      "reason": "already_completed",
      "cache_key": overall_cache_key,
      "start_time": start_sync_time,
      "end_time": time_utils.now(),
      "periods": periods,
      "stock_count": len(resolved_stock_list),
      "stock_list": resolved_stock_list,
    }

  sync_lock_key = _build_sync_lock_key(overall_cache_key)
  sync_lock_token = str(uuid.uuid4())
  if not _acquire_sync_lock(sync_lock_key, sync_lock_token):
    cache_key_log = _format_cache_key_for_log(overall_cache_key)
    logger.warning(
      "检测到同一时间范围与股票集合正在同步，将跳过本次运行。"
      f" 缓存键: {cache_key_log}, 锁: {sync_lock_key}"
    )
    return {
      "status": "skipped",
      "reason": "already_running",
      "cache_key": overall_cache_key,
      "lock_key": sync_lock_key,
      "start_time": start_sync_time,
      "end_time": time_utils.now(),
      "periods": periods,
      "stock_count": len(resolved_stock_list),
      "stock_list": resolved_stock_list,
    }

  if not resolved_stock_list:
    logger.warning("待同步股票列表为空，将直接跳过数据处理")

  # 4. 初始化全局聚合结果
  global_aggregate_results = {
    "start_time": start_sync_time,
    "chunk_results": [],
    "success_count": 0,
    "failed_count": 0,
    "errors": [],
    "processed_dates": [],
    "total_dates": len(date_list),
    "stock_list": resolved_stock_list,
    "stock_count": len(resolved_stock_list),
  }

  # 5. 获取交易日期用于过滤
  data_manager = XTDataManagerRegistry().get_manager()

  # 转换时间字符串为date类型
  start_date = _parse_date(start_time).date()
  end_date = _parse_date(end_time).date()

  # 获取交易日期列表
  trading_dates = data_manager.get_trading_dates(
    market="SH", start_date=start_date, end_date=end_date
  )
  logger.info(f"获取到交易日期: {len(trading_dates)} 个")

  # 6. 按日期循环处理
  total_dates = len(date_list)
  processed_count = 0
  skipped_count = 0

  for date_idx, single_day_time in enumerate(date_list, 1):
    # 检查是否为交易日
    if trading_dates:  # 如果有交易日期数据，则进行过滤
      # 解析当前处理的日期
      current_date = _parse_date(single_day_time).date()

      if current_date not in trading_dates:
        logger.info(
          f"跳过第 {date_idx}/{total_dates} 个日期: {single_day_time} (非交易日)"
        )
        skipped_count += 1
        continue

    logger.info(
      f"开始处理第 {date_idx}/{total_dates} 个日期: {single_day_time} (交易日)"
    )
    processed_count += 1

    try:
      # 调用单天处理函数
      day_result = await _process_single_day_data(
        stock_list=resolved_stock_list,
        single_day_time=single_day_time,
        periods=periods,
        skip_download=skip_download,
      )

      # 合并结果
      global_aggregate_results["success_count"] += day_result.get("success_count", 0)
      global_aggregate_results["failed_count"] += day_result.get("failed_count", 0)
      global_aggregate_results["errors"].extend(day_result.get("errors", []))
      global_aggregate_results["chunk_results"].extend(
        day_result.get("chunk_results", [])
      )
      global_aggregate_results["processed_dates"].append(
        {
          "date": single_day_time,
          "success_count": day_result.get("success_count", 0),
          "failed_count": day_result.get("failed_count", 0),
          "duration": day_result.get("duration_seconds", 0),
        }
      )

      logger.info(f"日期 {single_day_time} 处理完成")

    except Exception as e:
      error_msg = f"日期 {single_day_time} 处理失败: {e}"
      logger.error(error_msg)
      global_aggregate_results["errors"].append(error_msg)
      global_aggregate_results["processed_dates"].append(
        {
          "date": single_day_time,
          "success_count": 0,
          "failed_count": 0,
          "duration": 0,
          "error": str(e),
        }
      )

  # 如果所有日期处理成功，标记整体任务完成
  if (
    global_aggregate_results["failed_count"] == 0
    and len(global_aggregate_results["errors"]) == 0
  ):
    redis_client.set(overall_cache_key, "done")
    cache_key_log = _format_cache_key_for_log(overall_cache_key)
    if normalized_sectors:
      logger.info(
        "整体任务成功完成，设置缓存: %s (板块: %s, 股票数: %d)",
        cache_key_log,
        ", ".join(normalized_sectors),
        len(resolved_stock_list),
      )
    else:
      logger.info(
        "整体任务成功完成，设置缓存: %s (股票数: %d)",
        cache_key_log,
        len(resolved_stock_list),
      )

  signal_snapshot_result = None
  if compute_daily_signals and "1d" in periods and global_aggregate_results["processed_dates"]:
    try:
      logger.info("日线同步完成，开始触发日级信号快照预计算")
      signal_snapshot_result = await daily_indicator_snapshot_flow(
        sectors=normalized_sectors or ["沪深A股", "沪深ETF"],
        stock_list=resolved_stock_list,
      )
      global_aggregate_results["signal_snapshot"] = signal_snapshot_result
    except Exception as e:
      msg = f"日级信号快照预计算触发失败: {e}"
      logger.error(msg)
      global_aggregate_results["errors"].append(msg)
      global_aggregate_results["signal_snapshot"] = {
        "status": "failed",
        "error": str(e),
      }

  # 6. 汇总并生成报告
  end_sync_time = time_utils.now()
  sync_duration = (end_sync_time - start_sync_time).total_seconds()
  global_aggregate_results.update(
    {"end_time": end_sync_time, "duration_seconds": sync_duration}
  )

  # 计算总处理量（估算）
  total_processed = (
    global_aggregate_results["success_count"] + global_aggregate_results["failed_count"]
  )

  # 生成详细报告
  report_data = await generate_sync_report(
    task_name="市场数据同步（按天分割）",
    start_time=global_aggregate_results["start_time"],
    fetched_count=total_processed,
    saved_count=global_aggregate_results["success_count"],
    status="success"
    if global_aggregate_results["failed_count"] == 0
    and len(global_aggregate_results["errors"]) == 0
    else "partial_success",
    flow_name="市场数据同步",
    results=global_aggregate_results,
    summary={
      "total_dates": total_dates,
      "processed_dates": len(global_aggregate_results["processed_dates"]),
      "skipped_dates": skipped_count,
      "total_periods": len(periods),
      "periods": periods,
      "success_rate": (
        global_aggregate_results["success_count"] / max(total_processed, 1)
      )
      * 100,
      "duration": f"{sync_duration:.2f}s",
      "time_range": f"{start_time} ~ {end_time}",
    },
  )

  if report_data:
    await save_report_to_file(
      report=report_data,
      report_type=f"market_data_sync_split_{start_sync_time.strftime('%Y%m%d_%H%M%S')}",
    )

  # 设置最终状态
  if (
    global_aggregate_results["failed_count"] == 0
    and len(global_aggregate_results["errors"]) == 0
  ):
    global_aggregate_results["status"] = "success"
  else:
    global_aggregate_results["status"] = "partial_success"

  logger.info("=" * 60)
  logger.info("市场数据同步流程完成")
  logger.info(f"总耗时: {sync_duration:.2f}s")
  logger.info(f"总日期: {total_dates} 个")
  logger.info(f"处理日期: {processed_count} 个 (交易日)")
  logger.info(f"跳过日期: {skipped_count} 个 (非交易日)")
  logger.info(f"成功处理: {global_aggregate_results['success_count']}")
  logger.info(f"失败处理: {global_aggregate_results['failed_count']}")
  logger.info(f"错误数量: {len(global_aggregate_results['errors'])}")
  logger.info("=" * 60)

  _release_sync_lock(sync_lock_key, sync_lock_token)
  return global_aggregate_results
