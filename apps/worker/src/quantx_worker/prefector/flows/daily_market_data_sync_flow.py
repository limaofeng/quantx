"""日线同步、入库与日级指标快照编排 Flow。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, time
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
  InstrumentLifetimes,
  instrument_active_on,
  market_date_windows,
  plan_tick_partitions,
  validate_market_partition,
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
  if saved != received:
    raise RuntimeError(
      "行情数据未完整入库: "
      f"batch={batch_index}/{total_batches} "
      f"request_id={transfer.get('request_id')} "
      f"received={received} saved={saved}"
    )


class MarketDataPartitionFailure(RuntimeError):
  """A terminal source/coverage failure that does not block other partitions."""


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
  trading_days: list[date],
  lifetimes: InstrumentLifetimes,
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
  try:
    if transfer.get("status") == "completed":
      validate_market_partition(
        transfer,
        code_batch,
        periods,
        start_time,
        end_time,
        trading_days=trading_days,
        lifetimes=lifetimes,
      )
    _validate_market_data_transfer(
      transfer,
      batch_index=batch_index,
      total_batches=total_batches,
    )
  except (RuntimeError, KeyError, TypeError, ValueError) as exc:
    if transfer.get("status") not in {"completed", "failed"}:
      # A timeout leaves a live durable request, rather than a terminal data
      # gap. Stop dispatch until retry so an outage cannot grow the Agent queue.
      raise
    raise MarketDataPartitionFailure(
      f"{exc}; request_id={transfer.get('request_id')}"
    ) from exc
  return batch_index - 1, transfer


class MarketDataSyncIncomplete(RuntimeError):
  """All partitions were attempted, with durable evidence for failed scopes."""

  def __init__(self, failures: list[dict[str, Any]], total_batches: int) -> None:
    self.failures = failures
    self.total_batches = total_batches
    examples = "; ".join(
      f"batch={item['batch_index']} {item['reason']}" for item in failures[:10]
    )
    super().__init__(
      f"行情同步未完整完成: failed={len(failures)}/{total_batches}; {examples}"
    )

  def __reduce__(self):
    return type(self), (self.failures, self.total_batches)


async def _request_market_data_batches(
  *,
  codes: list[str],
  periods: list[str],
  start_time: str,
  end_time: str,
  agent_device_id: str,
  idempotency_scope: str,
  logger: Any,
  lifetimes: InstrumentLifetimes,
) -> list[dict[str, Any]]:
  """Drain a bounded pipeline before surfacing partition failures."""

  days = await TradingDateHelper().get_trading_calendar(
    market="SH", start_date=_parse_date(start_time), end_date=_parse_date(end_time)
  )
  if days != sorted(set(days)) or any(
    not _parse_date(start_time) <= day <= _parse_date(end_time) for day in days
  ):
    raise ValueError("行情同步交易日历重复、无序或超出请求区间")
  if not days:
    return []
  partitions: list[tuple[list[str], str, str]] = []
  days_by_window: dict[tuple[str, str], list[date]] = {}
  if "tick" in periods:
    for code_batch, start, end in plan_tick_partitions(
      codes, days, start_time, end_time, periods
    ):
      day = _parse_date(start)
      if instrument_active_on(code_batch[0], day, lifetimes):
        partitions.append((code_batch, start, end))
        days_by_window[(start, end)] = [day]
  else:
    for start, end in market_date_windows(start_time, end_time, periods):
      window_days = [
        day for day in days if _parse_date(start) <= day <= _parse_date(end)
      ]
      window_codes = [
        code
        for code in codes
        if any(instrument_active_on(code, day, lifetimes) for day in window_days)
      ]
      days_by_window[(start, end)] = window_days
      batch_size = _market_data_request_batch_size(
        periods=periods, start_time=start, end_time=end
      )
      partitions.extend(
        (batch, start, end) for batch in _chunks(window_codes, batch_size)
      )
  total_batches = len(partitions)
  results: list[Optional[dict[str, Any]]] = [None] * total_batches
  active: dict[asyncio.Task[tuple[int, dict[str, Any]]], int] = {}
  next_index = 0
  failures: list[dict[str, Any]] = []

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
        trading_days=days_by_window[
          (partitions[batch_offset][1], partitions[batch_offset][2])
        ],
        lifetimes=lifetimes,
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
        batch_offset = active.pop(task)
        try:
          _, transfer = task.result()
        except MarketDataPartitionFailure as exc:
          partition_codes, start, end = partitions[batch_offset]
          failure = {
            "batch_index": batch_offset + 1,
            "stock_list": partition_codes,
            "periods": periods,
            "start_time": start,
            "end_time": end,
            "reason": f"{type(exc).__name__}: {exc}",
          }
          failures.append(failure)
          logger.warning(
            "Agent 行情批次 %s/%s 失败，继续后续分区: "
            "codes=%s periods=%s range=%s..%s reason=%s",
            batch_offset + 1,
            total_batches,
            partition_codes,
            periods,
            start,
            end,
            failure["reason"],
          )
          continue
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

  if failures:
    raise MarketDataSyncIncomplete(failures, total_batches)
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
    "行情同步参数: target=%s codes=%s sectors=%s periods=%s range=%s..%s "
    "skip_download=%s compute_daily_signals=%s",
    "stock_list" if stock_list else "sectors",
    len(codes),
    [] if stock_list else (sectors or DEFAULT_MARKET_SECTORS),
    normalized_periods,
    resolved_start,
    resolved_end,
    skip_download,
    compute_daily_signals,
  )

  transfer: Optional[dict[str, Any]] = None
  if not skip_download:
    transfers = await _request_market_data_batches(
      codes=codes,
      periods=normalized_periods,
      start_time=resolved_start,
      end_time=resolved_end,
      agent_device_id=str(agent_device_id).strip(),
      idempotency_scope=_market_data_sync_idempotency_scope(
        idempotency_scope
      ),
      logger=logger,
      lifetimes={
        item["code"]: (item.get("open_date"), item.get("expire_date"))
        for item in instruments
      },
    )
    if not transfers:
      return {
        "status": "skipped",
        "reason": "请求范围内没有上市存续期内的交易日分区",
        "stock_count": len(codes),
        "start_time": resolved_start,
        "end_time": resolved_end,
        "periods": normalized_periods,
      }

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
