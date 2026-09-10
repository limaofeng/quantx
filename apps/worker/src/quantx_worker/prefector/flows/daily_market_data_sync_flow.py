"""日线同步、入库与日级指标快照编排 Flow。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, time
from typing import Any, Optional

from prefect import flow, get_run_logger
from prefect.runtime import flow_run as flow_run_runtime
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.services.market_data_sync_audit import MarketDataSyncAudit
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

from quantx_worker.prefector.flows.daily_indicator_snapshot_flow import (
  _parse_date,
  _scheduled_start_time,
  daily_indicator_snapshot_flow,
  expected_snapshot_date,
  resolve_instruments,
)
from quantx_worker.prefector.flows.durable_agent_flows import _request_and_wait
from quantx_worker.prefector.flows.market_data_sync_partitions import (
  InstrumentLifetimes,
  MarketPartition,
  iter_market_partitions,
  validate_market_partition,
)
from quantx_worker.prefector.flows.market_sync_observation import (
  observation,
  observe_market_sync,
)
from quantx_worker.prefector.flows.stock_probability_inference_flow import (
  stock_probability_inference_flow,
)

DEFAULT_MARKET_SECTORS = ["沪深A股", "沪深ETF", "沪深指数"]
SUPPORTED_PERIODS = {"tick", "1m", "1d"}
MARKET_DATA_REQUEST_BATCH_SIZE = 300
MARKET_DATA_REQUEST_CONCURRENCY = 2
MARKET_DATA_RETRY_DELAY_SECONDS = 60.0


def _validate_periods(periods: list[str]) -> list[str]:
  normalized = list(dict.fromkeys(str(item or "").lower() for item in periods))
  invalid = [item for item in normalized if item not in SUPPORTED_PERIODS]
  if invalid:
    raise ValueError(f"不支持的数据周期: {invalid}; 仅支持 {sorted(SUPPORTED_PERIODS)}")
  if not normalized:
    raise ValueError("至少选择一个数据周期")
  return normalized


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
    "idempotency_scope": (f"{idempotency_scope}:batch:{batch_index:04d}")
  }
  if agent_device_id:
    request_kwargs["agent_device_id"] = agent_device_id
  observer = observation.get()
  audit = getattr(observer, "audit", None)
  current_request = ""

  async def on_created(request_id):
    nonlocal current_request
    current_request = request_id
    if audit:
      await audit.record(batch_index, request_payload, request_id, "PENDING", {})

  request_kwargs["on_created"] = on_created
  try:
    transfer = await _request_and_wait(request_payload, **request_kwargs)
    current_request = str(transfer.get("request_id") or current_request)
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
      error = MarketDataPartitionFailure(
        f"{exc}; request_id={transfer.get('request_id')}"
      )
      error.counts = {
        key: value if isinstance(value, int) and value >= 0 else 0
        for key in ("records_received", "records_saved")
        for value in (transfer.get(key),)
      }
      raise error from exc
    if audit:
      await audit.record(
        batch_index,
        request_payload,
        current_request,
        "VERIFIED",
        {
          "records_received": int(transfer["records_received"]),
          "records_saved": int(transfer["records_saved"]),
        },
      )
    return batch_index - 1, {
      "request_id": current_request,
      "records_received": transfer["records_received"],
      "records_saved": transfer["records_saved"],
    }
  except MarketDataPartitionFailure as exc:
    if audit:
      await audit.record(
        batch_index,
        request_payload,
        current_request,
        "INCOMPLETE",
        {"reason": str(exc)[:2000], **exc.counts},
      )
    raise
  except BaseException:
    if observer and current_request:
      observer.logger.warning(
        "本次等待终止，持久化请求可能仍在后台运行: request_id=%s", current_request
      )
    raise
  finally:
    if observer and current_request:
      observer.requests.pop(current_request, None)


class MarketDataSyncIncomplete(RuntimeError):
  """All partitions were attempted, with durable evidence for failed scopes."""

  def __init__(
    self,
    failures: list[dict[str, Any]],
    total_batches: int,
    failed_count: int | None = None,
  ) -> None:
    self.failures = failures[:10]
    self.failed_count = len(failures) if failed_count is None else failed_count
    self.total_batches = total_batches
    examples = "; ".join(
      f"batch={item['batch_index']} {item['reason']}" for item in failures[:10]
    )
    super().__init__(
      f"行情同步未完整完成: failed={self.failed_count}/{total_batches}; {examples}"
    )

  def __reduce__(self):
    return type(self), (self.failures, self.total_batches, self.failed_count)


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
) -> dict[str, Any]:
  days = await TradingDateHelper().get_trading_calendar(
    market="SH", start_date=_parse_date(start_time), end_date=_parse_date(end_time)
  )
  if days != sorted(set(days)) or any(
    not _parse_date(start_time) <= day <= _parse_date(end_time) for day in days
  ):
    raise ValueError("行情同步交易日历重复、无序或超出请求区间")

  from quantx_contracts.data_exchange import MAX_REMOTE_HISTORY_PARTITIONS
  from quantx_infrastructure.config.settings import settings

  delivery_limit = (
    MAX_REMOTE_HISTORY_PARTITIONS if settings.environment == "development" else None
  )

  def partitions():
    return iter_market_partitions(
      codes,
      days,
      start_time,
      end_time,
      periods,
      lifetimes,
      max_delivery_partitions=delivery_limit,
    )

  total = await asyncio.to_thread(lambda: sum(1 for _ in partitions()))
  observer = observation.get()
  if observer:
    observer.total = total
    observer.completed = observer.failed = observer.saved = 0
    observer.phase = "同步分区"
  logger.info(
    "行情分区计划: batches=%s periods=%s range=%s..%s concurrency=%s",
    total,
    periods,
    start_time,
    end_time,
    MARKET_DATA_REQUEST_CONCURRENCY,
  )
  pending = enumerate(partitions(), start=1)
  active: dict[asyncio.Task, tuple[int, MarketPartition]] = {}
  summary = {
    "status": "completed",
    "batch_count": total,
    "records_received": 0,
    "records_saved": 0,
    "request_id": None,
    "run_id": str(flow_run_runtime.id or ""),
  }
  failures = []
  failed_count = 0
  completed_count = 0

  def launch():
    item = next(pending, None)
    if item is None:
      return False
    index, part = item
    task = asyncio.create_task(
      _request_market_data_batch(
        code_batch=part.codes,
        batch_index=index,
        total_batches=total,
        periods=part.periods,
        start_time=part.start,
        end_time=part.end,
        agent_device_id=agent_device_id,
        idempotency_scope=idempotency_scope,
        trading_days=part.days,
        lifetimes=lifetimes,
      ),
      name=f"market-data-batch-{index}",
    )
    active[task] = item
    return True

  try:
    while len(active) < MARKET_DATA_REQUEST_CONCURRENCY and launch():
      pass
    while active:
      done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
      for task in sorted(done, key=lambda t: active[t][0]):
        index, part = active.pop(task)
        try:
          _, transfer = task.result()
        except MarketDataPartitionFailure as exc:
          failed_count += 1
          summary["records_received"] += exc.counts["records_received"]
          summary["records_saved"] += exc.counts["records_saved"]
          failure = {
            "batch_index": index,
            "stock_list": part.codes,
            "periods": part.periods,
            "start_time": part.start,
            "end_time": part.end,
            "reason": str(exc),
          }
          if len(failures) < 10:
            failures.append(failure)
          logger.warning("行情分区失败，继续后续分区: %s", failure)
          if observer:
            observer.failed = failed_count
            observer.saved = summary["records_saved"]
        else:
          completed_count += 1
          summary["records_received"] += int(transfer["records_received"])
          summary["records_saved"] += int(transfer["records_saved"])
          if total == 1:
            summary["request_id"] = transfer["request_id"]
          if observer:
            observer.completed = completed_count
            observer.saved = summary["records_saved"]
          # Full manifests and day coverage are released with this task, not
          # retained for every completed partition in the logical Flow.
          del transfer
      done.clear()
      task = None
      while len(active) < MARKET_DATA_REQUEST_CONCURRENCY and launch():
        pass
  finally:
    for task in active:
      task.cancel()
    if active:
      await asyncio.gather(*active, return_exceptions=True)
  logger.info(
    "行情同步分区汇总: completed=%s failed=%s total=%s saved=%s run_id=%s",
    completed_count,
    failed_count,
    total,
    summary["records_saved"],
    summary["run_id"],
  )
  if failed_count:
    raise MarketDataSyncIncomplete(failures, total, failed_count)
  return summary


async def _daily_market_data_sync_attempt(
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

  observer = observation.get()
  if observer is not None and observer.instruments is not None:
    instruments = observer.instruments
  else:
    instruments = await resolve_instruments(
      sectors or DEFAULT_MARKET_SECTORS,
      stock_list,
      allowed_types={
        InstrumentType.STOCK,
        InstrumentType.ETF,
        InstrumentType.INDEX,
      },
    )
    if observer is not None:
      observer.instruments = instruments

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
    transfer = await _request_market_data_batches(
      codes=codes,
      periods=normalized_periods,
      start_time=resolved_start,
      end_time=resolved_end,
      agent_device_id=str(agent_device_id).strip(),
      idempotency_scope=_market_data_sync_idempotency_scope(idempotency_scope),
      logger=logger,
      lifetimes={
        item["code"]: (item.get("open_date"), item.get("expire_date"))
        for item in instruments
      },
    )
    if transfer["batch_count"] == 0:
      return {
        "status": "skipped",
        "reason": "请求范围内没有上市存续期内的交易日分区",
        "stock_count": len(codes),
        "start_time": resolved_start,
        "end_time": resolved_end,
        "periods": normalized_periods,
      }

  indicator_result: Optional[dict[str, Any]] = None
  probability_result: Optional[dict[str, Any]] = None
  if compute_daily_signals:
    if observer is not None:
      observer.phase = "日级指标计算"
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
      if observer is not None:
        observer.phase = "概率推理"
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


@flow(name="每日市场数据同步", description="持久化行情同步与持续进度", retries=0)
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
  scope = _market_data_sync_idempotency_scope(idempotency_scope)
  async with observe_market_sync(logger) as observer:
    run_id = str(flow_run_runtime.id or "")
    audit = MarketDataSyncAudit(run_id) if run_id else None
    observer.audit = audit
    try:
      for attempt in range(3):
        try:
          result = await _daily_market_data_sync_attempt(
            sectors=sectors,
            stock_list=stock_list,
            start_time=start_time,
            end_time=end_time,
            periods=periods,
            skip_download=skip_download,
            compute_daily_signals=compute_daily_signals,
            agent_device_id=agent_device_id,
            idempotency_scope=scope,
          )
          observer.phase = result["status"]
          return result
        except (MarketDataSyncIncomplete, ValueError):
          observer.phase = "数据不完整或参数无效"
          raise
        except Exception:
          if attempt == 2:
            observer.phase = "重试耗尽"
            raise
          observer.phase = f"等待内部重试 {attempt + 1}/2"
          logger.warning("行情同步暂时失败，60 秒后重试；保持 Running 和部署并发租约")
          await asyncio.sleep(MARKET_DATA_RETRY_DELAY_SECONDS)
    finally:
      if audit:
        await audit.close()
