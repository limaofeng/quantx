"""Audit and repair the completed 1m history used by the market workbench."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from prefect import flow, get_run_logger
from prefect.runtime import flow_run as flow_run_runtime
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.repositories.kline_repository import KLineRepository
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

from quantx_worker.prefector.flows.daily_indicator_snapshot_flow import (
  _parse_date,
  _scheduled_start_time,
  expected_snapshot_date,
)
from quantx_worker.prefector.flows.durable_agent_flows import _request_and_wait

CORE_INDEX_SYMBOLS = (
  "000001.SH",
  "399001.SZ",
  "399006.SZ",
  "000300.SH",
  "000905.SH",
  "000852.SH",
)
REPAIR_CUTOFF = time(15, 10)
DEFAULT_LOOKBACK_TRADING_DAYS = 3
MAX_LOOKBACK_TRADING_DAYS = 10


def _repair_attempt_scope(scheduled: Optional[datetime]) -> str:
  """Return a scope stable across retries of one Prefect flow run."""
  try:
    flow_run_id = str(flow_run_runtime.get_id() or "").strip()
  except Exception:
    flow_run_id = ""
  if flow_run_id:
    return f"run:{flow_run_id}"
  if scheduled is not None:
    return f"scheduled:{scheduled.replace(second=0, microsecond=0).isoformat()}"
  return f"manual:{time_utils.now().replace(second=0, microsecond=0).isoformat()}"


def _minute_slots(start: time, end: time) -> set[tuple[int, int]]:
  current = datetime.combine(date.min, start)
  boundary = datetime.combine(date.min, end)
  slots: set[tuple[int, int]] = set()
  while current <= boundary:
    slots.add((current.hour, current.minute))
    current += timedelta(minutes=1)
  return slots


# QMT's persisted index history uses 09:30..11:30 and 13:01..15:00 as the
# required continuous-auction minute keys. Some downloads also contain 09:25
# or 13:00; those optional rows are retained but do not affect completeness.
REQUIRED_MINUTE_SLOTS = frozenset(
  _minute_slots(time(9, 30), time(11, 30)) | _minute_slots(time(13, 1), time(15, 0))
)


def _is_valid_bar(bar: KLine) -> bool:
  try:
    open_price = float(bar.open)
    high_price = float(bar.high)
    low_price = float(bar.low)
    close_price = float(bar.close)
    volume = float(bar.volume)
    amount = float(bar.amount)
  except (AttributeError, TypeError, ValueError):
    return False
  values = (open_price, high_price, low_price, close_price, volume, amount)
  return (
    all(math.isfinite(value) for value in values)
    and open_price > 0
    and close_price > 0
    and high_price >= max(open_price, close_price)
    and low_price <= min(open_price, close_price)
    and volume >= 0
    and amount >= 0
  )


def assess_intraday_coverage(
  records: Iterable[KLine],
  *,
  target_date: date,
  stock_codes: Iterable[str] = CORE_INDEX_SYMBOLS,
) -> dict[str, Any]:
  """Assess exact required minute coverage for every requested index."""
  requested_codes = tuple(
    dict.fromkeys(str(code or "").strip().upper() for code in stock_codes)
  )
  rows_by_code: dict[str, list[tuple[datetime, KLine]]] = {
    code: [] for code in requested_codes
  }
  for bar in records:
    code = str(getattr(bar, "stock_code", "") or "").strip().upper()
    if code not in rows_by_code:
      continue
    raw_time = getattr(bar, "time", None)
    if not isinstance(raw_time, datetime):
      continue
    local_time = time_utils.to_shanghai(raw_time)
    if local_time.date() == target_date:
      rows_by_code[code].append((local_time, bar))

  code_results: dict[str, dict[str, Any]] = {}
  for code in requested_codes:
    rows = rows_by_code[code]
    distinct_times = {item[0].replace(second=0, microsecond=0) for item in rows}
    valid_slots = {
      (local_time.hour, local_time.minute)
      for local_time, bar in rows
      if _is_valid_bar(bar)
    }
    missing_slots = sorted(REQUIRED_MINUTE_SLOTS - valid_slots)
    invalid_required_rows = sum(
      1
      for local_time, bar in rows
      if (local_time.hour, local_time.minute) in REQUIRED_MINUTE_SLOTS
      and not _is_valid_bar(bar)
    )
    code_results[code] = {
      "complete": not missing_slots,
      "row_count": len(rows),
      "distinct_minutes": len(distinct_times),
      "valid_required_minutes": len(REQUIRED_MINUTE_SLOTS) - len(missing_slots),
      "missing_minutes": len(missing_slots),
      "invalid_required_rows": invalid_required_rows,
      "first_time": min(distinct_times).isoformat() if distinct_times else None,
      "last_time": max(distinct_times).isoformat() if distinct_times else None,
      "missing_sample": [
        f"{hour:02d}:{minute:02d}" for hour, minute in missing_slots[:8]
      ],
    }

  incomplete_codes = [
    code for code in requested_codes if not code_results[code]["complete"]
  ]
  return {
    "target_date": target_date.isoformat(),
    "complete": not incomplete_codes,
    "expected_minutes_per_code": len(REQUIRED_MINUTE_SLOTS),
    "incomplete_codes": incomplete_codes,
    "codes": code_results,
  }


def audit_core_index_intraday(target_date: date) -> dict[str, Any]:
  start = datetime.combine(target_date, time.min)
  end = datetime.combine(target_date, time.max)
  records = KLineRepository().find_by_period_and_time_range(
    period="1m",
    start=start,
    end=end,
    stock_codes=list(CORE_INDEX_SYMBOLS),
    use_cache=False,
  )
  return assess_intraday_coverage(records, target_date=target_date)


async def resolve_repair_dates(
  *,
  target_date: str = "",
  lookback_trading_days: int = DEFAULT_LOOKBACK_TRADING_DAYS,
  reference: Optional[datetime] = None,
  trading_dates: Optional[TradingDateHelper] = None,
) -> list[date]:
  helper = trading_dates or TradingDateHelper()
  if not 1 <= lookback_trading_days <= MAX_LOOKBACK_TRADING_DAYS:
    raise ValueError(f"回补检查交易日数必须在 1 到 {MAX_LOOKBACK_TRADING_DAYS} 之间")

  target_text = str(target_date or "").strip()
  if target_text:
    explicit = _parse_date(target_text)
    if not await helper.is_trading_date("SH", explicit):
      raise ValueError(f"指定日期不是交易日: {explicit.isoformat()}")
    return [explicit]

  target_reference = reference or _scheduled_start_time() or time_utils.now()
  latest = await expected_snapshot_date(
    target_reference,
    trading_dates=helper,
    cutoff=REPAIR_CUTOFF,
  )
  resolved = [latest]
  while len(resolved) < lookback_trading_days:
    resolved.append(
      await helper.trading_time_service.get_previous_trading_day("SH", resolved[-1])
    )
  return list(reversed(resolved))


@flow(
  name="核心指数分钟行情自愈",
  description="检查行情工作台核心指数 1 分钟 K 线，缺失时经 QMT Agent 自动回补",
  retries=1,
  retry_delay_seconds=120,
)
async def core_index_intraday_repair_flow(
  target_date: str = "",
  lookback_trading_days: int = DEFAULT_LOOKBACK_TRADING_DAYS,
  agent_device_id: str = "",
) -> dict[str, Any]:
  logger = get_run_logger()
  scheduled = _scheduled_start_time()
  reference = scheduled or time_utils.now()
  repair_dates = await resolve_repair_dates(
    target_date=target_date,
    lookback_trading_days=lookback_trading_days,
    reference=reference,
  )
  attempt_scope = _repair_attempt_scope(scheduled)
  results: list[dict[str, Any]] = []
  failed_dates: list[str] = []

  for repair_date in repair_dates:
    before = await asyncio.to_thread(audit_core_index_intraday, repair_date)
    incomplete_codes = list(before["incomplete_codes"])
    if not incomplete_codes:
      logger.info("核心指数分钟行情完整: date=%s", repair_date.isoformat())
      results.append(
        {
          "target_date": repair_date.isoformat(),
          "status": "complete",
          "requested_codes": [],
          "before": before,
          "after": before,
          "transfer": None,
        }
      )
      continue

    logger.warning(
      "核心指数分钟行情缺失，开始回补: date=%s codes=%s",
      repair_date.isoformat(),
      incomplete_codes,
    )
    payload = {
      "operation": "bars",
      "download": True,
      "stock_list": incomplete_codes,
      "periods": ["1m"],
      "start_time": repair_date.strftime("%Y%m%d"),
      "end_time": repair_date.strftime("%Y%m%d"),
    }
    try:
      transfer = await _request_and_wait(
        payload,
        agent_device_id=str(agent_device_id or "").strip(),
        idempotency_scope=(
          f"core-index-intraday-repair:v2:{repair_date.isoformat()}:{attempt_scope}"
        ),
      )
      if transfer.get("status") != "completed":
        raise RuntimeError(
          "QMT Agent 回补请求未完成: "
          f"request_id={transfer.get('request_id')} "
          f"status={transfer.get('status')} "
          f"durable_status={transfer.get('durable_status', '')} "
          f"reason={transfer.get('reason', '')}"
        )
      after = await asyncio.to_thread(audit_core_index_intraday, repair_date)
      status = "repaired" if after["complete"] else "incomplete"
      results.append(
        {
          "target_date": repair_date.isoformat(),
          "status": status,
          "requested_codes": incomplete_codes,
          "before": before,
          "after": after,
          "transfer": transfer,
        }
      )
      if not after["complete"]:
        failed_dates.append(repair_date.isoformat())
        logger.error(
          "核心指数分钟行情回补后仍不完整: date=%s codes=%s",
          repair_date.isoformat(),
          after["incomplete_codes"],
        )
    except Exception as exc:
      failed_dates.append(repair_date.isoformat())
      results.append(
        {
          "target_date": repair_date.isoformat(),
          "status": "failed",
          "requested_codes": incomplete_codes,
          "before": before,
          "after": None,
          "transfer": None,
          "error": f"{exc.__class__.__name__}: {exc}",
        }
      )
      logger.exception("核心指数分钟行情回补失败: date=%s", repair_date.isoformat())

  if failed_dates:
    raise RuntimeError("核心指数分钟行情自愈未完成: " + ", ".join(failed_dates))
  return {
    "status": "success",
    "dates": results,
    "checked_at": time_utils.now().isoformat(),
  }
