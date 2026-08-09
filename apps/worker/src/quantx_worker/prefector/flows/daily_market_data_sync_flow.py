"""日线同步、入库与日级指标快照编排 Flow。"""

from __future__ import annotations

from datetime import time
from typing import Any, Optional

from prefect import flow, get_run_logger
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

DEFAULT_MARKET_SECTORS = ["沪深A股", "沪深ETF", "沪深指数"]
SUPPORTED_PERIODS = {"tick", "1m", "1d"}
MARKET_DATA_REQUEST_BATCH_SIZE = 300


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


@flow(
  name="每日市场数据同步",
  description="经持久化消息箱请求 QMT Agent，入库后按需计算日级快照",
  retries=0,
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
    transfers: list[dict[str, Any]] = []
    total_batches = (
      len(codes) + MARKET_DATA_REQUEST_BATCH_SIZE - 1
    ) // MARKET_DATA_REQUEST_BATCH_SIZE
    for batch_index, code_batch in enumerate(
      _chunks(codes, MARKET_DATA_REQUEST_BATCH_SIZE),
      start=1,
    ):
      request_payload = {
        "operation": "bars",
        "download": True,
        "stock_list": code_batch,
        "periods": normalized_periods,
        "start_time": resolved_start,
        "end_time": resolved_end,
      }
      if agent_device_id:
        batch_transfer = await _request_and_wait(
          request_payload,
          agent_device_id=str(agent_device_id).strip(),
        )
      else:
        batch_transfer = await _request_and_wait(request_payload)
      logger.info(
        "Agent 行情批次 %s/%s 完成: codes=%s request_id=%s "
        "status=%s received=%s saved=%s",
        batch_index,
        total_batches,
        len(code_batch),
        batch_transfer.get("request_id"),
        batch_transfer.get("status"),
        batch_transfer.get("records_received"),
        batch_transfer.get("records_saved"),
      )
      if batch_transfer.get("status") != "completed":
        raise RuntimeError(
          "QMT Agent 行情请求失败: "
          f"batch={batch_index}/{total_batches} "
          f"request_id={batch_transfer.get('request_id')} "
          f"status={batch_transfer.get('status')} "
          f"reason={batch_transfer.get('reason') or 'unknown'}"
        )
      received = int(batch_transfer.get("records_received") or 0)
      saved = int(batch_transfer.get("records_saved") or 0)
      if received <= 0:
        raise RuntimeError(
          "QMT Agent 未返回任何行情: "
          f"batch={batch_index}/{total_batches} "
          f"request_id={batch_transfer.get('request_id')}"
        )
      if saved < received:
        raise RuntimeError(
          "行情数据未完整入库: "
          f"batch={batch_index}/{total_batches} "
          f"request_id={batch_transfer.get('request_id')} "
          f"received={received} saved={saved}"
        )
      transfers.append(batch_transfer)

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

  return {
    "status": "success",
    "stock_count": len(codes),
    "start_time": resolved_start,
    "end_time": resolved_end,
    "periods": normalized_periods,
    "skip_download": skip_download,
    "transfer": transfer,
    "indicator_snapshot": indicator_result,
    "completed_at": time_utils.now().isoformat(),
  }
