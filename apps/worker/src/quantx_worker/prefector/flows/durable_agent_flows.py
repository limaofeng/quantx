"""Prefect flows that request Agent work through durable database messages."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import math
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from prefect import flow, get_run_logger
from quantx_infrastructure import DurableRuntimeStore
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.services.divid_factor_service import (
  DividFactorService,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService

from quantx_worker.prefector.tasks.market_data_tasks import save_market_data

logger = logging.getLogger(__name__)

_DIVID_FACTOR_FIELDS = (
  "time",
  "interest",
  "stockBonus",
  "stockGift",
  "allotNum",
  "allotPrice",
  "gugai",
  "dr",
)


async def _persisted_instrument_codes() -> list[str]:
  store = DurableRuntimeStore()
  try:
    return await store.instrument_codes()
  finally:
    await store.close()


async def _request_and_wait(
  payload: dict[str, Any],
  *,
  timeout_seconds: int = 900,
  agent_device_id: str = "",
) -> dict[str, Any]:
  store = DurableRuntimeStore()
  try:
    request_id = (
      await store.create_market_data_request(
        payload,
        device_id=agent_device_id,
      )
      if agent_device_id
      else await store.create_market_data_request(payload)
    )
    logger.info(
      "Created market-data request request_id=%s operation=%s codes=%s "
      "periods=%s range=%s..%s",
      request_id,
      payload.get("operation"),
      len(payload.get("stock_list") or []),
      payload.get("periods") or [],
      payload.get("start_time") or "",
      payload.get("end_time") or "",
    )
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
      status = await store.market_data_request_status(request_id)
      if status in {
        "UPLOADED",
        "PROCESSING",
      } and await store.claim_market_data_request(request_id):
        try:
          ingestion = await _ingest_uploaded_request(store, request_id)
          await store.finish_market_data_request(
            request_id,
            status="COMPLETED",
          )
          return {
            "status": "completed",
            "request_id": request_id,
            **ingestion,
          }
        except Exception as exc:
          await store.finish_market_data_request(
            request_id,
            status="FAILED",
            error=f"{exc.__class__.__name__}: {exc}",
          )
          raise
      if status in {"COMPLETED", "FAILED", "MISSING"}:
        return {"status": status.lower(), "request_id": request_id}
      await asyncio.sleep(2)
    await store.finish_market_data_request(
      request_id,
      status="FAILED",
      error="Agent transfer did not complete before the flow timeout",
    )
    return {
      "status": "timeout",
      "request_id": request_id,
      "reason": "Agent transfer did not complete before the flow timeout",
    }
  finally:
    await store.close()


async def _ingest_uploaded_request(
  store: DurableRuntimeStore,
  request_id: str,
) -> dict[str, Any]:
  request = await store.market_data_request(request_id)
  if request is None:
    raise RuntimeError("Market-data request disappeared before ingestion")
  manifest = await store.market_data_transfers(request_id)
  expected = int(request.get("expected_chunks") or 0)
  if expected <= 0 or len(manifest) != expected:
    raise RuntimeError(
      f"Market-data transfer is incomplete: expected={expected} actual={len(manifest)}"
    )
  if [int(item["chunk_index"]) for item in manifest] != list(range(expected)):
    raise RuntimeError("Market-data transfer has missing or unordered chunks")

  records: list[dict[str, Any]] = []
  for item in manifest:
    path = Path(str(item["storage_reference"]))
    compressed = path.read_bytes()
    digest = hashlib.sha256(compressed).hexdigest()
    if digest != str(item["checksum_sha256"]):
      raise RuntimeError(f"Market-data chunk checksum mismatch: {path.name}")
    raw = gzip.decompress(compressed) if item.get("compressed") else compressed
    chunk = json.loads(raw.decode("utf-8"))
    if not isinstance(chunk, list):
      raise RuntimeError(f"Market-data chunk is not an array: {path.name}")
    if len(chunk) != int(item["record_count"]):
      raise RuntimeError(f"Market-data chunk record count mismatch: {path.name}")
    records.extend(item for item in chunk if isinstance(item, dict))

  payload = request.get("request_payload") or {}
  if isinstance(payload, str):
    payload = json.loads(payload)
  operation = str(payload.get("operation") or "bars")
  saved = 0
  replacement_audit: dict[str, Any] | None = None
  if operation == "bars" and records:
    periods = sorted({str(item.get("period") or "1d") for item in records})
    for period in periods:
      frames = {}
      codes = sorted(
        {
          str(item.get("code"))
          for item in records
          if str(item.get("period") or "1d") == period and item.get("code")
        }
      )
      for code in codes:
        rows = [
          {
            key: value
            for key, value in item.items()
            if key not in {"code", "period"}
          }
          for item in records
          if item.get("code") == code
          and str(item.get("period") or "1d") == period
        ]
        if rows:
          frames[code] = pd.DataFrame(rows)
      if frames:
        result = await save_market_data(period=period, market_data=frames)
        saved_count = int(result.get("saved_count", 0))
        if result.get("status") != "success":
          raise RuntimeError(f"{period} K 线写入未成功")
        expected_count = sum(len(frame) for frame in frames.values())
        if saved_count < expected_count:
          raise RuntimeError(
            f"{period} K 线写入不完整: expected={expected_count} saved={saved_count}"
          )
        saved += saved_count
  elif operation == "divid_factors":
    frames, stock_codes, start_ex_date, end_ex_date = (
      _normalize_divid_factor_records(records, payload)
    )
    replacement_audit = (
      await DividFactorService().replace_batch_divid_factors(
        frames,
        stock_codes=stock_codes,
        start_ex_date=start_ex_date,
        end_ex_date=end_ex_date,
      )
    )
    saved = int(replacement_audit["inserted_count"])

  result = {
    "operation": operation,
    "records_received": len(records),
    "records_saved": saved,
  }
  if replacement_audit is not None:
    result["replacement_audit"] = replacement_audit
  return result


async def reprocess_uploaded_market_data_request(
  request_id: str,
) -> dict[str, Any]:
  """Claim and re-ingest one explicitly reopened uploaded request.

  Reopening a terminal request is intentionally kept outside this helper.
  Callers must first prove the failed transfer is complete and invoke
  ``DurableRuntimeStore.reopen_failed_market_data_request``.  This function
  only accepts the resulting ``UPLOADED`` state (or its stale interrupted
  ``PROCESSING`` claim) and always reconverges a claimed request to a
  terminal state.
  """
  normalized_request_id = str(request_id or "").strip()
  if not normalized_request_id:
    raise ValueError("market-data request_id is required for reprocessing")

  store = DurableRuntimeStore()
  try:
    status = await store.market_data_request_status(normalized_request_id)
    if status not in {"UPLOADED", "PROCESSING"}:
      raise RuntimeError(
        "market-data request is not explicitly reopened for reprocessing: "
        f"request_id={normalized_request_id} status={status}"
      )
    if not await store.claim_market_data_request(normalized_request_id):
      raise RuntimeError(
        "market-data request could not be claimed for reprocessing: "
        f"request_id={normalized_request_id}"
      )
    try:
      ingestion = await _ingest_uploaded_request(
        store,
        normalized_request_id,
      )
      await store.finish_market_data_request(
        normalized_request_id,
        status="COMPLETED",
      )
    except Exception as exc:
      await store.finish_market_data_request(
        normalized_request_id,
        status="FAILED",
        error=f"{exc.__class__.__name__}: {exc}",
      )
      raise
    return {
      "status": "completed",
      "request_id": normalized_request_id,
      **ingestion,
    }
  finally:
    await store.close()


def _normalize_divid_factor_records(
  records: list[dict[str, Any]],
  payload: dict[str, Any],
) -> tuple[dict[str, pd.DataFrame], list[str], str, str]:
  """Validate the Agent transfer before replacing PostgreSQL state."""
  stock_codes = sorted(
    {
      str(code).strip().upper()
      for code in payload.get("stock_list") or []
      if str(code).strip()
    }
  )
  if not stock_codes:
    raise RuntimeError("divid_factors request has no stock_list")
  start_ex_date = str(payload.get("start_time") or "")
  end_ex_date = str(payload.get("end_time") or "")
  for label, value in (
    ("start_time", start_ex_date),
    ("end_time", end_ex_date),
  ):
    if len(value) != 8 or not value.isdigit():
      raise RuntimeError(f"divid_factors {label} must be YYYYMMDD")
  if end_ex_date < start_ex_date:
    raise RuntimeError("divid_factors end_time precedes start_time")

  requested = set(stock_codes)
  keys: set[tuple[str, str]] = set()
  rows_by_code: dict[str, list[dict[str, Any]]] = {}
  for record in records:
    code = str(record.get("code") or "").strip().upper()
    ex_date = str(record.get("ex_date") or "").strip()
    if code not in requested:
      raise RuntimeError(f"unexpected divid_factors code: {code}")
    if (
      len(ex_date) != 8
      or not ex_date.isdigit()
      or ex_date < start_ex_date
      or ex_date > end_ex_date
    ):
      raise RuntimeError(
        f"divid_factors ex_date is outside request range: {code}/{ex_date}"
      )
    key = (code, ex_date)
    if key in keys:
      raise RuntimeError(f"duplicate divid_factors key: {code}/{ex_date}")
    keys.add(key)

    normalized: dict[str, Any] = {"ex_date": ex_date}
    for field in _DIVID_FACTOR_FIELDS:
      try:
        numeric = float(record[field])
      except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
          f"invalid divid_factors {field}: {code}/{ex_date}"
        ) from exc
      if not math.isfinite(numeric):
        raise RuntimeError(
          f"non-finite divid_factors {field}: {code}/{ex_date}"
        )
      normalized[field] = numeric
    if normalized["time"] <= 0 or normalized["dr"] <= 0:
      raise RuntimeError(
        f"non-positive divid_factors time/dr: {code}/{ex_date}"
      )
    factor_date = (
      pd.to_datetime(normalized["time"], unit="ms", utc=True)
      .tz_convert("Asia/Shanghai")
      .strftime("%Y%m%d")
    )
    if factor_date != ex_date:
      raise RuntimeError(
        "divid_factors time/ex_date mismatch: "
        f"{code}/{ex_date}/{factor_date}"
      )
    rows_by_code.setdefault(code, []).append(normalized)

  frames = {
    code: pd.DataFrame(rows).set_index("ex_date")
    for code, rows in rows_by_code.items()
  }
  return frames, stock_codes, start_ex_date, end_ex_date


@flow(name="QMT Agent 市场标的请求", retries=1, retry_delay_seconds=30)
async def market_universe_request_flow(
  sectors: Optional[list[str]] = None,
) -> dict[str, Any]:
  return await _request_and_wait(
    {
      "operation": "sector_instruments",
      "sectors": sectors or ["沪深A股", "沪深ETF", "沪深指数"],
    }
  )


@flow(name="QMT Agent 标的信息请求", retries=1, retry_delay_seconds=30)
async def instrument_request_flow(
  stock_code: str = "",
) -> dict[str, Any]:
  if not stock_code:
    return {"status": "skipped", "reason": "stock_code is required"}
  return await _request_and_wait(
    {"operation": "instrument_details", "stock_list": [stock_code]}
  )


@flow(name="QMT Agent 财务数据请求", retries=1, retry_delay_seconds=30)
async def financial_request_flow(
  stock_codes: Optional[list[str]] = None,
) -> dict[str, Any]:
  if not stock_codes:
    stock_codes = await _persisted_instrument_codes()
  if not stock_codes:
    return {"status": "skipped", "reason": "persisted universe is empty"}
  return await _request_and_wait(
    {"operation": "financial_data", "stock_list": stock_codes}
  )


@flow(name="QMT Agent K线请求", retries=1, retry_delay_seconds=30)
async def daily_market_data_request_flow(
  stock_list: Optional[list[str]] = None,
  periods: Optional[list[str]] = None,
  sectors: Optional[list[str]] = None,
  start_time: str = "",
  end_time: str = "",
  batch_size: int = 300,
  retain_days: int = 30,
) -> dict[str, Any]:
  del batch_size, retain_days
  if not stock_list:
    stock_list = await _persisted_instrument_codes()
  if not stock_list:
    return {"status": "skipped", "reason": "persisted universe is empty"}
  now = datetime.now()
  return await _request_and_wait(
    {
      "operation": "bars",
      "stock_list": stock_list,
      "periods": periods or ["1d"],
      "start_time": start_time or (now - timedelta(days=2)).strftime("%Y%m%d"),
      "end_time": end_time or now.strftime("%Y%m%d"),
    }
  )


@flow(name="Agent 状态收敛检查")
async def agent_convergence_flow(
  sync_market_data: bool = False,
) -> dict[str, Any]:
  del sync_market_data
  store = DurableRuntimeStore()
  try:
    agents = await store.component_status("qmt-agent:")
    ready = [item for item in agents if item.get("status") == "READY"]
    return {
      "status": "ready" if ready else "degraded",
      "ready_agents": len(ready),
      "agents": agents,
    }
  finally:
    await store.close()


@flow(name="国债逆回购 TradeCommand 安全门")
async def bond_repo_trade_command_flow(
  account_id: str = "",
  instrument_code: str = "",
  annualized_rate: Optional[float] = None,
  volume: int = 0,
  idempotency_key: str = "",
) -> dict[str, Any]:
  if (
    not str(account_id or "").strip()
    or not str(instrument_code or "").strip()
    or annualized_rate is None
    or int(volume or 0) <= 0
    or not str(idempotency_key or "").strip()
  ):
    get_run_logger().warning(
      "自动逆回购必须提供账户、品种、利率、数量和业务幂等键；Worker 不直连 QMT"
    )
    return {
      "status": "skipped",
      "reason": "No explicit account-scoped TradeCommand policy was supplied",
    }

  rate = Decimal(str(annualized_rate))
  if rate <= 0:
    return {"status": "skipped", "reason": "annualized_rate must be positive"}
  async with AsyncSessionLocal() as db:
    queued = await TradeCommandService(db).enqueue_order_for_account(
      account_id=str(account_id).strip(),
      instrument_code=str(instrument_code).strip().upper(),
      side="SELL",
      order_type="LIMIT",
      limit_price=rate,
      volume=int(volume),
      strategy_name="bond-repo-worker",
      order_remark="QuantX国债逆回购",
      trace_id=str(idempotency_key).strip(),
      idempotency_key=str(idempotency_key).strip(),
    )
  get_run_logger().warning(
    "国债逆回购已持久化为 TradeCommand，等待 QMT Agent 投递"
  )
  return {
    "status": str(queued.status).lower(),
    "client_order_id": queued.client_order_id,
    "message_id": queued.message_id,
  }
