"""Resumable full-universe QMT dividend-factor backfill.

This operational script only creates durable ``market_data_request`` rows.
XTData remains isolated in the outbound, data-only QMT Agent.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
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
from quantx_worker.prefector.flows.divid_factor_sync_flow import (
  divid_factor_sync_flow,
)
from quantx_worker.prefector.flows.durable_agent_flows import (
  _normalize_divid_factor_records,
)
from sqlalchemy import select, text

SCHEMA_VERSION = 2
OPERATION_VERSION = "qmt-get-divid-factors-v1"
BENCHMARK_CODE = "000300.SH"
_A_SHARE_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
ROOT = Path(__file__).resolve().parents[3]
TRANSFER_ROOT = (ROOT / ".runtime" / "market-data").resolve()
DEFAULT_STATE_DIRECTORY = ROOT / ".runtime" / "research-backfill"
ACTIVE_REQUEST_STATES = {
  "QUEUED",
  "DELIVERED",
  "RECEIVING",
  "UPLOADED",
  "PROCESSING",
}
DATA_ONLY_READY_STATUSES = {"READY", "RECONCILING"}
# Intentionally shared with backfill_daily_market_data.py. A factor campaign
# cannot race the daily-bar campaign for the one serial XTData request worker.
CAMPAIGN_LOCK_KEY = int.from_bytes(
  hashlib.sha256(b"quantx:qmt-daily-history-backfill").digest()[:8],
  byteorder="big",
  signed=True,
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")


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
    try:
      await self.connection.scalar(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": CAMPAIGN_LOCK_KEY},
      )
    finally:
      await self.connection.close()
      self.connection = None


def _date(value: str) -> date:
  compact = str(value or "").strip().replace("-", "")
  if len(compact) != 8 or not compact.isdigit():
    raise argparse.ArgumentTypeError("日期必须是 YYYYMMDD 或 YYYY-MM-DD")
  return datetime.strptime(compact, "%Y%m%d").date()


def _compact(value: date) -> str:
  return value.strftime("%Y%m%d")


def _now_iso() -> str:
  return datetime.now().astimezone().isoformat()


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
          Instrument.open_date,
          Instrument.expire_date,
        )
        .where(Instrument.type == InstrumentType.STOCK)
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

  stock_codes = []
  for code, open_date, expire_date in rows:
    normalized = str(code or "").strip().upper()
    if not _A_SHARE_CODE_PATTERN.fullmatch(normalized):
      continue
    if open_date is not None and open_date > end:
      continue
    if expire_date is not None and expire_date < start:
      continue
    stock_codes.append(normalized)
  stock_codes = sorted(dict.fromkeys(stock_codes))
  if code_limit is not None:
    stock_codes = stock_codes[:code_limit]
  if not stock_codes:
    raise RuntimeError("PostgreSQL 中没有研究窗口内的沪深股票标的")
  codes = sorted([*stock_codes, BENCHMARK_CODE])
  return codes, {
    "stock_count": len(stock_codes),
    "requested_code_count": len(codes),
    "benchmark_code": BENCHMARK_CODE,
    "code_sha256": _code_hash(codes),
  }


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
    if item.get("status") not in DATA_ONLY_READY_STATUSES:
      continue
    details = item.get("details") or {}
    capabilities = set(details.get("capabilities") or [])
    if not {"market-data", "divid-factors", "data-only"}.issubset(capabilities):
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
      "data-only/market-data/divid-factors 的 QMT Agent；"
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


def _read_transfer_records(
  request: dict[str, Any],
  manifest: list[dict[str, Any]],
) -> list[dict[str, Any]]:
  expected = int(request.get("expected_chunks") or 0)
  if expected <= 0 or len(manifest) != expected:
    raise RuntimeError(
      f"复权因子分片不完整: expected={expected} actual={len(manifest)}"
    )
  indexes = [int(item["chunk_index"]) for item in manifest]
  if indexes != list(range(expected)):
    raise RuntimeError("复权因子分片序号不连续")

  records: list[dict[str, Any]] = []
  for item in manifest:
    path = Path(str(item["storage_reference"])).resolve()
    if not path.is_relative_to(TRANSFER_ROOT):
      raise RuntimeError(f"分片路径越过运行目录: {path}")
    compressed = path.read_bytes()
    digest = hashlib.sha256(compressed).hexdigest()
    if digest != str(item["checksum_sha256"]):
      raise RuntimeError(f"复权因子分片校验和不匹配: {path.name}")
    raw = gzip.decompress(compressed) if item.get("compressed") else compressed
    chunk = json.loads(raw.decode("utf-8"))
    if not isinstance(chunk, list):
      raise RuntimeError(f"复权因子分片不是数组: {path.name}")
    if len(chunk) != int(item["record_count"]):
      raise RuntimeError(f"复权因子分片记录数不匹配: {path.name}")
    if not all(isinstance(record, dict) for record in chunk):
      raise RuntimeError(f"复权因子分片包含非对象记录: {path.name}")
    records.extend(chunk)
  return records


def _decimal(value: Any, places: Decimal) -> Decimal:
  return Decimal(str(value)).quantize(places, rounding=ROUND_HALF_UP)


def _expected_database_rows(
  records: list[dict[str, Any]],
) -> list[tuple[Any, ...]]:
  expected = []
  for record in records:
    factor_time = (
      datetime.fromtimestamp(
        float(record["time"]) / 1000,
        tz=timezone.utc,
      )
      .astimezone(SHANGHAI)
      .replace(tzinfo=None)
    )
    expected.append(
      (
        str(record["code"]),
        factor_time,
        str(record["ex_date"]),
        _decimal(record["interest"], FOUR_PLACES),
        _decimal(record["stockBonus"], FOUR_PLACES),
        _decimal(record["stockGift"], FOUR_PLACES),
        _decimal(record["allotNum"], FOUR_PLACES),
        _decimal(record["allotPrice"], FOUR_PLACES),
        _decimal(record["gugai"], FOUR_PLACES),
        _decimal(record["dr"], SIX_PLACES),
      )
    )
  return sorted(expected, key=lambda row: (row[0], row[2]))


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
  records = _read_transfer_records(request, manifest)
  _normalize_divid_factor_records(records, actual_payload)
  expected_rows = _expected_database_rows(records)

  codes = sorted(expected_payload["stock_list"])
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
  actual_rows = [
    (
      str(row[0]),
      row[1].replace(tzinfo=None),
      str(row[2]),
      Decimal(row[3]),
      Decimal(row[4]),
      Decimal(row[5]),
      Decimal(row[6]),
      Decimal(row[7]),
      Decimal(row[8]),
      Decimal(row[9]),
    )
    for row in persisted
  ]
  if actual_rows != expected_rows:
    raise RuntimeError(
      "PostgreSQL 复权因子精确行验收失败: "
      f"source={len(expected_rows)} persisted={len(actual_rows)}"
    )

  source_codes = sorted({str(record["code"]) for record in records})
  ex_dates = sorted(str(record["ex_date"]) for record in records)
  canonical_records = sorted(
    records,
    key=lambda record: (str(record["code"]), str(record["ex_date"])),
  )
  return {
    "request_id": request_id,
    "source_record_count": len(records),
    "persisted_record_count": len(actual_rows),
    "requested_code_count": len(codes),
    "source_code_count": len(source_codes),
    "codes_without_events": len(codes) - len(source_codes),
    "min_ex_date": ex_dates[0] if ex_dates else "",
    "max_ex_date": ex_dates[-1] if ex_dates else "",
    "source_sha256": _sha256_json(canonical_records),
    "persisted_sha256": _sha256_json(actual_rows),
    "expected_chunks": int(request.get("expected_chunks") or 0),
    "transferred_record_count": sum(int(item["record_count"]) for item in manifest),
    "verified_at": _now_iso(),
  }


def _refresh_summary(state: dict[str, Any]) -> None:
  jobs = state["jobs"]
  state["summary"] = {
    "total_jobs": len(jobs),
    "completed_jobs": sum(job["status"] == "completed" for job in jobs),
    "pending_jobs": sum(job["status"] == "pending" for job in jobs),
    "running_jobs": sum(job["status"] == "running" for job in jobs),
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
    return state

  codes, universe = await load_universe(
    start=args.start_date,
    end=args.end_date,
    code_limit=args.code_limit,
  )
  identity = {
    "operation_version": OPERATION_VERSION,
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
    (job for job in state["jobs"] if job["status"] in {"pending", "running"}),
    None,
  )


def _failed_jobs(state: dict[str, Any]) -> list[dict[str, Any]]:
  return [job for job in state["jobs"] if job["status"] == "failed"]


def _campaign_incomplete_error(state: dict[str, Any]) -> str:
  incomplete = [job for job in state["jobs"] if job["status"] != "completed"]
  if not incomplete:
    return ""
  counts: dict[str, int] = {}
  for job in incomplete:
    status = str(job.get("status") or "missing")
    counts[status] = counts.get(status, 0) + 1
  rendered = ", ".join(
    f"{status}={count}" for status, count in sorted(counts.items())
  )
  return (
    "复权因子 campaign 仍有未完成作业，拒绝标记 completed: "
    f"{rendered}"
  )


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
  for job in _failed_jobs(state):
    next_attempt = int(job.get("attempt") or 0)
    previous_limit = int(job.get("attempt_limit") or next_attempt)
    attempt_limit = next_attempt + max_attempts
    history = job.setdefault("retry_history", [])
    history.append(
      {
        "requested_at": requested_at,
        "previous_status": "failed",
        "next_attempt": next_attempt,
        "previous_attempt_limit": previous_limit,
        "attempt_limit": attempt_limit,
        "last_error": str(job.get("last_error") or "")[:2000],
      }
    )
    job["status"] = "pending"
    job["attempt_limit"] = attempt_limit
    job["retry_requested_at"] = requested_at
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


async def run(args: argparse.Namespace) -> int:
  if args.end_date < args.start_date:
    raise RuntimeError("结束日期不能早于开始日期")
  state_path = (
    Path(args.state_file).resolve()
    if args.state_file
    else (
      DEFAULT_STATE_DIRECTORY
      / (
        "full-a-share-divid-factors-"
        f"{_compact(args.start_date)}-{_compact(args.end_date)}.json"
      )
    ).resolve()
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
          "stock_count": state["universe"]["stock_count"],
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
        _persist_state(state_path, state, status="completed")
        return 0
      if args.max_jobs is not None and processed >= args.max_jobs:
        _persist_state(state_path, state, status="paused")
        return 0

      payload = request_payload(state, job)
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
      job["status"] = "running"
      job["agent_device_id"] = device_id
      job["request_key"] = payload["request_key"]
      job["started_at"] = _now_iso()
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
        if str(result.get("status")) != "completed":
          raise RuntimeError(
            f"复权因子请求未成功: {result.get('status')}/{result.get('reason', '')}"
          )
        audit = await verify_completed_request(
          request_id=str(result["request_id"]),
          expected_payload=payload,
        )
      except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
        job["last_error"] = error[:2000]
        job.setdefault("failure_history", []).append(
          {
            "attempt": int(job.get("attempt") or 0),
            "request_key": str(payload["request_key"]),
            "failed_at": _now_iso(),
            "error": error[:2000],
          }
        )
        job["attempt"] = int(job.get("attempt") or 0) + 1
        attempt_limit = int(job.get("attempt_limit") or args.max_attempts)
        if job["attempt"] >= attempt_limit:
          job["status"] = "failed"
          job["failed_at"] = _now_iso()
          _persist_state(
            state_path,
            state,
            status="failed",
            error=error,
          )
          return 2
        job["status"] = "pending"
        _persist_state(state_path, state, error=error)
        await asyncio.sleep(args.poll_seconds)
        continue

      job["status"] = "completed"
      job["request_id"] = audit["request_id"]
      job["audit"] = audit
      job["finished_at"] = _now_iso()
      job.pop("last_error", None)
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
    description="通过 data-only QMT Agent 可恢复回填沪深股票复权因子"
  )
  parser.add_argument("--start-date", required=True, type=_date)
  parser.add_argument("--end-date", required=True, type=_date)
  parser.add_argument("--batch-size", type=int, default=200)
  parser.add_argument("--code-limit", type=int)
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
