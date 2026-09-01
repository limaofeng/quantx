"""Resumable stock-and-ETF QMT dividend-factor backfill.

This operational script only creates durable ``market_data_request`` rows.
XTData remains isolated in the outbound QMT Agent's low-priority
historical worker process.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quantx_infrastructure import DurableRuntimeStore
from quantx_infrastructure.database.relational_connection import (
  AsyncSessionLocal,
)
from quantx_infrastructure.database.relational_connection import (
  engine as relational_engine,
)
from quantx_infrastructure.models.agent_runtime import MarketDataRequest
from quantx_infrastructure.models.divid_factor import DividFactorTable
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.repositories.divid_factor_repository import (
  divid_factor_codes_sha256,
  divid_factor_rows_sha256,
)
from quantx_worker.prefector.flows.divid_factor_sync_flow import (
  divid_factor_sync_flow,
)
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

SCHEMA_VERSION = 3
OPERATION_VERSION = "qmt-get-divid-factors-v1"
BENCHMARK_CODE = "000300.SH"
UNIVERSE_VERSION = "stock-etf-plus-csi300-v1"
UNIVERSE_INSTRUMENT_TYPES = (
  InstrumentType.STOCK,
  InstrumentType.ETF,
)
_A_SHARE_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_DIRECTORY = ROOT / ".runtime" / "research-backfill"
ACTIVE_REQUEST_STATES = {
  "QUEUED",
  "DELIVERED",
  "RECEIVING",
  "UPLOADED",
  "PROCESSING",
}
MARKET_DATA_READY_STATUSES = {"READY", "RECONCILING"}
# Intentionally shared with backfill_daily_market_data.py. A factor campaign
# cannot race the daily-bar campaign for the one serial XTData request worker.
CAMPAIGN_LOCK_KEY = int.from_bytes(
  hashlib.sha256(b"quantx:qmt-daily-history-backfill").digest()[:8],
  byteorder="big",
  signed=True,
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_JOB_HISTORY_EVENTS = 50
RUNNING_RESELECT_AFTER_FAILURES = 3
RUNNING_STOP_AFTER_FAILURES = 9


class CampaignDatabaseLock:
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
      raise RuntimeError("日线或复权因子 QMT 回填正在运行；请等待其释放全局数据回填锁")

  async def release(self) -> None:
    if self.connection is None:
      return
    connection = self.connection
    self.connection = None
    try:
      if not connection.closed:
        await connection.scalar(
          text("SELECT pg_advisory_unlock(:lock_key)"),
          {"lock_key": CAMPAIGN_LOCK_KEY},
        )
    except SQLAlchemyError:
      # PostgreSQL advisory locks are connection-scoped.  A dropped or
      # invalidated connection has already released the lock server-side;
      # cleanup must not hide the original campaign failure.
      pass
    finally:
      try:
        await connection.close()
      except SQLAlchemyError:
        pass


def _date(value: str) -> date:
  compact = str(value or "").strip().replace("-", "")
  if len(compact) != 8 or not compact.isdigit():
    raise argparse.ArgumentTypeError("日期必须是 YYYYMMDD 或 YYYY-MM-DD")
  return datetime.strptime(compact, "%Y%m%d").date()


def _compact(value: date) -> str:
  return value.strftime("%Y%m%d")


def _now_iso() -> str:
  return datetime.now().astimezone().isoformat()


def _append_job_history(
  job: dict[str, Any],
  field: str,
  event: dict[str, Any],
) -> None:
  history = job.setdefault(field, [])
  history.append(event)
  if len(history) > MAX_JOB_HISTORY_EVENTS:
    del history[: len(history) - MAX_JOB_HISTORY_EVENTS]


def _sha256_json(value: Any) -> str:
  encoded = json.dumps(
    value,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _code_hash(codes: list[str]) -> str:
  return hashlib.sha256("\n".join(codes).encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
  temporary.write_text(
    json.dumps(value, ensure_ascii=False, indent=2),
    encoding="utf-8",
  )
  os.replace(temporary, path)


def build_jobs(codes: list[str], batch_size: int) -> list[dict[str, Any]]:
  if batch_size <= 0 or batch_size > 500:
    raise ValueError("batch_size 必须在 1..500")
  normalized = sorted(
    {str(code).strip().upper() for code in codes if str(code).strip()}
  )
  jobs = []
  for offset in range(0, len(normalized), batch_size):
    batch = normalized[offset : offset + batch_size]
    jobs.append(
      {
        "id": f"factors-{offset:06d}-{_code_hash(batch)[:12]}",
        "codes": batch,
        "status": "pending",
        "attempt": 0,
      }
    )
  return jobs


async def load_universe(
  *,
  start: date,
  end: date,
  code_limit: int | None,
) -> tuple[list[str], dict[str, Any]]:
  async with AsyncSessionLocal() as db:
    rows = (
      await db.execute(
        select(
          Instrument.id,
          Instrument.type,
          Instrument.open_date,
          Instrument.expire_date,
        )
        .where(Instrument.type.in_(UNIVERSE_INSTRUMENT_TYPES))
        .order_by(Instrument.id.asc())
      )
    ).all()
    benchmark = (
      await db.execute(
        select(Instrument.id, Instrument.type).where(Instrument.id == BENCHMARK_CODE)
      )
    ).one_or_none()
  if benchmark is None or benchmark.type != InstrumentType.INDEX:
    raise RuntimeError(f"PostgreSQL 缺少指数标的 {BENCHMARK_CODE}")

  codes_by_type = {
    InstrumentType.STOCK: [],
    InstrumentType.ETF: [],
  }
  for code, instrument_type, open_date, expire_date in rows:
    normalized = str(code or "").strip().upper()
    if not _A_SHARE_CODE_PATTERN.fullmatch(normalized):
      continue
    if open_date is not None and open_date > end:
      continue
    if expire_date is not None and expire_date < start:
      continue
    if instrument_type in codes_by_type:
      codes_by_type[instrument_type].append(normalized)
  for instrument_type in UNIVERSE_INSTRUMENT_TYPES:
    type_codes = sorted(dict.fromkeys(codes_by_type[instrument_type]))
    if code_limit is not None:
      type_codes = type_codes[:code_limit]
    codes_by_type[instrument_type] = type_codes

  stock_codes = codes_by_type[InstrumentType.STOCK]
  etf_codes = codes_by_type[InstrumentType.ETF]
  if not stock_codes:
    raise RuntimeError("PostgreSQL 中没有研究窗口内的沪深股票标的")
  if not etf_codes:
    raise RuntimeError("PostgreSQL 中没有研究窗口内的沪深 ETF 标的")
  codes = sorted([*stock_codes, *etf_codes, BENCHMARK_CODE])
  return codes, {
    "universe_version": UNIVERSE_VERSION,
    "instrument_types": [
      instrument_type.name for instrument_type in UNIVERSE_INSTRUMENT_TYPES
    ],
    "stock_count": len(stock_codes),
    "etf_count": len(etf_codes),
    "market_instrument_count": len(stock_codes) + len(etf_codes),
    "requested_code_count": len(codes),
    "benchmark_code": BENCHMARK_CODE,
    "benchmark_count": 1,
    "stock_code_sha256": _code_hash(stock_codes),
    "etf_code_sha256": _code_hash(etf_codes),
    "code_sha256": _code_hash(codes),
  }


def default_state_path(*, start: date, end: date) -> Path:
  return (
    DEFAULT_STATE_DIRECTORY
    / (f"full-stock-etf-divid-factors-{_compact(start)}-{_compact(end)}.json")
  ).resolve()


def _validate_state_universe_audit(state: dict[str, Any]) -> None:
  universe = state.get("universe") or {}
  expected_header = {
    "universe_version": UNIVERSE_VERSION,
    "instrument_types": [
      instrument_type.name for instrument_type in UNIVERSE_INSTRUMENT_TYPES
    ],
    "benchmark_code": BENCHMARK_CODE,
    "benchmark_count": 1,
  }
  actual_header = {key: universe.get(key) for key in expected_header}
  if actual_header != expected_header:
    raise RuntimeError(
      "状态账本 universe 审计口径不一致: "
      f"expected={expected_header} actual={actual_header}"
    )

  stock_count = int(universe.get("stock_count") or 0)
  etf_count = int(universe.get("etf_count") or 0)
  market_instrument_count = int(universe.get("market_instrument_count") or 0)
  requested_code_count = int(universe.get("requested_code_count") or 0)
  if (
    stock_count <= 0
    or etf_count <= 0
    or market_instrument_count != stock_count + etf_count
    or requested_code_count != market_instrument_count + 1
  ):
    raise RuntimeError("状态账本 universe 数量审计不一致")

  ledger_codes = [
    str(code).strip().upper()
    for job in state.get("jobs") or []
    for code in job.get("codes") or []
    if str(code).strip()
  ]
  job_codes = sorted(set(ledger_codes))
  expected_sha256 = str(state.get("universe_sha256") or "")
  if (
    len(ledger_codes) != len(job_codes)
    or len(job_codes) != requested_code_count
    or BENCHMARK_CODE not in job_codes
    or _code_hash(job_codes) != expected_sha256
    or str(universe.get("code_sha256") or "") != expected_sha256
    or not str(universe.get("stock_code_sha256") or "")
    or not str(universe.get("etf_code_sha256") or "")
  ):
    raise RuntimeError("状态账本 universe 代码摘要审计不一致")


def _request_key(state: dict[str, Any], job: dict[str, Any]) -> str:
  return f"{state['run_key']}:{job['id']}:attempt-{int(job.get('attempt') or 0)}"


def request_payload(
  state: dict[str, Any],
  job: dict[str, Any],
) -> dict[str, Any]:
  return {
    "operation": "divid_factors",
    "source": OPERATION_VERSION,
    "stock_list": sorted(job["codes"]),
    "start_time": state["start_date"],
    "end_time": state["end_date"],
    "request_key": _request_key(state, job),
  }


def _request_idempotency_key(payload: dict[str, Any]) -> str:
  return _sha256_json(payload)


async def ensure_factor_agent_ready(max_age_seconds: int = 90) -> str:
  store = DurableRuntimeStore()
  try:
    statuses = await store.component_status("qmt-agent:")
  finally:
    await store.close()
  now = datetime.now(timezone.utc)
  candidates: list[tuple[datetime, str]] = []
  for item in statuses:
    if item.get("status") not in MARKET_DATA_READY_STATUSES:
      continue
    details = item.get("details") or {}
    capabilities = set(details.get("capabilities") or [])
    if not {"market-data", "divid-factors"}.issubset(
      capabilities
    ) or not capabilities.intersection({"live", "data-only"}):
      continue
    updated_at = item.get("updated_at")
    if not isinstance(updated_at, datetime):
      continue
    if updated_at.tzinfo is None:
      updated_at = updated_at.replace(tzinfo=timezone.utc)
    if abs((now - updated_at.astimezone(timezone.utc)).total_seconds()) > (
      max_age_seconds
    ):
      continue
    device_id = str(item.get("instance_id") or "").strip()
    if device_id:
      candidates.append((updated_at, device_id))
  if not candidates:
    raise RuntimeError(
      "没有新鲜且处于 READY 或 RECONCILING、并声明 "
      "market-data/divid-factors 与 live/data-only 模式的 QMT Agent；"
      "部署新 operation 后需重启 Agent"
    )
  return max(candidates, key=lambda item: item[0])[1]


async def foreign_active_requests(
  *,
  own_idempotency_key: str,
) -> list[dict[str, str]]:
  async with AsyncSessionLocal() as db:
    rows = (
      await db.execute(
        select(
          MarketDataRequest.request_id,
          MarketDataRequest.status,
          MarketDataRequest.idempotency_key,
        ).where(MarketDataRequest.status.in_(ACTIVE_REQUEST_STATES))
      )
    ).all()
  return [
    {
      "request_id": str(request_id),
      "status": str(status),
      "idempotency_key": str(idempotency_key),
    }
    for request_id, status, idempotency_key in rows
    if str(idempotency_key) != own_idempotency_key
  ]


async def verify_completed_request(
  *,
  request_id: str,
  expected_payload: dict[str, Any],
) -> dict[str, Any]:
  store = DurableRuntimeStore()
  try:
    request = await store.market_data_request(request_id)
    manifest = await store.market_data_transfers(request_id)
  finally:
    await store.close()
  if request is None or str(request.get("status")) != "COMPLETED":
    raise RuntimeError(f"复权因子请求未完成: {request_id}")
  actual_payload = request.get("request_payload") or {}
  if isinstance(actual_payload, str):
    actual_payload = json.loads(actual_payload)
  if actual_payload != expected_payload:
    raise RuntimeError("复权因子请求 payload 与状态账本不一致")

  ingestion_result = request.get("ingestion_result") or {}
  if isinstance(ingestion_result, str):
    ingestion_result = json.loads(ingestion_result)
  if not isinstance(ingestion_result, dict):
    raise RuntimeError("复权因子请求缺少持久化入库结果")
  if ingestion_result.get("operation") != "divid_factors":
    raise RuntimeError("复权因子请求入库 operation 不一致")

  def audit_count(container: dict[str, Any], field: str) -> int:
    value = container.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
      raise RuntimeError(f"复权因子入库审计字段无效: {field}")
    return value

  records_received = audit_count(ingestion_result, "records_received")
  records_saved = audit_count(ingestion_result, "records_saved")
  replacement = ingestion_result.get("replacement_audit")
  if not isinstance(replacement, dict):
    raise RuntimeError("复权因子请求缺少范围替换审计")
  if replacement.get("audit_schema_version") != 1:
    raise RuntimeError("复权因子范围替换审计版本不受支持")

  prior_count = audit_count(replacement, "prior_count")
  deleted_count = audit_count(replacement, "deleted_count")
  inserted_count = audit_count(replacement, "inserted_count")
  verified_count = audit_count(replacement, "verified_count")
  stock_count = audit_count(replacement, "stock_count")
  codes = sorted(expected_payload["stock_list"])
  if expected_payload["stock_list"] != codes or len(set(codes)) != len(codes):
    raise RuntimeError("复权因子请求证券范围必须排序且无重复")
  if deleted_count != prior_count:
    raise RuntimeError("复权因子范围替换删除数与原有记录数不一致")
  if stock_count != len(codes):
    raise RuntimeError("复权因子范围替换审计证券数不一致")
  if replacement.get("stock_codes_sha256") != divid_factor_codes_sha256(codes):
    raise RuntimeError("复权因子范围替换审计证券范围摘要不一致")
  if replacement.get("start_ex_date") != expected_payload["start_time"] or (
    replacement.get("end_ex_date") != expected_payload["end_time"]
  ):
    raise RuntimeError("复权因子范围替换审计日期窗口不一致")
  if not (records_received == records_saved == inserted_count == verified_count):
    raise RuntimeError("复权因子入库记录数审计不一致")
  source_digest = str(replacement.get("source_sha256") or "")
  expected_digest = str(replacement.get("persisted_sha256") or "")
  if not re.fullmatch(r"[0-9a-f]{64}", source_digest) or not re.fullmatch(
    r"[0-9a-f]{64}", expected_digest
  ):
    raise RuntimeError("复权因子范围替换审计摘要无效")
  if source_digest != expected_digest:
    raise RuntimeError("复权因子范围替换源数据与持久化摘要不一致")

  expected_chunks = audit_count(request, "expected_chunks")
  received_chunks = audit_count(request, "received_chunks")
  if expected_chunks <= 0 or received_chunks != expected_chunks:
    raise RuntimeError("复权因子请求分片完成数不一致")
  if len(manifest) != expected_chunks:
    raise RuntimeError("复权因子请求持久化分片清单不完整")
  chunk_indexes: list[int] = []
  transferred_record_count = 0
  for item in manifest:
    chunk_indexes.append(audit_count(item, "chunk_index"))
    transferred_record_count += audit_count(item, "record_count")
    if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("checksum_sha256") or "")):
      raise RuntimeError("复权因子请求持久化分片摘要无效")
  if chunk_indexes != list(range(expected_chunks)):
    raise RuntimeError("复权因子请求持久化分片序号不连续")
  if transferred_record_count != records_received:
    raise RuntimeError("复权因子请求分片记录数与入库审计不一致")

  async with AsyncSessionLocal() as db:
    persisted = (
      await db.execute(
        select(
          DividFactorTable.stock_code,
          DividFactorTable.time,
          DividFactorTable.ex_date,
          DividFactorTable.interest,
          DividFactorTable.stock_bonus,
          DividFactorTable.stock_gift,
          DividFactorTable.allot_num,
          DividFactorTable.allot_price,
          DividFactorTable.gugai,
          DividFactorTable.dr,
        )
        .where(
          DividFactorTable.stock_code.in_(codes),
          DividFactorTable.ex_date >= expected_payload["start_time"],
          DividFactorTable.ex_date <= expected_payload["end_time"],
        )
        .order_by(
          DividFactorTable.stock_code.asc(),
          DividFactorTable.ex_date.asc(),
        )
      )
    ).all()
  actual_rows = [tuple(row) for row in persisted]
  actual_digest = divid_factor_rows_sha256(actual_rows)
  if len(actual_rows) != verified_count or actual_digest != expected_digest:
    raise RuntimeError(
      "PostgreSQL 复权因子持久化摘要验收失败: "
      f"audited={verified_count} persisted={len(actual_rows)}"
    )

  source_codes = sorted({str(row[0]) for row in actual_rows})
  ex_dates = sorted(str(row[2]) for row in actual_rows)
  return {
    "request_id": request_id,
    "source_record_count": records_received,
    "persisted_record_count": len(actual_rows),
    "requested_code_count": len(codes),
    "source_code_count": len(source_codes),
    "codes_without_events": len(codes) - len(source_codes),
    "min_ex_date": ex_dates[0] if ex_dates else "",
    "max_ex_date": ex_dates[-1] if ex_dates else "",
    "source_sha256": expected_digest,
    "persisted_sha256": actual_digest,
    "expected_chunks": expected_chunks,
    "transferred_record_count": transferred_record_count,
    "verified_at": _now_iso(),
  }


def _refresh_summary(state: dict[str, Any]) -> None:
  jobs = state["jobs"]
  state["summary"] = {
    "total_jobs": len(jobs),
    "completed_jobs": sum(job["status"] == "completed" for job in jobs),
    "pending_jobs": sum(job["status"] == "pending" for job in jobs),
    "running_jobs": sum(job["status"] == "running" for job in jobs),
    "waiting_jobs": sum(job["status"] == "waiting" for job in jobs),
    "verifying_jobs": sum(job["status"] == "verifying" for job in jobs),
    "failed_jobs": sum(job["status"] == "failed" for job in jobs),
    "source_records": sum(
      int((job.get("audit") or {}).get("source_record_count") or 0)
      for job in jobs
      if job["status"] == "completed"
    ),
    "persisted_records": sum(
      int((job.get("audit") or {}).get("persisted_record_count") or 0)
      for job in jobs
      if job["status"] == "completed"
    ),
  }
  state["updated_at"] = _now_iso()


async def _load_or_create_state(
  args: argparse.Namespace,
  state_path: Path,
) -> dict[str, Any]:
  if state_path.exists():
    state = json.loads(state_path.read_text(encoding="utf-8"))
    expected = {
      "schema_version": SCHEMA_VERSION,
      "operation_version": OPERATION_VERSION,
      "universe_version": UNIVERSE_VERSION,
      "start_date": _compact(args.start_date),
      "end_date": _compact(args.end_date),
      "batch_size": args.batch_size,
      "code_limit": args.code_limit,
    }
    actual = {key: state.get(key) for key in expected}
    if actual != expected:
      raise RuntimeError(
        f"回填参数与状态账本不一致: expected={expected} actual={actual}"
      )
    _validate_state_universe_audit(state)
    return state

  codes, universe = await load_universe(
    start=args.start_date,
    end=args.end_date,
    code_limit=args.code_limit,
  )
  identity = {
    "operation_version": OPERATION_VERSION,
    "universe_version": UNIVERSE_VERSION,
    "start_date": _compact(args.start_date),
    "end_date": _compact(args.end_date),
    "batch_size": args.batch_size,
    "code_limit": args.code_limit,
    "universe_sha256": universe["code_sha256"],
  }
  state = {
    "schema_version": SCHEMA_VERSION,
    **identity,
    "run_key": f"divid-factor-{_sha256_json(identity)[:20]}",
    "status": "pending",
    "created_at": _now_iso(),
    "universe": universe,
    "jobs": build_jobs(codes, args.batch_size),
  }
  _refresh_summary(state)
  _atomic_write_json(state_path, state)
  return state


def _next_job(state: dict[str, Any]) -> dict[str, Any] | None:
  return next(
    (
      job
      for job in state["jobs"]
      if job["status"] in {"pending", "running", "waiting", "verifying"}
    ),
    None,
  )


def _failed_jobs(state: dict[str, Any]) -> list[dict[str, Any]]:
  return [job for job in state["jobs"] if job["status"] == "failed"]


def _explicit_retry_jobs(state: dict[str, Any]) -> list[dict[str, Any]]:
  return [
    job
    for job in state["jobs"]
    if job.get("status") == "failed"
    or (job.get("status") == "verifying" and job.get("last_error"))
  ]


def _campaign_incomplete_error(state: dict[str, Any]) -> str:
  incomplete = [job for job in state["jobs"] if job["status"] != "completed"]
  if not incomplete:
    return ""
  counts: dict[str, int] = {}
  for job in incomplete:
    status = str(job.get("status") or "missing")
    counts[status] = counts.get(status, 0) + 1
  rendered = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
  return f"复权因子 campaign 仍有未完成作业，拒绝标记 completed: {rendered}"


def _retry_failed_jobs(
  state: dict[str, Any],
  *,
  max_attempts: int,
) -> list[str]:
  """Explicitly reopen failed jobs without reusing a failed request key."""
  if max_attempts <= 0:
    raise ValueError("max_attempts 必须大于 0")
  retried: list[str] = []
  requested_at = _now_iso()
  for job in _explicit_retry_jobs(state):
    previous_status = str(job.get("status") or "missing")
    next_attempt = int(job.get("attempt") or 0) + (
      1 if previous_status == "verifying" else 0
    )
    previous_limit = int(job.get("attempt_limit") or next_attempt)
    attempt_limit = next_attempt + max_attempts
    _append_job_history(
      job,
      "retry_history",
      {
        "requested_at": requested_at,
        "previous_status": previous_status,
        "next_attempt": next_attempt,
        "previous_attempt_limit": previous_limit,
        "attempt_limit": attempt_limit,
        "last_error": str(job.get("last_error") or "")[:2000],
      },
    )
    job["attempt"] = next_attempt
    job["status"] = "pending"
    job["attempt_limit"] = attempt_limit
    job["retry_requested_at"] = requested_at
    job.pop("agent_device_id", None)
    job.pop("durable_status", None)
    job.pop("transient_failures", None)
    job.pop("verification_failures", None)
    retried.append(str(job["id"]))
  return retried


def _persist_state(
  state_path: Path,
  state: dict[str, Any],
  *,
  status: str | None = None,
  error: str = "",
) -> None:
  if status is not None:
    state["status"] = status
  if error:
    state["last_error"] = error[:2000]
  elif status in {"running", "completed"}:
    state.pop("last_error", None)
  _refresh_summary(state)
  _atomic_write_json(state_path, state)


async def _reverify_completed_jobs(state: dict[str, Any]) -> str:
  """Re-prove every completed scope immediately before campaign completion."""

  verified_at = _now_iso()
  for job in state["jobs"]:
    if job.get("status") != "completed":
      continue
    request_id = str(job.get("request_id") or "")
    if not request_id:
      error = f"completed 作业缺少 request_id: {job.get('id')}"
    else:
      try:
        audit = await verify_completed_request(
          request_id=request_id,
          expected_payload=request_payload(state, job),
        )
      except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
      else:
        job["audit"] = audit
        job["final_verified_at"] = verified_at
        continue

    _append_job_history(
      job,
      "final_verification_history",
      {
        "attempt": int(job.get("attempt") or 0),
        "request_id": request_id,
        "failed_at": verified_at,
        "error": error[:2000],
      },
    )
    job["attempt"] = int(job.get("attempt") or 0) + 1
    job["status"] = "failed"
    job["last_error"] = error[:2000]
    job["failed_at"] = verified_at
    return f"最终复权因子验收失败: job={job.get('id')} {error}"

  state["final_verification"] = {
    "verified_at": verified_at,
    "verified_jobs": len(state["jobs"]),
  }
  return ""


async def run(args: argparse.Namespace) -> int:
  if args.end_date < args.start_date:
    raise RuntimeError("结束日期不能早于开始日期")
  state_path = (
    Path(args.state_file).resolve()
    if args.state_file
    else default_state_path(start=args.start_date, end=args.end_date)
  )
  campaign_lock = CampaignDatabaseLock()
  await campaign_lock.acquire()
  try:
    state = await _load_or_create_state(args, state_path)
    failed = _failed_jobs(state)
    if failed and not args.retry_failed:
      error = (
        f"状态账本包含 {len(failed)} 个 failed 作业；"
        "必须显式使用 --retry-failed 才能恢复"
      )
      _persist_state(state_path, state, status="failed", error=error)
      print(
        json.dumps(
          {
            "event": "divid_factor_backfill_failed",
            "state_file": str(state_path),
            "failed_jobs": [str(job["id"]) for job in failed],
            "reason": error,
          },
          ensure_ascii=False,
        ),
        flush=True,
      )
      return 2
    retried = (
      _retry_failed_jobs(state, max_attempts=args.max_attempts)
      if args.retry_failed
      else []
    )
    _persist_state(state_path, state, status="running")
    if retried:
      print(
        json.dumps(
          {
            "event": "divid_factor_failed_jobs_retried",
            "state_file": str(state_path),
            "jobs": retried,
            "attempts_per_job": args.max_attempts,
          },
          ensure_ascii=False,
        ),
        flush=True,
      )
    processed = 0
    print(
      json.dumps(
        {
          "event": "divid_factor_backfill_started",
          "state_file": str(state_path),
          "run_key": state["run_key"],
          "universe_version": state["universe"]["universe_version"],
          "stock_count": state["universe"]["stock_count"],
          "etf_count": state["universe"]["etf_count"],
          "market_instrument_count": state["universe"]["market_instrument_count"],
          "requested_code_count": state["universe"]["requested_code_count"],
          "jobs": len(state["jobs"]),
        },
        ensure_ascii=False,
      ),
      flush=True,
    )
    while True:
      job = _next_job(state)
      if job is None:
        error = _campaign_incomplete_error(state)
        if error:
          _persist_state(state_path, state, status="failed", error=error)
          print(
            json.dumps(
              {
                "event": "divid_factor_backfill_failed",
                "state_file": str(state_path),
                "reason": error,
                "summary": state["summary"],
              },
              ensure_ascii=False,
            ),
            flush=True,
          )
          return 2
        error = await _reverify_completed_jobs(state)
        if error:
          _persist_state(state_path, state, status="failed", error=error)
          print(
            json.dumps(
              {
                "event": "divid_factor_backfill_failed",
                "state_file": str(state_path),
                "reason": error,
                "summary": state["summary"],
              },
              ensure_ascii=False,
            ),
            flush=True,
          )
          return 2
        _persist_state(state_path, state, status="completed")
        return 0
      if args.max_jobs is not None and processed >= args.max_jobs:
        _persist_state(state_path, state, status="paused")
        return 0

      payload = request_payload(state, job)
      if job["status"] == "verifying":
        request_id = str(job.get("request_id") or "")
        if not request_id:
          raise RuntimeError(f"verifying 作业缺少 request_id: {job['id']}")
      else:
        if job["status"] == "pending":
          foreign = await foreign_active_requests(
            own_idempotency_key=_request_idempotency_key(payload)
          )
          if foreign:
            print(
              json.dumps(
                {
                  "event": "waiting_for_market_data_queue",
                  "requests": foreign,
                }
              ),
              flush=True,
            )
            await asyncio.sleep(args.poll_seconds)
            continue
          device_id = await ensure_factor_agent_ready()
          job["agent_device_id"] = device_id
          job["started_at"] = _now_iso()
        else:
          # ``running`` and ``waiting`` resume the same idempotent payload.
          # Do not require a live Agent before checking an already completed
          # durable request; server-side ingestion may also still converge.
          device_id = str(job.get("agent_device_id") or "")
        job["status"] = "running"
        job["request_key"] = payload["request_key"]
        job.setdefault("attempt_limit", int(args.max_attempts))
        _persist_state(state_path, state)
        try:
          result = await divid_factor_sync_flow.fn(
            stock_list=job["codes"],
            start_time=state["start_date"],
            end_time=state["end_date"],
            agent_device_id=device_id,
            timeout_seconds=args.timeout_seconds,
            request_key=str(payload["request_key"]),
          )
        except Exception as exc:
          error = f"{exc.__class__.__name__}: {exc}"
          job["last_error"] = error[:2000]
          transient_failures = int(job.get("transient_failures") or 0) + 1
          job["transient_failures"] = transient_failures
          _append_job_history(
            job,
            "transient_history",
            {
              "attempt": int(job.get("attempt") or 0),
              "failure_number": transient_failures,
              "request_key": str(payload["request_key"]),
              "observed_at": _now_iso(),
              "error": error[:2000],
            },
          )
          if transient_failures >= RUNNING_STOP_AFTER_FAILURES:
            job["attempt"] = int(job.get("attempt") or 0) + 1
            job["status"] = "failed"
            job["failed_at"] = _now_iso()
            _persist_state(state_path, state, status="failed", error=error)
            return 2
          if transient_failures % RUNNING_RESELECT_AFTER_FAILURES == 0:
            job["status"] = "pending"
            job.pop("agent_device_id", None)
          else:
            job["status"] = "running"
          _persist_state(state_path, state, error=error)
          await asyncio.sleep(args.poll_seconds)
          continue

        job.pop("transient_failures", None)
        result_status = str(result.get("status") or "").lower()
        request_id = str(result.get("request_id") or "")
        if result_status == "timeout" and request_id:
          job["status"] = "waiting"
          job["request_id"] = request_id
          job["durable_status"] = str(result.get("durable_status") or "")
          job["last_wait_timeout_at"] = _now_iso()
          _append_job_history(
            job,
            "wait_history",
            {
              "attempt": int(job.get("attempt") or 0),
              "request_key": str(payload["request_key"]),
              "request_id": request_id,
              "durable_status": job["durable_status"],
              "observed_at": job["last_wait_timeout_at"],
            },
          )
          _persist_state(state_path, state)
          await asyncio.sleep(args.poll_seconds)
          continue
        if result_status != "completed" or not request_id:
          error = (
            "复权因子请求终态失败: "
            f"{result_status or 'missing-status'}/{result.get('reason', '')}"
          )
          job["last_error"] = error[:2000]
          _append_job_history(
            job,
            "failure_history",
            {
              "attempt": int(job.get("attempt") or 0),
              "request_key": str(payload["request_key"]),
              "request_id": request_id,
              "failed_at": _now_iso(),
              "error": error[:2000],
            },
          )
          job["attempt"] = int(job.get("attempt") or 0) + 1
          attempt_limit = int(job.get("attempt_limit") or args.max_attempts)
          if job["attempt"] >= attempt_limit:
            job["status"] = "failed"
            job["failed_at"] = _now_iso()
            _persist_state(state_path, state, status="failed", error=error)
            return 2
          job["status"] = "pending"
          _persist_state(state_path, state, error=error)
          await asyncio.sleep(args.poll_seconds)
          continue

        job["status"] = "verifying"
        job["request_id"] = request_id
        job["request_completed_at"] = _now_iso()
        _persist_state(state_path, state)

      try:
        audit = await verify_completed_request(
          request_id=request_id,
          expected_payload=payload,
        )
      except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
        job["last_error"] = error[:2000]
        verification_failures = int(job.get("verification_failures") or 0) + 1
        job["verification_failures"] = verification_failures
        _append_job_history(
          job,
          "verification_history",
          {
            "attempt": int(job.get("attempt") or 0),
            "failure_number": verification_failures,
            "request_key": str(payload["request_key"]),
            "request_id": request_id,
            "verification_failed_at": _now_iso(),
            "error": error[:2000],
          },
        )
        if verification_failures >= 2:
          job["attempt"] = int(job.get("attempt") or 0) + 1
          job["status"] = "failed"
          job["failed_at"] = _now_iso()
        else:
          job["status"] = "verifying"
        _persist_state(
          state_path,
          state,
          status="failed",
          error=error,
        )
        return 2

      job["status"] = "completed"
      job["request_id"] = audit["request_id"]
      job["audit"] = audit
      job["finished_at"] = _now_iso()
      job.pop("last_error", None)
      job.pop("verification_failures", None)
      processed += 1
      _persist_state(state_path, state)
      print(
        json.dumps(
          {
            "event": "divid_factor_job_completed",
            "job_id": job["id"],
            "request_id": audit["request_id"],
            "requested_codes": audit["requested_code_count"],
            "event_codes": audit["source_code_count"],
            "records": audit["source_record_count"],
            "source_sha256": audit["source_sha256"],
          },
          ensure_ascii=False,
        ),
        flush=True,
      )
  finally:
    await campaign_lock.release()


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description=("通过唯一活动 QMT Agent 可恢复回填沪深股票、ETF 与 000300.SH 复权因子")
  )
  parser.add_argument("--start-date", required=True, type=_date)
  parser.add_argument("--end-date", required=True, type=_date)
  parser.add_argument("--batch-size", type=int, default=200)
  parser.add_argument(
    "--code-limit",
    type=int,
    help="仅用于 smoke：分别限制股票和 ETF 的代码数，不移除 000300.SH",
  )
  parser.add_argument("--state-file", default="")
  parser.add_argument("--poll-seconds", type=float, default=3.0)
  parser.add_argument("--timeout-seconds", type=int, default=900)
  parser.add_argument("--max-attempts", type=int, default=3)
  parser.add_argument("--max-jobs", type=int)
  parser.add_argument(
    "--retry-failed",
    action="store_true",
    help=(
      "显式恢复状态账本中的 failed 作业，并为每个作业增加 "
      "--max-attempts 次新尝试；不会复用失败请求的幂等键"
    ),
  )
  args = parser.parse_args()
  if args.batch_size <= 0 or args.batch_size > 500:
    parser.error("--batch-size 必须在 1..500")
  if args.code_limit is not None and args.code_limit <= 0:
    parser.error("--code-limit 必须大于 0")
  if args.poll_seconds <= 0 or args.poll_seconds > 60:
    parser.error("--poll-seconds 必须在 0..60")
  if args.timeout_seconds <= 0:
    parser.error("--timeout-seconds 必须大于 0")
  if args.max_attempts <= 0:
    parser.error("--max-attempts 必须大于 0")
  if args.max_jobs is not None and args.max_jobs <= 0:
    parser.error("--max-jobs 必须大于 0")
  return args


if __name__ == "__main__":
  raise SystemExit(asyncio.run(run(parse_args())))
