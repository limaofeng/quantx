"""Run a resumable, strictly serial QMT daily-bar history backfill.

This is an operational orchestrator around the existing
``daily-market-data-sync`` Prefect deployment.  It deliberately keeps each
QMT request small because the Agent and Worker materialize one request in
memory before and after upload.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from quantx_infrastructure import DurableRuntimeStore
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.database.relational_connection import (
  engine as relational_engine,
)
from quantx_infrastructure.models.agent_runtime import (
  MarketDataRequest,
  MarketDataTransfer,
)
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.repositories.kline_repository import KLineRepository
from quantx_worker.prefector.flows.durable_agent_flows import (
  reprocess_uploaded_market_data_request,
)
from sqlalchemy import func, select, text

SCHEMA_VERSION = 3
VERIFICATION_VERSION = 2
DEFAULT_DEPLOYMENT_NAME = "daily-market-data-sync"
DEFAULT_PREFECT_API_URL = "http://192.168.5.6:30420/api"
EXPECTED_FLOW_NAME = "每日市场数据同步"
EXPECTED_ENTRYPOINT = (
  "apps/worker/src/quantx_worker/prefector/flows/"
  "daily_market_data_sync_flow.py:daily_market_data_sync_flow"
)
EXPECTED_WORK_POOL_NAME = "quantx-pool"
EXPECTED_PARAMETER_NAMES = {
  "sectors",
  "stock_list",
  "start_time",
  "end_time",
  "periods",
  "skip_download",
  "compute_daily_signals",
  "agent_device_id",
}
BENCHMARK_CODE = "000300.SH"
TERMINAL_FLOW_STATES = {
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "CRASHED",
  "TIMEDOUT",
}
ACTIVE_REQUEST_STATES = {
  "QUEUED",
  "DELIVERED",
  "RECEIVING",
  "UPLOADED",
  "PROCESSING",
}
DATA_ONLY_READY_STATUSES = {"READY", "RECONCILING"}
CAMPAIGN_LOCK_KEY = int.from_bytes(
  hashlib.sha256(b"quantx:qmt-daily-history-backfill").digest()[:8],
  byteorder="big",
  signed=True,
)


class CampaignDatabaseLock:
  """Hold one PostgreSQL session advisory lock for the campaign lifetime."""

  def __init__(self) -> None:
    self.connection = None

  async def acquire(self) -> None:
    self.connection = await relational_engine.connect()
    acquired = await self.connection.scalar(
      text("SELECT pg_try_advisory_lock(:lock_key)"),
      {"lock_key": CAMPAIGN_LOCK_KEY},
    )
    if not acquired:
      await self.connection.close()
      self.connection = None
      raise RuntimeError("另一个 QMT 历史回填运行器已持有数据库锁")

  async def release(self) -> None:
    if self.connection is None:
      return
    try:
      await self.connection.scalar(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": CAMPAIGN_LOCK_KEY},
      )
    finally:
      await self.connection.close()
      self.connection = None


def _now_iso() -> str:
  return datetime.now().astimezone().isoformat()


def _date(value: str) -> date:
  compact = str(value or "").strip().replace("-", "")
  if len(compact) != 8 or not compact.isdigit():
    raise argparse.ArgumentTypeError("日期必须是 YYYYMMDD 或 YYYY-MM-DD")
  return datetime.strptime(compact, "%Y%m%d").date()


def _compact(value: date) -> str:
  return value.strftime("%Y%m%d")


def annual_windows(start: date, end: date) -> list[tuple[date, date]]:
  """Split an inclusive date range at calendar-year boundaries."""
  if end < start:
    raise ValueError("结束日期不能早于开始日期")
  windows: list[tuple[date, date]] = []
  cursor = start
  while cursor <= end:
    window_end = min(end, date(cursor.year, 12, 31))
    windows.append((cursor, window_end))
    cursor = window_end + timedelta(days=1)
  return windows


def calendar_month_windows(
  start: date,
  end: date,
) -> list[tuple[date, date]]:
  """Split an inclusive range into non-overlapping calendar-month windows.

  InfluxDB 3 Core limits how many Parquet files one query may scan.  A
  year-wide verification query can exceed that limit even though the QMT
  transfer itself is valid, so acceptance queries deliberately use much
  narrower natural date windows.
  """
  if end < start:
    raise ValueError("结束日期不能早于开始日期")
  windows: list[tuple[date, date]] = []
  cursor = start
  while cursor <= end:
    if cursor.month == 12:
      next_month = date(cursor.year + 1, 1, 1)
    else:
      next_month = date(cursor.year, cursor.month + 1, 1)
    window_end = min(end, next_month - timedelta(days=1))
    windows.append((cursor, window_end))
    cursor = window_end + timedelta(days=1)
  return windows


def _code_hash(codes: list[str]) -> str:
  payload = "\n".join(codes).encode("utf-8")
  return hashlib.sha256(payload).hexdigest()


def _job_id(start: date, end: date, codes: list[str], kind: str) -> str:
  digest = _code_hash(codes)[:12]
  return f"{kind}-{_compact(start)}-{_compact(end)}-{digest}"


def build_jobs(
  *,
  codes: list[str] | None = None,
  instruments: list[dict[str, Any]] | None = None,
  start: date,
  end: date,
  batch_size: int,
  benchmark_code: str = BENCHMARK_CODE,
) -> list[dict[str, Any]]:
  """Build deterministic stock batches plus one benchmark batch per year."""
  if batch_size <= 0:
    raise ValueError("batch_size 必须大于 0")
  if instruments is None:
    instruments = [{"code": code} for code in (codes or [])]
  normalized_instruments = sorted(
    instruments,
    key=lambda item: str(item["code"]).strip().upper(),
  )
  jobs: list[dict[str, Any]] = []
  for window_start, window_end in annual_windows(start, end):
    window_codes = []
    for item in normalized_instruments:
      code = str(item["code"]).strip().upper()
      open_date = (
        date.fromisoformat(str(item["open_date"]))
        if item.get("open_date")
        else None
      )
      expire_date = (
        date.fromisoformat(str(item["expire_date"]))
        if item.get("expire_date")
        else None
      )
      if open_date is not None and open_date > window_end:
        continue
      if expire_date is not None and expire_date < window_start:
        continue
      window_codes.append(code)
    window_codes = sorted(dict.fromkeys(window_codes))
    for offset in range(0, len(window_codes), batch_size):
      batch = window_codes[offset : offset + batch_size]
      jobs.append(
        {
          "id": _job_id(window_start, window_end, batch, "stocks"),
          "kind": "stocks",
          "codes": batch,
          "start_date": _compact(window_start),
          "end_date": _compact(window_end),
          "status": "pending",
          "attempt": 0,
          "children": [],
        }
      )
    jobs.append(
      {
        "id": _job_id(
          window_start,
          window_end,
          [benchmark_code],
          "benchmark",
        ),
        "kind": "benchmark",
        "codes": [benchmark_code],
        "start_date": _compact(window_start),
        "end_date": _compact(window_end),
        "status": "pending",
        "attempt": 0,
        "children": [],
      }
    )
  return jobs


def split_job(job: dict[str, Any]) -> list[dict[str, Any]]:
  """Split a failed job without replaying its exact idempotency payload."""
  codes = list(job["codes"])
  start = _date(str(job["start_date"]))
  end = _date(str(job["end_date"]))
  if len(codes) > 1:
    midpoint = len(codes) // 2
    code_groups = [codes[:midpoint], codes[midpoint:]]
    windows = [(start, end), (start, end)]
  elif start < end:
    midpoint = start + timedelta(days=(end - start).days // 2)
    code_groups = [codes, codes]
    windows = [(start, midpoint), (midpoint + timedelta(days=1), end)]
  else:
    return []

  children: list[dict[str, Any]] = []
  for index, (child_codes, window) in enumerate(zip(code_groups, windows)):
    child_start, child_end = window
    kind = f"{job['kind']}-retry"
    children.append(
      {
        "id": _job_id(child_start, child_end, child_codes, kind),
        "kind": kind,
        "codes": child_codes,
        "start_date": _compact(child_start),
        "end_date": _compact(child_end),
        "status": "pending",
        "attempt": int(job.get("attempt") or 0) + 1,
        "parent_id": job["id"],
        "split_index": index,
        "children": [],
      }
    )
  return children


def request_payload(job: dict[str, Any]) -> dict[str, Any]:
  return {
    "operation": "bars",
    "download": True,
    "stock_list": sorted(job["codes"]),
    "periods": ["1d"],
    "start_time": str(job["start_date"]),
    "end_time": str(job["end_date"]),
  }


def request_idempotency_key(payload: dict[str, Any]) -> str:
  encoded = json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    default=str,
  )
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def flow_parameters(
  job: dict[str, Any],
  *,
  agent_device_id: str,
) -> dict[str, Any]:
  """Build the exact Prefect parameters used for submission and recovery."""
  return {
    "stock_list": sorted(job["codes"]),
    "sectors": [],
    "start_time": str(job["start_date"]),
    "end_time": str(job["end_date"]),
    "periods": ["1d"],
    "skip_download": False,
    "compute_daily_signals": False,
    "agent_device_id": agent_device_id,
  }


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
  temporary.write_text(
    json.dumps(value, ensure_ascii=False, indent=2),
    encoding="utf-8",
  )
  os.replace(temporary, path)


async def load_universe(
  *,
  code_limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
  async with AsyncSessionLocal() as db:
    rows = (
      await db.execute(
        select(
          Instrument.id,
          Instrument.name,
          Instrument.open_date,
          Instrument.expire_date,
          Instrument.is_trading,
        )
        .where(Instrument.type == InstrumentType.STOCK)
        .order_by(Instrument.id.asc())
      )
    ).all()
    benchmark = (
      await db.execute(
        select(Instrument.id, Instrument.type).where(
          Instrument.id == BENCHMARK_CODE
        )
      )
    ).one_or_none()
  if benchmark is None or benchmark.type != InstrumentType.INDEX:
    raise RuntimeError(f"PostgreSQL 缺少指数标的 {BENCHMARK_CODE}")

  instruments = [
    {
      "code": code,
      "name": name or "",
      "open_date": open_date.isoformat() if open_date else None,
      "expire_date": expire_date.isoformat() if expire_date else None,
      "is_trading": bool(is_trading),
    }
    for code, name, open_date, expire_date, is_trading in rows
  ]
  unsupported = [
    item["code"]
    for item in instruments
    if not (
      str(item["code"]).endswith(".SH")
      or str(item["code"]).endswith(".SZ")
    )
  ]
  if unsupported:
    raise RuntimeError(
      "当前 Influx K 线仓储只支持沪深代码，证券主表包含未支持后缀: "
      + ", ".join(unsupported[:10])
    )
  if code_limit is not None:
    instruments = instruments[:code_limit]
  if not instruments:
    raise RuntimeError("PostgreSQL 中没有股票标的")
  codes = [item["code"] for item in instruments]
  metadata = {
    "stock_count": len(codes),
    "benchmark_code": BENCHMARK_CODE,
    "code_sha256": _code_hash(codes),
    "instruments": instruments,
  }
  return instruments, metadata


async def ensure_data_only_agent_ready(max_age_seconds: int = 90) -> str:
  store = DurableRuntimeStore()
  try:
    statuses = await store.component_status("qmt-agent:")
  finally:
    await store.close()
  now = datetime.now().astimezone()
  ready: list[tuple[datetime, dict[str, Any]]] = []
  for item in statuses:
    if item.get("status") not in DATA_ONLY_READY_STATUSES:
      continue
    details = item.get("details") or {}
    capabilities = set(details.get("capabilities") or [])
    updated_at = item.get("updated_at")
    if not isinstance(updated_at, datetime):
      continue
    if updated_at.tzinfo is None:
      updated_at = updated_at.replace(tzinfo=timezone.utc)
    age = abs((now - updated_at.astimezone(now.tzinfo)).total_seconds())
    if (
      age <= max_age_seconds
      and "market-data" in capabilities
      and "data-only" in capabilities
    ):
      ready.append((updated_at, item))
  if not ready:
    raise RuntimeError(
      "没有新鲜且处于 data-only/market-data READY 或 RECONCILING 的 QMT Agent"
    )
  _, selected = max(ready, key=lambda value: value[0])
  device_id = str(selected.get("instance_id") or "").strip()
  if not device_id:
    raise RuntimeError("可用 QMT Agent 缺少 device_id")
  return device_id


async def active_market_data_requests() -> list[dict[str, Any]]:
  async with AsyncSessionLocal() as db:
    rows = (
      await db.execute(
        select(
          MarketDataRequest.request_id,
          MarketDataRequest.status,
          MarketDataRequest.updated_at,
          MarketDataRequest.device_id,
          MarketDataRequest.idempotency_key,
        ).where(MarketDataRequest.status.in_(ACTIVE_REQUEST_STATES))
      )
    ).all()
    return [
      {
        "request_id": str(request_id),
        "status": str(status),
        "updated_at": updated_at.isoformat() if updated_at else None,
        "device_id": str(device_id),
        "idempotency_key": str(idempotency_key),
      }
      for request_id, status, updated_at, device_id, idempotency_key in rows
    ]


async def request_audit(payload: dict[str, Any]) -> dict[str, Any]:
  key = request_idempotency_key(payload)
  async with AsyncSessionLocal() as db:
    request = (
      await db.execute(
        select(MarketDataRequest).where(
          MarketDataRequest.idempotency_key == key
        )
      )
    ).scalar_one_or_none()
    if request is None:
      return {"ok": False, "reason": "market-data request not found"}
    actual_chunks, records = (
      await db.execute(
        select(
          func.count(MarketDataTransfer.transfer_id),
          func.coalesce(func.sum(MarketDataTransfer.record_count), 0),
        ).where(MarketDataTransfer.request_id == request.request_id)
      )
    ).one()
  expected_chunks = int(request.expected_chunks or 0)
  received_chunks = int(request.received_chunks or 0)
  actual_chunks = int(actual_chunks or 0)
  records = int(records or 0)
  ok = (
    request.status == "COMPLETED"
    and expected_chunks > 0
    and expected_chunks == received_chunks == actual_chunks
    and records > 0
  )
  return {
    "ok": ok,
    "request_id": request.request_id,
    "status": request.status,
    "expected_chunks": expected_chunks,
    "received_chunks": received_chunks,
    "actual_chunks": actual_chunks,
    "records": records,
    "processing_error": request.processing_error,
  }


async def market_data_request_details(
  request_id: str,
) -> dict[str, Any] | None:
  store = DurableRuntimeStore()
  try:
    return await store.market_data_request(request_id)
  finally:
    await store.close()


async def transfer_manifest(request_id: str) -> list[dict[str, Any]]:
  async with AsyncSessionLocal() as db:
    rows = (
      await db.execute(
        select(
          MarketDataTransfer.chunk_index,
          MarketDataTransfer.checksum_sha256,
          MarketDataTransfer.record_count,
          MarketDataTransfer.compressed,
          MarketDataTransfer.storage_reference,
        )
        .where(MarketDataTransfer.request_id == request_id)
        .order_by(MarketDataTransfer.chunk_index.asc())
      )
    ).all()
  return [
    {
      "chunk_index": int(chunk_index),
      "checksum_sha256": str(checksum_sha256),
      "record_count": int(record_count),
      "compressed": bool(compressed),
      "storage_reference": str(storage_reference),
    }
    for (
      chunk_index,
      checksum_sha256,
      record_count,
      compressed,
      storage_reference,
    ) in rows
  ]


def _key_digest(times: list[int]) -> str:
  encoded = "\n".join(str(value) for value in sorted(times))
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _timestamp_millis(value: Any) -> int:
  if isinstance(value, (int, float)):
    return int(value)
  if hasattr(value, "to_pydatetime"):
    value = value.to_pydatetime()
  if isinstance(value, datetime):
    normalized = (
      value.replace(tzinfo=timezone.utc)
      if value.tzinfo is None
      else value.astimezone(timezone.utc)
    )
    return int(normalized.timestamp() * 1000)
  parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
  return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def expected_daily_keys(
  job: dict[str, Any],
  manifest: list[dict[str, Any]],
) -> dict[str, Any]:
  """Re-read SHA-verified transfer chunks and summarize exact daily keys."""
  expected_indices = list(range(len(manifest)))
  actual_indices = [int(item["chunk_index"]) for item in manifest]
  if actual_indices != expected_indices:
    raise RuntimeError(
      "行情传输清单分片不连续: "
      f"expected={expected_indices} actual={actual_indices}"
    )

  requested_codes = {str(code).upper() for code in job["codes"]}
  times_by_code: dict[str, list[int]] = {
    code: [] for code in sorted(requested_codes)
  }
  record_count = 0
  for item in manifest:
    path = Path(str(item["storage_reference"])).resolve()
    compressed = path.read_bytes()
    digest = hashlib.sha256(compressed).hexdigest()
    if digest != str(item["checksum_sha256"]):
      raise RuntimeError(f"行情分片 SHA256 不匹配: {path.name}")
    raw = gzip.decompress(compressed) if item["compressed"] else compressed
    chunk = json.loads(raw.decode("utf-8"))
    if not isinstance(chunk, list):
      raise RuntimeError(f"行情分片不是数组: {path.name}")
    if len(chunk) != int(item["record_count"]):
      raise RuntimeError(f"行情分片记录数不匹配: {path.name}")
    for record in chunk:
      if not isinstance(record, dict):
        raise RuntimeError(f"行情分片包含非对象记录: {path.name}")
      code = str(record.get("code") or "").upper()
      period = str(record.get("period") or "1d")
      if code not in requested_codes:
        raise RuntimeError(f"行情分片包含请求外标的: {code or '<empty>'}")
      if period != "1d":
        raise RuntimeError(f"行情分片包含请求外周期: {period}")
      if record.get("time") is None:
        raise RuntimeError(f"行情分片记录缺少 time: {code}")
      times_by_code[code].append(_timestamp_millis(record["time"]))
      record_count += 1

  summaries: dict[str, dict[str, Any]] = {}
  duplicate_rows = 0
  for code, times in times_by_code.items():
    distinct_times = sorted(set(times))
    duplicate_rows += len(times) - len(distinct_times)
    summaries[code] = {
      "row_count": len(times),
      "distinct_times": len(distinct_times),
      "min_time_ms": distinct_times[0] if distinct_times else None,
      "max_time_ms": distinct_times[-1] if distinct_times else None,
      "key_sha256": _key_digest(distinct_times),
    }
  if duplicate_rows:
    raise RuntimeError(f"QMT 行情分片包含 {duplicate_rows} 个重复日线键")
  return {
    "record_count": record_count,
    "symbols": summaries,
  }


def verify_influx(
  job: dict[str, Any],
  expected: dict[str, Any],
) -> dict[str, Any]:
  timezone = ZoneInfo("Asia/Shanghai")
  query_windows = calendar_month_windows(
    _date(str(job["start_date"])),
    _date(str(job["end_date"])),
  )
  repository = KLineRepository()
  summaries: dict[str, dict[str, Any]] = {}
  keys: dict[str, list[Any]] = {}
  for window_start, window_end in query_windows:
    start = datetime.combine(
      window_start,
      datetime_time.min,
      tzinfo=timezone,
    )
    end = datetime.combine(
      window_end,
      datetime_time.max,
      tzinfo=timezone,
    )
    window_summaries = repository.summarize_daily_batch(
      list(job["codes"]),
      start,
      end,
      use_cache=False,
    )
    window_keys = repository.find_daily_keys_batch(
      list(job["codes"]),
      start,
      end,
      use_cache=False,
    )
    for code, item in window_summaries.items():
      summary = summaries.setdefault(
        code,
        {
          "row_count": 0,
          "distinct_times": 0,
          "invalid_rows": 0,
        },
      )
      summary["row_count"] += int(item["row_count"])
      summary["distinct_times"] += int(item["distinct_times"])
      summary["invalid_rows"] += int(item["invalid_rows"])
    for code, times in window_keys.items():
      keys.setdefault(code, []).extend(times)
  rows = int(sum(item["row_count"] for item in summaries.values()))
  duplicate_rows = int(
    sum(
      item["row_count"] - item["distinct_times"]
      for item in summaries.values()
    )
  )
  invalid_rows = int(
    sum(item["invalid_rows"] for item in summaries.values())
  )
  requested_codes = set(job["codes"])
  returned_codes = set(summaries)
  expected_symbols = expected["symbols"]
  expected_nonempty_codes = {
    code
    for code, summary in expected_symbols.items()
    if int(summary["row_count"]) > 0
  }
  missing_codes = sorted(expected_nonempty_codes - returned_codes)
  unexpected_codes = sorted(returned_codes - requested_codes)
  mismatched_codes: list[str] = []
  actual_key_summaries: dict[str, dict[str, Any]] = {}
  for code in sorted(requested_codes):
    actual_times = [_timestamp_millis(value) for value in keys.get(code, [])]
    distinct_times = sorted(set(actual_times))
    actual = {
      "row_count": len(actual_times),
      "distinct_times": len(distinct_times),
      "min_time_ms": distinct_times[0] if distinct_times else None,
      "max_time_ms": distinct_times[-1] if distinct_times else None,
      "key_sha256": _key_digest(distinct_times),
    }
    actual_key_summaries[code] = actual
    if actual != expected_symbols[code]:
      mismatched_codes.append(code)
  expected_records = int(expected["record_count"])
  return {
    "ok": (
      rows == expected_records
      and duplicate_rows == 0
      and not missing_codes
      and not unexpected_codes
      and not mismatched_codes
    ),
    "verification_version": VERIFICATION_VERSION,
    "query_window": "calendar_month",
    "query_window_count": len(query_windows),
    "rows": rows,
    "expected_records": expected_records,
    "requested_symbol_count": len(requested_codes),
    "symbol_count": len(summaries),
    "missing_codes": missing_codes,
    "unexpected_codes": unexpected_codes,
    "mismatched_codes": mismatched_codes,
    "source_empty_codes": sorted(requested_codes - expected_nonempty_codes),
    "duplicate_rows": duplicate_rows,
    "invalid_rows": invalid_rows,
    "quality_warning": (
      f"{invalid_rows} 条原始行情含空值或异常 OHLCV，将由研究预检剔除"
      if invalid_rows
      else None
    ),
  }


class PrefectClient:
  def __init__(self, api_url: str, deployment_name: str):
    self.client = httpx.Client(base_url=api_url.rstrip("/"), timeout=30.0)
    self.deployment_name = deployment_name
    self.deployment = self._deployment()
    self.deployment_id = str(self.deployment["id"])
    self.deployment_identity = {
      "id": self.deployment_id,
      "flow_id": str(self.deployment["flow_id"]),
      "flow_name": EXPECTED_FLOW_NAME,
      "entrypoint": str(self.deployment["entrypoint"]),
      "work_pool_name": str(self.deployment["work_pool_name"]),
      "version": str(self.deployment.get("version") or ""),
    }

  def close(self) -> None:
    self.client.close()

  def _deployment(self) -> dict[str, Any]:
    path = (
      f"/deployments/name/{quote(EXPECTED_FLOW_NAME, safe='')}/"
      f"{quote(self.deployment_name, safe='')}"
    )
    response = self.client.get(path)
    response.raise_for_status()
    deployment = dict(response.json())
    flow_id = str(deployment.get("flow_id") or "")
    flow_response = self.client.get(f"/flows/{flow_id}")
    flow_response.raise_for_status()
    flow = dict(flow_response.json())
    properties = (
      (deployment.get("parameter_openapi_schema") or {}).get("properties")
      or {}
    )
    mismatches = []
    if str(deployment.get("name") or "") != self.deployment_name:
      mismatches.append("deployment name")
    if str(flow.get("name") or "") != EXPECTED_FLOW_NAME:
      mismatches.append("flow name")
    if str(deployment.get("entrypoint") or "") != EXPECTED_ENTRYPOINT:
      mismatches.append("entrypoint")
    if (
      str(deployment.get("work_pool_name") or "")
      != EXPECTED_WORK_POOL_NAME
    ):
      mismatches.append("work pool")
    if not EXPECTED_PARAMETER_NAMES.issubset(set(properties)):
      mismatches.append("parameter schema")
    if mismatches:
      raise RuntimeError(
        "Prefect 部署身份校验失败: " + ", ".join(mismatches)
      )
    return deployment

  def submit(
    self,
    job: dict[str, Any],
    *,
    agent_device_id: str,
    idempotency_key: str,
  ) -> str:
    parameters = flow_parameters(job, agent_device_id=agent_device_id)
    response = self.client.post(
      f"/deployments/{self.deployment_id}/create_flow_run",
      json={
        "name": f"quantx-backfill-{job['id'][-12:]}",
        "tags": [
          "quantx-backfill",
          idempotency_key.split(":", maxsplit=1)[0],
        ],
        "parameters": parameters,
        "idempotency_key": idempotency_key,
      },
    )
    response.raise_for_status()
    provisional_run_id = str(response.json()["id"])
    for attempt in range(3):
      confirmed = self.flow_run_by_idempotency_key(idempotency_key)
      if confirmed is not None:
        self._validate_confirmed_run(
          confirmed,
          idempotency_key=idempotency_key,
          parameters=parameters,
        )
        return str(confirmed["id"])
      if attempt < 2:
        time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(
      "Prefect 创建响应未能按幂等键回读确认: "
      f"provisional_run_id={provisional_run_id}"
    )

  def _validate_confirmed_run(
    self,
    flow_run: dict[str, Any],
    *,
    idempotency_key: str,
    parameters: dict[str, Any],
  ) -> None:
    if str(flow_run.get("deployment_id") or "") != self.deployment_id:
      raise RuntimeError("Prefect 幂等运行所属 deployment 不匹配")
    if str(flow_run.get("idempotency_key") or "") != idempotency_key:
      raise RuntimeError("Prefect 幂等运行的 idempotency_key 不匹配")
    actual_parameters = flow_run.get("parameters") or {}
    mismatched = [
      key
      for key, expected in parameters.items()
      if actual_parameters.get(key) != expected
    ]
    if mismatched:
      raise RuntimeError(
        "Prefect 幂等运行参数不匹配: " + ", ".join(mismatched)
      )

  def flow_run_by_idempotency_key(
    self,
    idempotency_key: str,
  ) -> dict[str, Any] | None:
    response = self.client.post(
      "/flow_runs/filter",
      json={
        "deployments": {
          "id": {
            "any_": [self.deployment_id],
          }
        },
        "flow_runs": {
          "idempotency_key": {
            "any_": [idempotency_key],
          }
        },
        "limit": 2,
      },
    )
    response.raise_for_status()
    matches = response.json()
    if not matches:
      return None
    if len(matches) != 1:
      raise RuntimeError(
        f"Prefect 幂等键 {idempotency_key!r} 匹配到 {len(matches)} 个运行"
      )
    return dict(matches[0])

  def flow_run(self, run_id: str) -> dict[str, Any]:
    response = self.client.get(f"/flow_runs/{run_id}")
    response.raise_for_status()
    result = dict(response.json())
    if str(result.get("id") or "") != run_id:
      raise RuntimeError(
        f"Prefect Flow 查询 ID 不匹配: requested={run_id}, "
        f"returned={result.get('id')}"
      )
    return result


def _initial_state(
  *,
  universe: dict[str, Any],
  jobs: list[dict[str, Any]],
  start: date,
  end: date,
  batch_size: int,
  code_limit: int | None,
  deployment_name: str,
  prefect_api_url: str,
) -> dict[str, Any]:
  run_key = hashlib.sha256(
    (
      f"{universe['code_sha256']}:{_compact(start)}:"
      f"{_compact(end)}:{batch_size}:{BENCHMARK_CODE}"
    ).encode("utf-8")
  ).hexdigest()[:24]
  return {
    "schema_version": SCHEMA_VERSION,
    "run_key": run_key,
    "created_at": _now_iso(),
    "updated_at": _now_iso(),
    "status": "pending",
    "start_date": _compact(start),
    "end_date": _compact(end),
    "batch_size": batch_size,
    "code_limit": code_limit,
    "deployment_name": deployment_name,
    "prefect_api_url": prefect_api_url.rstrip("/"),
    "scope": "PostgreSQL STOCK 类型中的 .SH/.SZ 沪深 A 股",
    "universe": universe,
    "jobs": jobs,
    "initial_job_count": len(jobs),
    "summary": {},
  }


def _refresh_summary(state: dict[str, Any]) -> None:
  counts: dict[str, int] = {}
  records = 0
  for job in state["jobs"]:
    status = str(job.get("status") or "unknown")
    counts[status] = counts.get(status, 0) + 1
    if status == "completed":
      records += int((job.get("request_audit") or {}).get("records") or 0)
  state["updated_at"] = _now_iso()
  state["summary"] = {
    "jobs": len(state["jobs"]),
    "status_counts": counts,
    "verified_records": records,
  }


def _next_job(state: dict[str, Any]) -> dict[str, Any] | None:
  for job in state["jobs"]:
    if job.get("status") in {
      "pending",
      "running",
      "verification_pending",
    }:
      return job
  return None


def _append_split_children(
  state: dict[str, Any],
  job: dict[str, Any],
  *,
  max_split_depth: int,
  max_total_jobs: int,
) -> bool:
  if job.get("children"):
    return True
  if int(job.get("attempt") or 0) >= max_split_depth:
    return False
  children = split_job(job)
  if not children:
    return False
  if len(state["jobs"]) + len(children) > max_total_jobs:
    return False
  existing_ids = {str(item["id"]) for item in state["jobs"]}
  job["children"] = [child["id"] for child in children]
  state["jobs"].extend(
    child for child in children if child["id"] not in existing_ids
  )
  return True


async def _load_or_create_state(
  args: argparse.Namespace,
) -> dict[str, Any]:
  state_path = Path(args.state_file).resolve()
  if state_path.exists():
    state = json.loads(state_path.read_text(encoding="utf-8"))
    schema_version = int(state.get("schema_version") or 0)
    if schema_version == 2:
      normalized_api_url = args.prefect_api_url.rstrip("/")
      if normalized_api_url != DEFAULT_PREFECT_API_URL:
        raise RuntimeError(
          "v2 回填账本只允许显式迁移到配置的 Prefect API"
        )
      nonterminal_jobs = [
        str(job.get("id"))
        for job in state.get("jobs", [])
        if job.get("status") not in {"completed", "superseded"}
      ]
      if nonterminal_jobs:
        raise RuntimeError(
          "v2 回填账本含未终结任务，无法排除提交后写盘前崩溃，"
          "也无法证明原 Prefect API；请先人工收敛: "
          + ", ".join(nonterminal_jobs[:5])
        )
      state["schema_version"] = SCHEMA_VERSION
      state["prefect_api_url"] = normalized_api_url
      state.setdefault("migrations", []).append(
        {
          "at": _now_iso(),
          "from": 2,
          "to": SCHEMA_VERSION,
          "reason": "锁定 Prefect API，避免 dotenv 污染运行目标",
        }
      )
      _refresh_summary(state)
      _atomic_write_json(state_path, state)
    elif schema_version != SCHEMA_VERSION:
      raise RuntimeError("回填账本版本不受支持")
    expected = {
      "start_date": _compact(args.start_date),
      "end_date": _compact(args.end_date),
      "batch_size": args.batch_size,
      "code_limit": args.code_limit,
      "deployment_name": args.deployment_name,
      "prefect_api_url": args.prefect_api_url.rstrip("/"),
    }
    actual = {key: state.get(key) for key in expected}
    if actual != expected:
      raise RuntimeError(
        f"回填参数与现有账本不一致: expected={expected} actual={actual}"
      )
    return state

  instruments, universe = await load_universe(code_limit=args.code_limit)
  jobs = build_jobs(
    instruments=instruments,
    start=args.start_date,
    end=args.end_date,
    batch_size=args.batch_size,
  )
  state = _initial_state(
    universe=universe,
    jobs=jobs,
    start=args.start_date,
    end=args.end_date,
    batch_size=args.batch_size,
    code_limit=args.code_limit,
    deployment_name=args.deployment_name,
    prefect_api_url=args.prefect_api_url,
  )
  _refresh_summary(state)
  _atomic_write_json(state_path, state)
  return state


async def _audit_and_verify_job(job: dict[str, Any]) -> bool:
  audit = await request_audit(request_payload(job))
  job["request_audit"] = audit
  if not audit.get("ok"):
    job["influx_verification"] = {
      "ok": False,
      "reason": "request audit failed",
    }
    return False
  manifest = await transfer_manifest(str(audit["request_id"]))
  expected = await asyncio.to_thread(expected_daily_keys, job, manifest)
  if int(expected["record_count"]) != int(audit["records"]):
    job["influx_verification"] = {
      "ok": False,
      "reason": "transfer manifest record count does not match request audit",
      "manifest_records": int(expected["record_count"]),
      "audit_records": int(audit["records"]),
    }
    return False
  verification = await asyncio.to_thread(
    verify_influx,
    job,
    expected,
  )
  job["influx_verification"] = verification
  return bool(verification.get("ok"))


def _decoded_request_payload(value: Any) -> dict[str, Any]:
  if isinstance(value, str):
    value = json.loads(value)
  if not isinstance(value, dict):
    raise RuntimeError("market-data request payload is not an object")
  return dict(value)


def _failed_ingestion_jobs(state: dict[str, Any]) -> list[dict[str, Any]]:
  return [
    job for job in state["jobs"] if job.get("status") == "flow_failed"
  ]


async def _verify_reprocessed_ingestion(
  state_path: Path,
  state: dict[str, Any],
  job: dict[str, Any],
  history_entry: dict[str, Any],
  *,
  request_id: str,
  source_records: int,
) -> bool:
  try:
    verified = await _audit_and_verify_job(job)
  except Exception as exc:
    verification_error = f"{exc.__class__.__name__}: {exc}"
  else:
    verification_error = (
      ""
      if verified
      else (
        "失败入库恢复后未通过 Influx exact-key verification v"
        f"{VERIFICATION_VERSION}"
      )
    )
  if verification_error:
    history_entry["failed_at"] = _now_iso()
    history_entry["status"] = "failed"
    history_entry["error"] = verification_error[:2000]
    job["status"] = "flow_failed"
    _persist_status(
      state_path,
      state,
      status="paused",
      reason=history_entry["error"],
    )
    return False

  job["status"] = "completed"
  job["finished_at"] = _now_iso()
  history_entry["completed_at"] = job["finished_at"]
  history_entry["status"] = "completed"
  _refresh_summary(state)
  _atomic_write_json(state_path, state)
  print(
    json.dumps(
      {
        "event": "failed_ingestion_reprocessed",
        "job_id": job["id"],
        "request_id": request_id,
        "records": source_records,
      }
    ),
    flush=True,
  )
  return True


async def _retry_failed_ingestion_job(
  state_path: Path,
  state: dict[str, Any],
  job: dict[str, Any],
) -> bool:
  """Re-ingest a fully proven failed transfer without requesting QMT again."""
  if job.get("status") != "flow_failed":
    raise RuntimeError(
      "--retry-failed-ingestion 只允许处理 flow_failed 任务"
    )

  payload = request_payload(job)
  if (
    payload.get("operation") != "bars"
    or payload.get("periods") != ["1d"]
  ):
    raise RuntimeError(
      "--retry-failed-ingestion 只允许恢复 bars/1d 请求"
    )
  audit = await request_audit(payload)
  request_id = str(audit.get("request_id") or "")
  request_status = str(audit.get("status") or "")
  if not request_id or request_status not in {
    "FAILED",
    "UPLOADED",
    "PROCESSING",
    "COMPLETED",
  }:
    raise RuntimeError(
      "失败入库恢复要求 exact payload 请求处于 FAILED，或处于"
      "本 controller 已记录的重入库恢复状态: "
      f"request_id={request_id or '<missing>'} "
      f"status={request_status or '<missing>'}"
    )
  expected_chunks = int(audit.get("expected_chunks") or 0)
  received_chunks = int(audit.get("received_chunks") or 0)
  actual_chunks = int(audit.get("actual_chunks") or 0)
  records = int(audit.get("records") or 0)
  if (
    expected_chunks <= 0
    or expected_chunks != received_chunks
    or expected_chunks != actual_chunks
    or records <= 0
  ):
    raise RuntimeError(
      "失败入库恢复拒绝不完整传输: "
      f"expected={expected_chunks} received={received_chunks} "
      f"actual={actual_chunks} records={records}"
    )

  details = await market_data_request_details(request_id)
  if details is None or str(details.get("status") or "") != request_status:
    raise RuntimeError(
      "失败入库恢复读取到不一致的请求状态: "
      f"request_id={request_id} status="
      f"{(details or {}).get('status') or '<missing>'}"
    )
  actual_payload = _decoded_request_payload(
    details.get("request_payload"),
  )
  if actual_payload != payload:
    raise RuntimeError(
      "失败入库恢复的 request payload 与任务 exact payload 不一致"
    )
  if str(actual_payload.get("operation") or "") != "bars":
    raise RuntimeError(
      "--retry-failed-ingestion 不得用于非 bars 请求"
    )

  manifest = await transfer_manifest(request_id)
  expected = await asyncio.to_thread(expected_daily_keys, job, manifest)
  source_records = int(expected.get("record_count") or 0)
  if source_records != records:
    raise RuntimeError(
      "失败入库恢复的 manifest 记录数与请求审计不一致: "
      f"manifest={source_records} audit={records}"
    )

  prior_history = list(job.get("ingestion_retry_history") or [])
  has_reopen_proof = any(
    str(item.get("request_id") or "") == request_id
    and (
      item.get("reprocessed_at")
      or (item.get("reprocess_result") or {}).get("status") == "completed"
      or (
        item.get("reopened_at")
        and (item.get("reopen_evidence") or {}).get("status")
        == "UPLOADED"
      )
    )
    for item in prior_history
  )
  if request_status != "FAILED" and not has_reopen_proof:
    raise RuntimeError(
      f"{request_status} 请求缺少本 controller 的既有重入库证明，"
      "拒绝借 --retry-failed-ingestion 改写任务结论"
    )

  history_entry: dict[str, Any] = {
    "attempt": len(prior_history) + 1,
    "mode": (
      "reprocess"
      if request_status == "FAILED"
      else (
        "verification_resume"
        if request_status == "COMPLETED"
        else "reprocess_resume"
      )
    ),
    "requested_at": _now_iso(),
    "request_id": request_id,
    "old_processing_error": str(
      details.get("processing_error")
      or audit.get("processing_error")
      or ""
    )[:2000],
    "payload_sha256": request_idempotency_key(payload),
    "expected_chunks": expected_chunks,
    "received_chunks": received_chunks,
    "manifest_chunks": actual_chunks,
    "source_records": source_records,
    "source_symbol_count": len(expected.get("symbols") or {}),
    "chunk_checksums": [
      {
        "chunk_index": int(item["chunk_index"]),
        "checksum_sha256": str(item["checksum_sha256"]),
        "record_count": int(item["record_count"]),
      }
      for item in manifest
    ],
    "status": "validated",
  }
  job.setdefault("ingestion_retry_history", []).append(history_entry)
  _refresh_summary(state)
  _atomic_write_json(state_path, state)

  if request_status == "COMPLETED":
    return await _verify_reprocessed_ingestion(
      state_path,
      state,
      job,
      history_entry,
      request_id=request_id,
      source_records=source_records,
    )

  try:
    if request_status == "FAILED":
      store = DurableRuntimeStore()
      try:
        reopen_evidence = (
          await store.reopen_failed_market_data_request(request_id)
        )
      finally:
        await store.close()
      history_entry["reopened_at"] = _now_iso()
      history_entry["reopen_evidence"] = reopen_evidence
      history_entry["status"] = "reopened"
      _refresh_summary(state)
      _atomic_write_json(state_path, state)

    reprocessed = await reprocess_uploaded_market_data_request(request_id)
    if (
      reprocessed.get("status") != "completed"
      or str(reprocessed.get("request_id") or "") != request_id
      or str(reprocessed.get("operation") or "") != "bars"
      or int(reprocessed.get("records_received") or 0) != source_records
      or int(reprocessed.get("records_saved") or 0) != source_records
    ):
      raise RuntimeError(
        "失败入库恢复未返回完整 bars 入库证明: "
        f"{reprocessed}"
      )
    history_entry["reprocessed_at"] = _now_iso()
    history_entry["reprocess_result"] = reprocessed
    history_entry["status"] = "reprocessed"
    _refresh_summary(state)
    _atomic_write_json(state_path, state)
  except Exception as exc:
    history_entry["failed_at"] = _now_iso()
    history_entry["status"] = "failed"
    history_entry["error"] = f"{exc.__class__.__name__}: {exc}"[:2000]
    job["status"] = "flow_failed"
    _persist_status(
      state_path,
      state,
      status="paused",
      reason=history_entry["error"],
    )
    return False

  return await _verify_reprocessed_ingestion(
    state_path,
    state,
    job,
    history_entry,
    request_id=request_id,
    source_records=source_records,
  )


def _queue_outdated_verifications(state: dict[str, Any]) -> int:
  queued = 0
  for job in state["jobs"]:
    verification = job.get("influx_verification") or {}
    if (
      job.get("status") == "completed"
      and int(verification.get("verification_version") or 0)
      < VERIFICATION_VERSION
    ):
      job["status"] = "verification_pending"
      job["verification_only"] = True
      job["reverification_reason"] = (
        f"upgrade to exact-key verification v{VERIFICATION_VERSION}"
      )
      queued += 1
  return queued


def _elapsed_seconds(value: str | None) -> float:
  if not value:
    return 0.0
  submitted = datetime.fromisoformat(value)
  if submitted.tzinfo is None:
    submitted = submitted.replace(tzinfo=timezone.utc)
  return (
    datetime.now(timezone.utc) - submitted.astimezone(timezone.utc)
  ).total_seconds()


def _persist_status(
  state_path: Path,
  state: dict[str, Any],
  *,
  status: str,
  reason: str = "",
) -> None:
  state["status"] = status
  if reason:
    state["last_error"] = reason
  elif status == "running":
    state.pop("last_error", None)
  _refresh_summary(state)
  _atomic_write_json(state_path, state)


async def run(args: argparse.Namespace) -> int:
  state_path = Path(args.state_file).resolve()
  campaign_lock = CampaignDatabaseLock()
  await campaign_lock.acquire()
  client: PrefectClient | None = None
  state: dict[str, Any] | None = None
  try:
    state = await _load_or_create_state(args)
    client = PrefectClient(args.prefect_api_url, args.deployment_name)
    recorded_deployment_id = str(state.get("deployment_id") or "")
    if (
      recorded_deployment_id
      and recorded_deployment_id != client.deployment_id
    ):
      raise RuntimeError(
        "回填账本的 Prefect deployment_id 与当前 API 不一致"
      )
    if not recorded_deployment_id:
      state["deployment_id"] = client.deployment_id
    recorded_identity = state.get("deployment_identity")
    if (
      recorded_identity is not None
      and recorded_identity != client.deployment_identity
    ):
      raise RuntimeError(
        "回填账本的 Prefect deployment identity 与当前 API 不一致"
      )
    if recorded_identity is None:
      state["deployment_identity"] = client.deployment_identity
    failed_ingestion_jobs = _failed_ingestion_jobs(state)
    verification_failed_jobs = [
      job
      for job in state["jobs"]
      if job.get("status") == "verification_failed"
    ]
    if verification_failed_jobs:
      _persist_status(
        state_path,
        state,
        status="paused",
        reason=(
          "--retry-failed-ingestion 不得用于 verification_failed；"
          "请先人工审计 exact-key 差异"
        ),
      )
      return 3
    if failed_ingestion_jobs and not args.retry_failed_ingestion:
      _persist_status(
        state_path,
        state,
        status="paused",
        reason=(
          "账本含 flow_failed 任务；必须显式使用 "
          "--retry-failed-ingestion 才能尝试恢复完整上传后的入库失败"
        ),
      )
      return 3
    if args.retry_failed_ingestion and not failed_ingestion_jobs:
      _persist_status(
        state_path,
        state,
        status="paused",
        reason=(
          "--retry-failed-ingestion 只允许用于账本中的 flow_failed 任务"
        ),
      )
      return 3

    recovered = 0
    for failed_job in failed_ingestion_jobs:
      try:
        recovered_ok = await _retry_failed_ingestion_job(
          state_path,
          state,
          failed_job,
        )
      except Exception as exc:
        failed_job["status"] = "flow_failed"
        _persist_status(
          state_path,
          state,
          status="paused",
          reason=f"{exc.__class__.__name__}: {exc}",
        )
        return 2
      if not recovered_ok:
        return 2
      recovered += 1

    requeued = _queue_outdated_verifications(state)
    if not recorded_deployment_id or recorded_identity is None or requeued:
      _refresh_summary(state)
      _atomic_write_json(state_path, state)
    processed = recovered
    queue_wait_started: float | None = None
    agent_wait_started: float | None = None
    _persist_status(state_path, state, status="running")
    print(
      json.dumps(
        {
          "event": "backfill_started",
          "run_key": state["run_key"],
          "state_file": str(state_path),
          "stock_count": state["universe"]["stock_count"],
          "jobs": len(state["jobs"]),
          "batch_size": state["batch_size"],
        },
        ensure_ascii=False,
      ),
      flush=True,
    )
    while True:
      job = _next_job(state)
      if job is None:
        unresolved = [
          item
          for item in state["jobs"]
          if item.get("status") not in {"completed", "superseded"}
        ]
        _persist_status(
          state_path,
          state,
          status="completed" if not unresolved else "failed",
        )
        return 0 if not unresolved else 2
      if args.max_jobs is not None and processed >= args.max_jobs:
        _persist_status(state_path, state, status="paused")
        return 0

      if job["status"] in {"pending", "verification_pending"}:
        verification_only = bool(job.get("verification_only"))
        payload = request_payload(job)
        existing = await request_audit(payload)
        if existing.get("request_id"):
          if existing.get("ok"):
            verified = await _audit_and_verify_job(job)
            job["finished_at"] = _now_iso()
            if verified:
              job["status"] = "completed"
              job.pop("verification_only", None)
              job.pop("reverification_reason", None)
              processed += 1
              _refresh_summary(state)
              _atomic_write_json(state_path, state)
              print(
                json.dumps(
                  {
                    "event": "job_reconciled",
                    "job_id": job["id"],
                    "request_id": existing["request_id"],
                    "records": existing["records"],
                  }
                ),
                flush=True,
              )
              continue
            job["status"] = "verification_failed"
            reason = f"既有请求 {existing['request_id']} 的 Influx 验收失败"
            _persist_status(
              state_path,
              state,
              status="paused",
              reason=reason,
            )
            return 2
          if verification_only:
            job["status"] = "verification_failed"
            reason = (
              f"只读重验要求既有请求处于 COMPLETED；"
              f"{existing['request_id']} 当前为 {existing.get('status')}"
            )
            _persist_status(
              state_path,
              state,
              status="paused",
              reason=reason,
            )
            return 3
          if existing.get("status") in ACTIVE_REQUEST_STATES:
            if queue_wait_started is None:
              queue_wait_started = time.monotonic()
            if (
              time.monotonic() - queue_wait_started
              > args.queue_wait_timeout_seconds
            ):
              reason = (
                f"既有请求 {existing['request_id']} 长时间未收敛: "
                f"{existing.get('status')}"
              )
              _persist_status(
                state_path,
                state,
                status="paused",
                reason=reason,
              )
              return 3
            print(
              json.dumps(
                {
                  "event": "waiting_for_existing_request",
                  "job_id": job["id"],
                  "request_id": existing["request_id"],
                  "status": existing.get("status"),
                }
              ),
              flush=True,
            )
            await asyncio.sleep(args.poll_seconds)
            continue
          reason = (
            f"既有请求 {existing['request_id']} 处于不可自动恢复的 "
            f"{existing.get('status')} 状态"
          )
          job["request_audit"] = existing
          _persist_status(
            state_path,
            state,
            status="paused",
            reason=reason,
          )
          return 3

        if verification_only:
          job["status"] = "verification_failed"
          _persist_status(
            state_path,
            state,
            status="paused",
            reason=(
              f"任务 {job['id']} 是只读验收升级，但原 PG 请求、"
              "manifest 或分片不可用；禁止重新提交 QMT"
            ),
          )
          return 3

        try:
          agent_device_id = await ensure_data_only_agent_ready()
        except RuntimeError as exc:
          if agent_wait_started is None:
            agent_wait_started = time.monotonic()
          if (
            time.monotonic() - agent_wait_started
            > args.agent_wait_timeout_seconds
          ):
            _persist_status(
              state_path,
              state,
              status="paused",
              reason=str(exc),
            )
            return 3
          print(
            json.dumps(
              {
                "event": "waiting_for_data_only_agent",
                "reason": str(exc),
              },
              ensure_ascii=False,
            ),
            flush=True,
          )
          await asyncio.sleep(args.poll_seconds)
          continue
        agent_wait_started = None
        active = await active_market_data_requests()
        if active:
          if queue_wait_started is None:
            queue_wait_started = time.monotonic()
          if (
            time.monotonic() - queue_wait_started
            > args.queue_wait_timeout_seconds
          ):
            reason = (
              "QMT 行情队列被外部请求长期占用: "
              + ", ".join(
                f"{item['request_id']}:{item['status']}" for item in active
              )
            )
            _persist_status(
              state_path,
              state,
              status="paused",
              reason=reason,
            )
            return 3
          print(
            json.dumps(
              {
                "event": "waiting_for_market_data_queue",
                "requests": active,
              }
            ),
            flush=True,
          )
          await asyncio.sleep(args.poll_seconds)
          continue
        queue_wait_started = None
        try:
          run_id = client.submit(
            job,
            agent_device_id=agent_device_id,
            idempotency_key=f"{state['run_key']}:{job['id']}",
          )
        except (httpx.HTTPError, RuntimeError) as exc:
          job["submission_failures"] = int(
            job.get("submission_failures") or 0
          ) + 1
          job["last_submission_error"] = (
            f"{exc.__class__.__name__}: {exc}"
          )[:1000]
          _refresh_summary(state)
          _atomic_write_json(state_path, state)
          if job["submission_failures"] >= 3:
            _persist_status(
              state_path,
              state,
              status="paused",
              reason=job["last_submission_error"],
            )
            return 3
          await asyncio.sleep(args.poll_seconds)
          continue
        job["prefect_run_id"] = run_id
        job["agent_device_id"] = agent_device_id
        job["status"] = "running"
        job["submitted_at"] = _now_iso()
        _refresh_summary(state)
        _atomic_write_json(state_path, state)
        print(
          json.dumps(
            {
              "event": "job_submitted",
              "job_id": job["id"],
              "run_id": run_id,
              "codes": len(job["codes"]),
              "start_date": job["start_date"],
              "end_date": job["end_date"],
            }
          ),
          flush=True,
        )

      try:
        flow_run = client.flow_run(str(job["prefect_run_id"]))
      except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
          idempotency_key = f"{state['run_key']}:{job['id']}"
          print(
            json.dumps(
              {
                "event": "prefect_run_missing",
                "job_id": job["id"],
                "run_id": job.get("prefect_run_id"),
                "idempotency_key": idempotency_key,
              }
            ),
            flush=True,
          )
          recovered_run = client.flow_run_by_idempotency_key(
            idempotency_key
          )
          if recovered_run is not None:
            client._validate_confirmed_run(
              recovered_run,
              idempotency_key=idempotency_key,
              parameters=flow_parameters(
                job,
                agent_device_id=str(job.get("agent_device_id") or ""),
              ),
            )
            stale_run_id = str(job["prefect_run_id"])
            recovered_run_id = str(recovered_run["id"])
            if recovered_run_id != stale_run_id:
              orphan_ids = job.setdefault("orphan_prefect_run_ids", [])
              if stale_run_id not in orphan_ids:
                orphan_ids.append(stale_run_id)
              job["prefect_run_id"] = recovered_run_id
              job["recovered_at"] = _now_iso()
              job.pop("prefect_404_observations", None)
              _refresh_summary(state)
              _atomic_write_json(state_path, state)
              print(
                json.dumps(
                  {
                    "event": "prefect_run_recovered",
                    "job_id": job["id"],
                    "stale_run_id": stale_run_id,
                    "run_id": recovered_run_id,
                  }
                ),
                flush=True,
              )
              continue
            job["prefect_404_observations"] = int(
              job.get("prefect_404_observations") or 0
            ) + 1
            if (
              job["prefect_404_observations"] > 3
              or _elapsed_seconds(job.get("submitted_at"))
              > args.flow_timeout_seconds
            ):
              _persist_status(
                state_path,
                state,
                status="paused",
                reason=(
                  f"Prefect Flow {stale_run_id} 连续返回 404，"
                  "但幂等查询仍返回同一运行"
                ),
              )
              return 3
            _refresh_summary(state)
            _atomic_write_json(state_path, state)
            await asyncio.sleep(args.poll_seconds)
            continue

          audit = await request_audit(request_payload(job))
          if audit.get("request_id"):
            job["request_audit"] = audit
            if audit.get("ok") and await _audit_and_verify_job(job):
              job["status"] = "completed"
              job["finished_at"] = _now_iso()
              processed += 1
              _refresh_summary(state)
              _atomic_write_json(state_path, state)
              continue
            if audit.get("status") in ACTIVE_REQUEST_STATES:
              if (
                _elapsed_seconds(job.get("submitted_at"))
                > args.flow_timeout_seconds
              ):
                _persist_status(
                  state_path,
                  state,
                  status="paused",
                  reason=(
                    f"Prefect Flow {job.get('prefect_run_id')} 已消失，"
                    f"关联请求 {audit['request_id']} 长时间停留在 "
                    f"{audit.get('status')}"
                  ),
                )
                return 3
              await asyncio.sleep(args.poll_seconds)
              continue
            _persist_status(
              state_path,
              state,
              status="paused",
              reason=(
                f"Prefect Flow {job.get('prefect_run_id')} 已消失，"
                f"关联请求 {audit['request_id']} 状态为 "
                f"{audit.get('status')}"
              ),
            )
            return 3

          stale_run_id = str(job["prefect_run_id"])
          orphan_ids = job.setdefault("orphan_prefect_run_ids", [])
          if stale_run_id not in orphan_ids:
            orphan_ids.append(stale_run_id)
          job["status"] = "pending"
          job["prefect_run_id"] = None
          job["submitted_at"] = None
          job["prefect_404_retries"] = int(
            job.get("prefect_404_retries") or 0
          ) + 1
          if job["prefect_404_retries"] > 3:
            _persist_status(
              state_path,
              state,
              status="paused",
              reason=(
                f"任务 {job['id']} 的 Prefect 运行连续消失 "
                f"{job['prefect_404_retries']} 次"
              ),
            )
            return 3
          _refresh_summary(state)
          _atomic_write_json(state_path, state)
          print(
            json.dumps(
              {
                "event": "prefect_run_missing_resubmit",
                "job_id": job["id"],
                "stale_run_id": stale_run_id,
                "retry": job["prefect_404_retries"],
              }
            ),
            flush=True,
          )
          continue
        job["last_prefect_error"] = (
          f"{exc.__class__.__name__}: {exc}"
        )[:1000]
        if (
          _elapsed_seconds(job.get("submitted_at"))
          > args.flow_timeout_seconds
        ):
          _persist_status(
            state_path,
            state,
            status="paused",
            reason=job["last_prefect_error"],
          )
          return 3
        await asyncio.sleep(args.poll_seconds)
        continue
      except httpx.HTTPError as exc:
        job["last_prefect_error"] = (
          f"{exc.__class__.__name__}: {exc}"
        )[:1000]
        if (
          _elapsed_seconds(job.get("submitted_at"))
          > args.flow_timeout_seconds
        ):
          _persist_status(
            state_path,
            state,
            status="paused",
            reason=job["last_prefect_error"],
          )
          return 3
        await asyncio.sleep(args.poll_seconds)
        continue
      state_type = str(flow_run.get("state_type") or "").upper()
      if state_type not in TERMINAL_FLOW_STATES:
        if (
          _elapsed_seconds(job.get("submitted_at"))
          > args.flow_timeout_seconds
        ):
          reason = (
            f"Prefect Flow {job.get('prefect_run_id')} 超过 "
            f"{args.flow_timeout_seconds} 秒仍未结束"
          )
          _persist_status(
            state_path,
            state,
            status="paused",
            reason=reason,
          )
          return 3
        await asyncio.sleep(args.poll_seconds)
        continue

      job["flow_state"] = state_type
      job["finished_at"] = _now_iso()
      processed += 1
      if state_type == "COMPLETED":
        if await _audit_and_verify_job(job):
          job["status"] = "completed"
          audit = job["request_audit"]
          print(
            json.dumps(
              {
                "event": "job_completed",
                "job_id": job["id"],
                "run_id": job["prefect_run_id"],
                "request_id": audit["request_id"],
                "codes": len(job["codes"]),
                "records": audit["records"],
              }
            ),
            flush=True,
          )
        else:
          job["status"] = "verification_failed"
      else:
        job["status"] = "flow_failed"
        job["flow_message"] = (flow_run.get("state") or {}).get("message")

      if job["status"] != "completed":
        if args.split_failures and _append_split_children(
          state,
          job,
          max_split_depth=args.max_split_depth,
          max_total_jobs=args.max_total_jobs,
        ):
          job["status"] = "superseded"
          print(
            json.dumps(
              {
                "event": "job_split",
                "job_id": job["id"],
                "flow_state": state_type,
                "children": job["children"],
              }
            ),
            flush=True,
          )
        else:
          _persist_status(
            state_path,
            state,
            status="paused",
            reason=(
              f"任务 {job['id']} 未通过，flow_state={state_type}; "
              "默认不自动拆分系统性失败"
            ),
          )
          return 2
      _refresh_summary(state)
      _atomic_write_json(state_path, state)
  except Exception as exc:
    if state is not None:
      _persist_status(
        state_path,
        state,
        status="error",
        reason=f"{exc.__class__.__name__}: {exc}",
      )
    raise
  finally:
    if client is not None:
      client.close()
    await campaign_lock.release()


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="通过 QMT Agent 串行、可恢复地回填全市场日线",
  )
  parser.add_argument("--start-date", type=_date, required=True)
  parser.add_argument("--end-date", type=_date, required=True)
  parser.add_argument("--batch-size", type=int, default=100)
  parser.add_argument("--code-limit", type=int)
  parser.add_argument("--max-jobs", type=int)
  parser.add_argument("--poll-seconds", type=float, default=5.0)
  parser.add_argument("--agent-wait-timeout-seconds", type=float, default=600)
  parser.add_argument("--queue-wait-timeout-seconds", type=float, default=1200)
  parser.add_argument("--flow-timeout-seconds", type=float, default=1200)
  parser.add_argument("--split-failures", action="store_true")
  parser.add_argument(
    "--retry-failed-ingestion",
    action="store_true",
    help=(
      "显式恢复完整上传但入库失败的 flow_failed 任务；"
      "不会重新请求 QMT，也不适用于验收失败"
    ),
  )
  parser.add_argument("--max-split-depth", type=int, default=4)
  parser.add_argument("--max-total-jobs", type=int, default=1000)
  parser.add_argument("--state-file", required=True)
  parser.add_argument(
    "--prefect-api-url",
    default=DEFAULT_PREFECT_API_URL,
  )
  parser.add_argument(
    "--deployment-name",
    default=DEFAULT_DEPLOYMENT_NAME,
  )
  args = parser.parse_args()
  if args.batch_size <= 0:
    parser.error("--batch-size 必须大于 0")
  if args.code_limit is not None and args.code_limit <= 0:
    parser.error("--code-limit 必须大于 0")
  if args.max_jobs is not None and args.max_jobs <= 0:
    parser.error("--max-jobs 必须大于 0")
  if args.poll_seconds <= 0:
    parser.error("--poll-seconds 必须大于 0")
  if args.agent_wait_timeout_seconds <= 0:
    parser.error("--agent-wait-timeout-seconds 必须大于 0")
  if args.queue_wait_timeout_seconds <= 0:
    parser.error("--queue-wait-timeout-seconds 必须大于 0")
  if args.flow_timeout_seconds <= 0:
    parser.error("--flow-timeout-seconds 必须大于 0")
  if args.max_split_depth < 0:
    parser.error("--max-split-depth 不能小于 0")
  if args.max_total_jobs <= 0:
    parser.error("--max-total-jobs 必须大于 0")
  if args.end_date < args.start_date:
    parser.error("--end-date 不能早于 --start-date")
  return args


def main() -> int:
  return asyncio.run(run(parse_args()))


if __name__ == "__main__":
  raise SystemExit(main())
