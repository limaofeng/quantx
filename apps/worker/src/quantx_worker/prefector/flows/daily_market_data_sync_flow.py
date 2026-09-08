"""日线同步、入库与日级指标快照编排 Flow。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import time
from typing import Any, Optional, cast

from prefect import flow, get_run_logger
from prefect.runtime import flow_run as flow_run_runtime
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

from quantx_worker.prefector.flows.daily_indicator_snapshot_flow import (
  _chunks,
  _parse_date,
  _scheduled_start_time,
  daily_indicator_snapshot_flow,
  expected_snapshot_date,
  resolve_instruments,
)
from quantx_worker.prefector.flows.durable_agent_flows import _request_and_wait
from quantx_worker.prefector.flows.market_data_sync_partitions import (
  plan_tick_partitions,
  validate_tick_partition,
)
from quantx_worker.prefector.flows.stock_probability_inference_flow import (
  stock_probability_inference_flow,
)

DEFAULT_MARKET_SECTORS = ["沪深A股", "沪深ETF", "沪深指数"]
SUPPORTED_PERIODS = {"tick", "1m", "1d"}
MARKET_DATA_REQUEST_BATCH_SIZE = 300
MARKET_DATA_REQUEST_CONCURRENCY = 2
MAX_MARKET_DATA_REQUEST_RECORDS = 500_000
ESTIMATED_MARKET_RECORDS_PER_DAY = {
  "tick": 20_000,
  "1m": 300,
  "1d": 1,
}


def _validate_periods(periods: list[str]) -> list[str]:
  normalized = list(dict.fromkeys(str(item or "").lower() for item in periods))
  invalid = [item for item in normalized if item not in SUPPORTED_PERIODS]
  if invalid:
    raise ValueError(
      f"不支持的数据周期: {invalid}; 仅支持 {sorted(SUPPORTED_PERIODS)}"
    )
  if not normalized:
    raise ValueError("至少选择一个数据周期")
  return normalized


def _market_data_request_batch_size(
  *,
  periods: list[str],
  start_time: str,
  end_time: str,
) -> int:
  """Fit each durable request under the Agent's complete-record budget."""

  start_date = _parse_date(start_time)
  end_date = _parse_date(end_time)
  span_days = (end_date - start_date).days + 1
  estimated_per_code = span_days * sum(
    ESTIMATED_MARKET_RECORDS_PER_DAY[period] for period in periods
  ) + len(periods)
  if estimated_per_code <= 0:
    raise ValueError("行情请求记录预算必须为正数")
  return max(
    1,
    min(
      MARKET_DATA_REQUEST_BATCH_SIZE,
      MAX_MARKET_DATA_REQUEST_RECORDS // estimated_per_code,
    ),
  )


async def _resolve_market_time_range(
  start_time: str,
  end_time: str,
) -> tuple[str, str]:
  start_text = str(start_time or "").strip()
  end_text = str(end_time or "").strip()
  if start_text or end_text:
    start_date = _parse_date(start_text or end_text)
    end_date = _parse_date(end_text or start_text)
    if end_date < start_date:
      raise ValueError("行情同步结束日期不能早于开始日期")
    return start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")

  reference = _scheduled_start_time() or time_utils.now()
  target = await expected_snapshot_date(
    reference,
    trading_dates=TradingDateHelper(),
    cutoff=time(15, 5),
  )
  compact = target.strftime("%Y%m%d")
  return compact, compact


def _market_data_sync_idempotency_scope(explicit_scope: str) -> str:
  """Return one retry-stable scope for this logical Prefect flow run."""

  normalized = str(explicit_scope or "").strip()
  if not normalized:
    run_id = str(flow_run_runtime.id or "").strip() or str(uuid.uuid4())
    normalized = f"daily-market-data-sync-v1:{run_id}"
  if len(normalized) > 180:
    raise ValueError("行情同步 idempotency_scope 不能超过 180 个字符")
  return normalized


def _validate_market_data_transfer(
  transfer: dict[str, Any],
  *,
  batch_index: int,
  total_batches: int,
) -> None:
  if transfer.get("status") != "completed":
    raise RuntimeError(
      "QMT Agent 行情请求失败: "
      f"batch={batch_index}/{total_batches} "
      f"request_id={transfer.get('request_id')} "
      f"status={transfer.get('status')} "
      f"reason={transfer.get('reason') or 'unknown'}"
    )
  received = int(transfer.get("records_received") or 0)
  saved = int(transfer.get("records_saved") or 0)
  if received <= 0:
    raise RuntimeError(
      "QMT Agent 未返回任何行情: "
      f"batch={batch_index}/{total_batches} "
      f"request_id={transfer.get('request_id')}"
    )
  if saved < received:
    raise RuntimeError(
      "行情数据未完整入库: "
      f"batch={batch_index}/{total_batches} "
      f"request_id={transfer.get('request_id')} "
      f"received={received} saved={saved}"
    )


async def _request_market_data_batch(
  *,
  code_batch: list[str],
  batch_index: int,
  total_batches: int,
  periods: list[str],
  start_time: str,
  end_time: str,
  agent_device_id: str,
  idempotency_scope: str,
) -> tuple[int, dict[str, Any]]:
  request_payload = {
    "operation": "bars",
    "download": True,
    "stock_list": code_batch,
    "periods": periods,
    "start_time": start_time,
    "end_time": end_time,
  }
  request_kwargs: dict[str, Any] = {
    "idempotency_scope": (
      f"{idempotency_scope}:batch:{batch_index:04d}"
    )
  }
  if "tick" in periods:
    request_kwargs["retry_failed_requests"] = False
  if agent_device_id:
    request_kwargs["agent_device_id"] = agent_device_id
  transfer = await _request_and_wait(request_payload, **request_kwargs)
  if "tick" in periods and transfer.get("status") == "completed":
    validate_tick_partition(transfer, code_batch, periods, start_time, end_time)
  _validate_market_data_transfer(
    transfer,
    batch_index=batch_index,
    total_batches=total_batches,
  )
  return batch_index - 1, transfer


async def _request_market_data_batches(
  *,
  code_batches: list[list[str]],
  periods: list[str],
  start_time: str,
  end_time: str,
  agent_device_id: str,
  idempotency_scope: str,
  logger: Any,
) -> list[dict[str, Any]]:
  """Run a bounded rolling pipeline and retain deterministic batch order."""

  partitions = [(codes, start_time, end_time) for codes in code_batches]
  if "tick" in periods:
    days = await TradingDateHelper().get_trading_calendar(
      market="SH", start_date=_parse_date(start_time), end_date=_parse_date(end_time)
    )
    partitions = plan_tick_partitions(
      [code for codes in code_batches for code in codes],
      days,
      start_time,
      end_time,
      periods,
    )
  total_batches = len(partitions)
  results: list[Optional[dict[str, Any]]] = [None] * total_batches
  active: dict[asyncio.Task[tuple[int, dict[str, Any]]], int] = {}
  next_index = 0

  def launch(batch_offset: int) -> None:
    batch_index = batch_offset + 1
    task = asyncio.create_task(
      _request_market_data_batch(
        code_batch=partitions[batch_offset][0],
        batch_index=batch_index,
        total_batches=total_batches,
        periods=periods,
        start_time=partitions[batch_offset][1],
        end_time=partitions[batch_offset][2],
        agent_device_id=agent_device_id,
        idempotency_scope=idempotency_scope,
      ),
      name=f"market-data-batch-{batch_index}",
    )
    active[task] = batch_offset

  while next_index < min(MARKET_DATA_REQUEST_CONCURRENCY, total_batches):
    launch(next_index)
    next_index += 1

  completed: list[asyncio.Task[tuple[int, dict[str, Any]]]] = []
  try:
    while active:
      done, _ = await asyncio.wait(
        active,
        return_when=asyncio.FIRST_COMPLETED,
      )
      completed = sorted(done, key=lambda task: active[task])
      for task in completed:
        active.pop(task)
      for task in completed:
        batch_offset, transfer = task.result()
        results[batch_offset] = transfer
        logger.info(
          "Agent 行情批次 %s/%s 完成: codes=%s request_id=%s "
          "status=%s received=%s saved=%s",
          batch_offset + 1,
          total_batches,
          len(partitions[batch_offset][0]),
          transfer.get("request_id"),
          transfer.get("status"),
          transfer.get("records_received"),
          transfer.get("records_saved"),
        )
      while (
        next_index < total_batches
        and len(active) < MARKET_DATA_REQUEST_CONCURRENCY
      ):
        launch(next_index)
        next_index += 1
  except BaseException:
    abandoned = [*active, *completed]
    for task in active:
      task.cancel()
    if abandoned:
      await asyncio.gather(*abandoned, return_exceptions=True)
    raise

  if any(item is None for item in results):
    raise RuntimeError("行情批次流水线未生成完整结果")
  return cast(list[dict[str, Any]], results)


@flow(
  name="每日市场数据同步",
  description="经持久化消息箱请求 QMT Agent，入库后按需计算日级快照",
  retries=2,
  retry_delay_seconds=60,
)
async def daily_market_data_sync_flow(
  sectors: Optional[list[str]] = None,
  stock_list: Optional[list[str]] = None,
  start_time: str = "",
  end_time: str = "",
  periods: Optional[list[str]] = None,
  skip_download: bool = False,
  compute_daily_signals: bool = False,
  agent_device_id: str = "",
  idempotency_scope: str = "",
) -> dict[str, Any]:
  logger = get_run_logger()
  normalized_periods = _validate_periods(periods or ["1d"])
  if compute_daily_signals and "1d" not in normalized_periods:
    raise ValueError("计算日级指标必须选择 1d 周期")
  if skip_download and (not compute_daily_signals or "1d" not in normalized_periods):
    raise ValueError("仅补算指标必须同时启用指标计算并选择 1d")

  resolved_start, resolved_end = await _resolve_market_time_range(
    start_time,
    end_time,
  )
  if compute_daily_signals:
    start_date = _parse_date(resolved_start)
    end_date = _parse_date(resolved_end)
    if (end_date - start_date).days + 1 > 30:
      raise ValueError("指标补算日期范围最多 30 天")

  instruments = await resolve_instruments(
    sectors or DEFAULT_MARKET_SECTORS,
    stock_list,
    allowed_types={
      InstrumentType.STOCK,
      InstrumentType.ETF,
      InstrumentType.INDEX,
    },
  )
  codes = [item["code"] for item in instruments]
  if not codes:
    raise RuntimeError("PostgreSQL 中没有匹配的行情标的")
  logger.info(
    "行情同步参数: codes=%s sectors=%s periods=%s range=%s..%s "
    "skip_download=%s compute_daily_signals=%s",
    len(codes),
    sectors or DEFAULT_MARKET_SECTORS,
    normalized_periods,
    resolved_start,
    resolved_end,
    skip_download,
    compute_daily_signals,
  )

  transfer: Optional[dict[str, Any]] = None
  if not skip_download:
    request_batch_size = _market_data_request_batch_size(
      periods=normalized_periods,
      start_time=resolved_start,
      end_time=resolved_end,
    )
    code_batches = list(_chunks(codes, request_batch_size))
    transfers = await _request_market_data_batches(
      code_batches=code_batches,
      periods=normalized_periods,
      start_time=resolved_start,
      end_time=resolved_end,
      agent_device_id=str(agent_device_id).strip(),
      idempotency_scope=_market_data_sync_idempotency_scope(
        idempotency_scope
      ),
      logger=logger,
    )

    transfer = {
      "status": "completed",
      "request_id": (
        transfers[0].get("request_id") if len(transfers) == 1 else None
      ),
      "request_ids": [item.get("request_id") for item in transfers],
      "batch_count": len(transfers),
      "records_received": sum(
        int(item.get("records_received") or 0) for item in transfers
      ),
      "records_saved": sum(
        int(item.get("records_saved") or 0) for item in transfers
      ),
      "batches": transfers,
    }

  indicator_result: Optional[dict[str, Any]] = None
  probability_result: Optional[dict[str, Any]] = None
  if compute_daily_signals:
    indicator_result = await daily_indicator_snapshot_flow(
      sectors=sectors or ["沪深A股", "沪深ETF"],
      stock_list=stock_list,
      start_time=resolved_start,
      end_time=resolved_end,
      batch_size=300,
      retain_days=30,
    )
    if indicator_result.get("status") != "success":
      failed_dates = [
        item["snapshot_date"]
        for item in indicator_result.get("dates", [])
        if item.get("status") != "success"
      ]
      raise RuntimeError(
        f"日级指标快照未全部成功: {', '.join(failed_dates) or 'unknown'}"
      )
    completed_dates = [
      str(item["snapshot_date"])
      for item in indicator_result.get("dates", [])
      if item.get("status") == "success"
    ]
    if completed_dates:
      probability_result = await stock_probability_inference_flow(
        as_of=max(completed_dates)
      )
      if probability_result.get("status") == "failed":
        raise RuntimeError("次日上涨概率推理存在失败模型，已保留失败运行证据")

  return {
    "status": "success",
    "stock_count": len(codes),
    "start_time": resolved_start,
    "end_time": resolved_end,
    "periods": normalized_periods,
    "skip_download": skip_download,
    "transfer": transfer,
    "indicator_snapshot": indicator_result,
    "probability_inference": probability_result,
    "completed_at": time_utils.now().isoformat(),
  }
