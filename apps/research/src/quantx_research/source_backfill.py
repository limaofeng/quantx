"""Build an immutable research-only archive from durable QMT bar transfers.

This command is intentionally independent from the normal InfluxDB ingestion
flow.  It asks the existing QMT Agent for missing daily-bar requests through
``market_data_request``, waits only for the durable upload, validates the
PostgreSQL manifest and every gzip chunk, and publishes an immutable archive.

The original daily-backfill state file is read-only.  A newly uploaded request
is marked ``FAILED`` *after* its archive and ledger are durable so normal
workers cannot mistake source-only completion for successful Influx ingestion.
QMT Agent remains the only process that imports or calls XTData.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quantx_infrastructure import DurableRuntimeStore
from sqlalchemy import text

from .data.qmt_archive_source import (
  QMT_DAILY_BAR_ARCHIVE_FORMAT,
  QMT_DAILY_BAR_ARCHIVE_SCHEMA_VERSION,
)

_ACTIVE_REQUEST_STATES = {
  "QUEUED",
  "DELIVERED",
  "RECEIVING",
  "UPLOADED",
  "PROCESSING",
}
_ARCHIVABLE_REQUEST_STATES = {"UPLOADED", "COMPLETED", "FAILED"}
_DATA_ONLY_READY_STATUSES = {"READY", "RECONCILING"}
_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
_SOURCE_ONLY_TERMINAL_PREFIX = "SOURCE_ONLY_ARCHIVED_NO_INFLUX"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MAX_COMPRESSED_CHUNK_BYTES = 64 * 1024**2
_MAX_UNCOMPRESSED_CHUNK_BYTES = 256 * 1024**2
_MAX_CHUNK_RECORDS = 100_000
_CAMPAIGN_LOCK_KEY = int.from_bytes(
  hashlib.sha256(b"quantx:qmt-daily-history-backfill").digest()[:8],
  byteorder="big",
  signed=True,
)


class SourceBackfillError(RuntimeError):
  """Source transfer, archive, or campaign evidence failed closed."""


def _now_iso() -> str:
  return datetime.now().astimezone().isoformat()


def _canonical_json_bytes(value: Any) -> bytes:
  return json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    default=str,
  ).encode("utf-8")


def _canonical_json_sha256(value: Any) -> str:
  return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _text_sha256(value: str) -> str:
  return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for block in iter(lambda: handle.read(1024 * 1024), b""):
      digest.update(block)
  return digest.hexdigest()


def _request_payload(job: Mapping[str, Any]) -> dict[str, Any]:
  return {
    "operation": "bars",
    "download": True,
    "stock_list": list(job["codes"]),
    "periods": ["1d"],
    "start_time": str(job["start_date"]),
    "end_time": str(job["end_date"]),
  }


def resolve_market_data_root(value: str | Path | None = None) -> Path:
  """Resolve the API-owned canonical durable transfer root."""
  if value is not None and str(value).strip():
    root = Path(value).expanduser()
  elif os.environ.get("QUANTX_RUNTIME_DIR"):
    root = Path(os.environ["QUANTX_RUNTIME_DIR"]).expanduser() / "market-data"
  else:
    workspace = (
      Path(os.environ["QUANTX_ROOT"]).expanduser()
      if os.environ.get("QUANTX_ROOT")
      else Path(__file__).resolve().parents[4]
    )
    root = workspace / ".runtime" / "market-data"
  lexical = root.absolute()
  _reject_symlink_chain(lexical)
  try:
    resolved = lexical.resolve(strict=True)
  except OSError as exc:
    raise SourceBackfillError(
      f"canonical market-data root 不可访问: {lexical}"
    ) from exc
  if not resolved.is_dir() or resolved.is_symlink():
    raise SourceBackfillError(
      f"canonical market-data root 不是普通目录: {resolved}"
    )
  return resolved


def _parse_compact_date(value: Any, label: str) -> date:
  try:
    return datetime.strptime(str(value), "%Y%m%d").date()
  except ValueError as exc:
    raise SourceBackfillError(f"{label} 必须是 YYYYMMDD") from exc


def _timestamp_millis(value: Any) -> int:
  if isinstance(value, bool):
    raise SourceBackfillError("QMT 日线 time 不能是布尔值")
  if isinstance(value, (int, float)):
    numeric = float(value)
    if not math.isfinite(numeric):
      raise SourceBackfillError("QMT 日线 time 不是有限数")
    return int(numeric)
  if isinstance(value, datetime):
    normalized = (
      value.replace(tzinfo=timezone.utc)
      if value.tzinfo is None
      else value.astimezone(timezone.utc)
    )
    return int(normalized.timestamp() * 1000)
  try:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  except ValueError as exc:
    raise SourceBackfillError(f"QMT 日线 time 无法解析: {value!r}") from exc
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
  return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _local_compact_date(time_ms: int) -> str:
  try:
    return (
      datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)
      .astimezone(_SHANGHAI)
      .strftime("%Y%m%d")
    )
  except (OSError, OverflowError, ValueError) as exc:
    raise SourceBackfillError(
      f"QMT 日线 time 超出可用范围: {time_ms}"
    ) from exc


def _decoded_payload(value: Any) -> dict[str, Any]:
  if isinstance(value, str):
    try:
      value = json.loads(value)
    except json.JSONDecodeError as exc:
      raise SourceBackfillError(
        "market_data_request payload 不是有效 JSON"
      ) from exc
  if not isinstance(value, dict):
    raise SourceBackfillError(
      "market_data_request payload 根节点必须是 object"
    )
  return dict(value)


def _recorded_request_id(job: Mapping[str, Any]) -> str | None:
  candidates: set[str] = set()
  audit = job.get("request_audit")
  if isinstance(audit, dict) and audit.get("request_id"):
    candidates.add(str(audit["request_id"]))
  for item in job.get("ingestion_retry_history") or []:
    if isinstance(item, dict) and item.get("request_id"):
      candidates.add(str(item["request_id"]))
  if len(candidates) > 1:
    raise SourceBackfillError(
      f"任务 {job.get('id')} 记录了多个 market-data request_id"
    )
  return next(iter(candidates), None)


def _validated_jobs(state: Mapping[str, Any]) -> list[dict[str, Any]]:
  raw_jobs = state.get("jobs")
  if not isinstance(raw_jobs, list) or not raw_jobs:
    raise SourceBackfillError("原日线账本 jobs 不能为空")

  jobs: list[dict[str, Any]] = []
  job_ids: set[str] = set()
  intervals_by_code: dict[str, list[tuple[date, date, str]]] = {}
  for raw in raw_jobs:
    if not isinstance(raw, dict):
      raise SourceBackfillError("原日线账本包含非法 job")
    if str(raw.get("status") or "") == "superseded":
      continue
    job_id = str(raw.get("id") or "").strip()
    if not job_id or job_id in job_ids:
      raise SourceBackfillError(f"原日线账本 job_id 非法或重复: {job_id!r}")
    job_ids.add(job_id)
    kind = str(raw.get("kind") or "")
    if kind != "benchmark" and not kind.startswith("stocks"):
      raise SourceBackfillError(f"任务 {job_id} kind 不受支持: {kind!r}")
    raw_codes = raw.get("codes")
    if not isinstance(raw_codes, list) or not raw_codes:
      raise SourceBackfillError(f"任务 {job_id} codes 不能为空")
    codes = sorted(
      {
        str(code).strip().upper()
        for code in raw_codes
        if str(code).strip()
      }
    )
    if len(codes) != len(raw_codes):
      raise SourceBackfillError(
        f"任务 {job_id} codes 含空值、重复值或非 canonical 排序"
      )
    if codes != raw_codes:
      raise SourceBackfillError(f"任务 {job_id} codes 必须升序且大写")
    invalid = [code for code in codes if not _CODE_PATTERN.fullmatch(code)]
    if invalid:
      raise SourceBackfillError(
        f"任务 {job_id} 含非沪深代码: {invalid[:5]}"
      )
    start = _parse_compact_date(raw.get("start_date"), f"{job_id}.start_date")
    end = _parse_compact_date(raw.get("end_date"), f"{job_id}.end_date")
    if end < start:
      raise SourceBackfillError(f"任务 {job_id} 日期倒置")
    for code in codes:
      intervals_by_code.setdefault(code, []).append((start, end, job_id))
    jobs.append(
      {
        **raw,
        "id": job_id,
        "kind": kind,
        "codes": codes,
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "recorded_request_id": _recorded_request_id(raw),
      }
    )

  for code, intervals in intervals_by_code.items():
    ordered = sorted(intervals)
    for left, right in zip(ordered, ordered[1:]):
      if right[0] <= left[1]:
        raise SourceBackfillError(
          "有效任务包含重叠 code/date coverage: "
          f"{code} {left[2]} {right[2]}"
        )
  return jobs


def load_campaign(state_path: Path) -> dict[str, Any]:
  """Load an existing daily-backfill state without modifying it."""
  try:
    state_bytes = state_path.read_bytes()
    state = json.loads(state_bytes.decode("utf-8"))
  except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SourceBackfillError("原日线账本不可读或不是有效 JSON") from exc
  if not isinstance(state, dict):
    raise SourceBackfillError("原日线账本根节点必须是 object")
  jobs = _validated_jobs(state)
  universe = state.get("universe")
  if not isinstance(universe, dict):
    raise SourceBackfillError("原日线账本缺少 universe")
  universe_sha256 = str(universe.get("code_sha256") or "")
  if not re.fullmatch(r"[0-9a-f]{64}", universe_sha256):
    raise SourceBackfillError("原日线账本 universe.code_sha256 非法")
  run_key = str(state.get("run_key") or "").strip()
  if not run_key:
    raise SourceBackfillError("原日线账本 run_key 不能为空")
  start_date = _parse_compact_date(
    state.get("start_date"), "campaign.start_date"
  ).strftime("%Y%m%d")
  end_date = _parse_compact_date(
    state.get("end_date"), "campaign.end_date"
  ).strftime("%Y%m%d")
  job_plan = sorted(
    (
      {
        "job_id": job["id"],
        "kind": job["kind"],
        "codes": job["codes"],
        "start_date": job["start_date"],
        "end_date": job["end_date"],
      }
      for job in jobs
    ),
    key=lambda item: item["job_id"],
  )
  return {
    "source_state_path": str(state_path),
    "source_state_sha256_at_load": hashlib.sha256(state_bytes).hexdigest(),
    "run_key": run_key,
    "start_date": start_date,
    "end_date": end_date,
    "universe_sha256": universe_sha256,
    "job_plan_sha256": _canonical_json_sha256(job_plan),
    "jobs": jobs,
  }


def _campaign_identity(campaign: Mapping[str, Any]) -> dict[str, Any]:
  return {
    "run_key": str(campaign["run_key"]),
    "start_date": str(campaign["start_date"]),
    "end_date": str(campaign["end_date"]),
    "universe_sha256": str(campaign["universe_sha256"]),
    "job_plan_sha256": str(campaign["job_plan_sha256"]),
  }


def _reject_symlink_chain(path: Path, *, stop: Path | None = None) -> None:
  current = path.absolute()
  boundary = stop.absolute() if stop is not None else None
  while True:
    if current.exists() and current.is_symlink():
      raise SourceBackfillError(f"archive 路径不能经过符号链接: {current}")
    if boundary is not None and current == boundary:
      break
    parent = current.parent
    if parent == current:
      break
    current = parent


def _prepare_archive_root(path: Path) -> Path:
  lexical = path.absolute()
  _reject_symlink_chain(lexical)
  lexical.mkdir(parents=True, exist_ok=True)
  if lexical.is_symlink() or not lexical.is_dir():
    raise SourceBackfillError(f"archive root 不是普通目录: {lexical}")
  return lexical.resolve(strict=True)


def _archive_root_for_dry_run(path: Path) -> Path:
  """Resolve an archive location without creating it."""
  lexical = path.absolute()
  _reject_symlink_chain(lexical)
  if lexical.exists():
    resolved = lexical.resolve(strict=True)
    if not resolved.is_dir() or resolved.is_symlink():
      raise SourceBackfillError(
        f"archive root 不是普通目录: {resolved}"
      )
    return resolved
  return lexical


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex[:8]}.tmp")
  payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
  try:
    with temporary.open("xb") as handle:
      handle.write(payload)
      handle.flush()
      os.fsync(handle.fileno())
    for attempt in range(8):
      try:
        os.replace(temporary, path)
        break
      except PermissionError:
        if attempt == 7:
          raise
        # Windows readers may briefly hold the destination without delete
        # sharing.  Keep the fully fsynced temp file and retry the same atomic
        # replacement instead of weakening the ledger write.
        time.sleep(min(0.05 * (2**attempt), 0.5))
  finally:
    temporary.unlink(missing_ok=True)


def _publish_directory(source: Path, destination: Path) -> None:
  """Atomically publish a prepared request directory on Windows.

  Antivirus/indexer readers can briefly open one of the freshly written files
  without delete sharing, which makes the directory rename fail with WinError
  5 even though the destination does not exist.  Retrying the same atomic
  rename keeps the request invisible until the complete directory is ready.
  """
  for attempt in range(8):
    try:
      os.replace(source, destination)
      return
    except PermissionError:
      if destination.exists():
        raise SourceBackfillError(
          f"archive request 目录发布冲突: {destination.name}"
        )
      if attempt == 7:
        raise
      time.sleep(min(0.05 * (2**attempt), 0.5))


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
  payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
  with path.open("xb") as handle:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())


def _write_bytes(path: Path, value: bytes) -> None:
  with path.open("xb") as handle:
    handle.write(value)
    handle.flush()
    os.fsync(handle.fileno())


def _ledger_template(campaign: Mapping[str, Any]) -> dict[str, Any]:
  return {
    "schema_version": QMT_DAILY_BAR_ARCHIVE_SCHEMA_VERSION,
    "archive_format": QMT_DAILY_BAR_ARCHIVE_FORMAT,
    "status": "in_progress",
    "expected_request_count": len(campaign["jobs"]),
    "effective_job_count": len(campaign["jobs"]),
    "job_plan_sha256": str(campaign["job_plan_sha256"]),
    "created_at": _now_iso(),
    "updated_at": _now_iso(),
    "campaign": _campaign_identity(campaign),
    "source_state": {
      "path": str(campaign["source_state_path"]),
      "sha256_at_load": str(campaign["source_state_sha256_at_load"]),
    },
    "source_transport": {
      "kind": "market_data_request",
      "market_data_root": str(campaign.get("market_data_root") or ""),
    },
    "summary": {
      "request_count": 0,
      "chunk_count": 0,
      "record_count": 0,
    },
    "requests": [],
  }


def _load_ledger(
  archive_root: Path,
  campaign: Mapping[str, Any],
) -> dict[str, Any]:
  ledger_path = archive_root / "ledger.json"
  if not ledger_path.exists():
    return _ledger_template(campaign)
  _reject_symlink_chain(ledger_path, stop=archive_root)
  try:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
  except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SourceBackfillError("archive ledger 不是有效 JSON") from exc
  if not isinstance(ledger, dict):
    raise SourceBackfillError("archive ledger 根节点必须是 object")
  if ledger.get("archive_format") != QMT_DAILY_BAR_ARCHIVE_FORMAT:
    raise SourceBackfillError("archive ledger format 不匹配")
  if int(ledger.get("schema_version") or 0) != (
    QMT_DAILY_BAR_ARCHIVE_SCHEMA_VERSION
  ):
    raise SourceBackfillError("archive ledger schema_version 不匹配")
  if ledger.get("campaign") != _campaign_identity(campaign):
    raise SourceBackfillError("archive ledger campaign identity 不匹配")
  if int(ledger.get("expected_request_count") or 0) != len(campaign["jobs"]):
    raise SourceBackfillError("archive ledger expected_request_count 不匹配")
  if int(ledger.get("effective_job_count") or 0) != len(campaign["jobs"]):
    raise SourceBackfillError("archive ledger effective_job_count 不匹配")
  if str(ledger.get("job_plan_sha256") or "") != str(
    campaign["job_plan_sha256"]
  ):
    raise SourceBackfillError("archive ledger job_plan_sha256 不匹配")
  expected_source_transport = {
    "kind": "market_data_request",
    "market_data_root": str(campaign.get("market_data_root") or ""),
  }
  if ledger.get("source_transport") != expected_source_transport:
    raise SourceBackfillError("archive ledger source_transport 不匹配")
  if not isinstance(ledger.get("requests"), list):
    raise SourceBackfillError("archive ledger requests 必须是 array")
  known_job_ids = {str(job["id"]) for job in campaign["jobs"]}
  observed_job_ids: set[str] = set()
  observed_request_ids: set[str] = set()
  for entry in ledger["requests"]:
    if not isinstance(entry, dict):
      raise SourceBackfillError("archive ledger 含非 object request entry")
    job_id = str(entry.get("job_id") or "")
    request_id = str(entry.get("request_id") or "")
    if job_id not in known_job_ids:
      raise SourceBackfillError(
        f"archive ledger 含任务计划外 job_id: {job_id!r}"
      )
    if job_id in observed_job_ids or request_id in observed_request_ids:
      raise SourceBackfillError(
        "archive ledger 含重复 job_id 或 request_id"
      )
    observed_job_ids.add(job_id)
    observed_request_ids.add(request_id)
  return ledger


def _refresh_ledger(ledger: dict[str, Any]) -> None:
  requests = ledger["requests"]
  ledger["updated_at"] = _now_iso()
  ledger["summary"] = {
    "request_count": len(requests),
    "chunk_count": sum(len(item["chunks"]) for item in requests),
    "record_count": sum(int(item["record_count"]) for item in requests),
  }
  ledger["status"] = (
    "completed"
    if len(requests) == int(ledger["expected_request_count"])
    else "in_progress"
  )


def _bounded_gzip_decompress(compressed: bytes, label: str) -> bytes:
  if len(compressed) > _MAX_COMPRESSED_CHUNK_BYTES:
    raise SourceBackfillError(f"QMT chunk 压缩大小超限: {label}")
  try:
    with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as handle:
      raw = handle.read(_MAX_UNCOMPRESSED_CHUNK_BYTES + 1)
  except (OSError, EOFError) as exc:
    raise SourceBackfillError(f"QMT chunk gzip 损坏: {label}") from exc
  if len(raw) > _MAX_UNCOMPRESSED_CHUNK_BYTES:
    raise SourceBackfillError(f"QMT chunk 解压后大小超限: {label}")
  return raw


def _safe_source_path(
  item: Mapping[str, Any],
  *,
  source_root: Path,
  request_id: str,
  chunk_index: int,
) -> Path:
  path = Path(str(item.get("storage_reference") or "")).absolute()
  expected_name = f"{chunk_index:08d}.json.gz"
  if path.name != expected_name or path.parent.name != request_id:
    raise SourceBackfillError(
      "QMT transfer storage_reference 非 canonical: "
      f"{request_id}/{chunk_index}"
    )
  _reject_symlink_chain(path)
  try:
    resolved = path.resolve(strict=True)
  except OSError as exc:
    raise SourceBackfillError(f"QMT chunk 不可访问: {path}") from exc
  if not resolved.is_file() or resolved.is_symlink():
    raise SourceBackfillError(f"QMT chunk 不是普通文件: {resolved}")
  try:
    relative = resolved.relative_to(source_root)
  except ValueError as exc:
    raise SourceBackfillError(
      f"QMT chunk 逃逸 canonical market-data root: {resolved}"
    ) from exc
  expected_relative = Path(request_id) / expected_name
  if relative != expected_relative:
    raise SourceBackfillError(
      "QMT chunk 不位于 canonical request 目录: "
      f"expected={expected_relative} actual={relative}"
    )
  return resolved


def _validated_record(
  raw: Any,
  *,
  request_id: str,
  requested_codes: set[str],
  start_date: str,
  end_date: str,
) -> tuple[str, int]:
  if not isinstance(raw, dict):
    raise SourceBackfillError(
      f"QMT chunk 含非 object 记录: {request_id}"
    )
  code = str(raw.get("code") or "").strip().upper()
  if code not in requested_codes or raw.get("code") != code:
    raise SourceBackfillError(
      f"QMT chunk 含请求外或非 canonical 标的: {request_id} {code!r}"
    )
  if raw.get("period") != "1d":
    raise SourceBackfillError(
      f"QMT chunk 含非日线周期: {request_id} {raw.get('period')!r}"
    )
  if raw.get("time") is None:
    raise SourceBackfillError(f"QMT chunk 记录缺少 time: {request_id} {code}")
  time_ms = _timestamp_millis(raw["time"])
  compact_date = _local_compact_date(time_ms)
  if compact_date < start_date or compact_date > end_date:
    raise SourceBackfillError(
      f"QMT chunk 记录超出请求日期: {request_id} {code}@{time_ms}"
    )
  source_index = raw.get("index")
  if source_index not in (None, "") and str(source_index) != compact_date:
    raise SourceBackfillError(
      f"QMT chunk index/time 不一致: {request_id} {code}@{time_ms}"
    )
  required = ("open", "high", "low", "close", "volume", "amount")
  missing = [field for field in required if field not in raw]
  if missing:
    raise SourceBackfillError(
      f"QMT chunk 缺少研究字段: {request_id} {code} {missing}"
    )
  if "suspendFlag" not in raw and "suspend_flag" not in raw:
    raise SourceBackfillError(
      f"QMT chunk 缺少 suspend flag: {request_id} {code}"
    )
  return code, time_ms


def _validate_existing_request_entry(
  archive_root: Path,
  expected: Mapping[str, Any],
) -> dict[str, Any]:
  request_id = str(expected["request_id"])
  manifest_path = archive_root / "requests" / request_id / "manifest.json"
  _reject_symlink_chain(manifest_path, stop=archive_root)
  try:
    entry = json.loads(manifest_path.read_text(encoding="utf-8"))
  except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SourceBackfillError(
      f"已发布 request manifest 不可读: {request_id}"
    ) from exc
  if not isinstance(entry, dict):
    raise SourceBackfillError(f"request manifest 非 object: {request_id}")
  identity_fields = (
    "job_id",
    "request_id",
    "kind",
    "codes",
    "start_date",
    "end_date",
    "payload",
    "payload_sha256",
  )
  mismatched = [
    field for field in identity_fields if entry.get(field) != expected.get(field)
  ]
  if mismatched:
    raise SourceBackfillError(
      f"已发布 request manifest identity 不匹配: {request_id} {mismatched}"
    )
  for chunk in entry.get("chunks") or []:
    relative = str(chunk.get("path") or "")
    path = archive_root.joinpath(*relative.split("/"))
    _reject_symlink_chain(path, stop=archive_root)
    if not path.is_file():
      raise SourceBackfillError(f"已发布 archive chunk 缺失: {relative}")
    if path.stat().st_size != int(chunk.get("bytes") or 0):
      raise SourceBackfillError(f"已发布 archive chunk 字节数变化: {relative}")
    if _file_sha256(path) != str(chunk.get("sha256") or ""):
      raise SourceBackfillError(f"已发布 archive chunk SHA256 变化: {relative}")
  return entry


def archive_request(
  *,
  archive_root: Path,
  source_root: Path,
  job: Mapping[str, Any],
  request: Mapping[str, Any],
  manifest: Sequence[Mapping[str, Any]],
  publish: bool = True,
) -> dict[str, Any]:
  """Validate and optionally atomically publish one durable transfer request."""
  request_id = str(request.get("request_id") or "")
  try:
    if str(uuid.UUID(request_id)) != request_id:
      raise ValueError
  except (ValueError, AttributeError) as exc:
    raise SourceBackfillError(f"request_id 非 canonical UUID: {request_id!r}") from exc
  payload = _request_payload(job)
  actual_payload = _decoded_payload(request.get("request_payload"))
  if actual_payload != payload:
    raise SourceBackfillError(
      f"request payload 与任务不一致: {request_id}"
    )
  status = str(request.get("status") or "")
  if status not in _ARCHIVABLE_REQUEST_STATES:
    raise SourceBackfillError(
      f"request 尚不可归档: {request_id} status={status}"
    )
  expected_chunks = int(request.get("expected_chunks") or 0)
  received_chunks = int(request.get("received_chunks") or 0)
  if (
    expected_chunks <= 0
    or expected_chunks != received_chunks
    or len(manifest) != expected_chunks
  ):
    raise SourceBackfillError(
      "QMT transfer 分片不完整: "
      f"{request_id} expected={expected_chunks} "
      f"received={received_chunks} actual={len(manifest)}"
    )
  indices = [int(item.get("chunk_index", -1)) for item in manifest]
  if indices != list(range(expected_chunks)):
    raise SourceBackfillError(
      f"QMT transfer chunk index 不连续: {request_id} {indices}"
    )

  expected_entry = {
    "job_id": str(job["id"]),
    "request_id": request_id,
    "kind": str(job["kind"]),
    "codes": list(job["codes"]),
    "start_date": str(job["start_date"]),
    "end_date": str(job["end_date"]),
    "payload": payload,
    "payload_sha256": _canonical_json_sha256(payload),
  }
  target_directory = archive_root / "requests" / request_id
  if publish and target_directory.exists():
    return _validate_existing_request_entry(archive_root, expected_entry)

  staging: Path | None = None
  if publish:
    requests_directory = archive_root / "requests"
    staging_root = archive_root / ".staging"
    requests_directory.mkdir(parents=True, exist_ok=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    _reject_symlink_chain(requests_directory, stop=archive_root)
    _reject_symlink_chain(staging_root, stop=archive_root)
    for _ in range(5):
      candidate = staging_root / uuid.uuid4().hex[:12]
      try:
        candidate.mkdir()
      except FileExistsError:
        continue
      staging = candidate
      break
    if staging is None:
      raise SourceBackfillError("无法分配唯一 archive staging 目录")

  requested_codes = set(job["codes"])
  seen_keys: set[tuple[str, int]] = set()
  key_lines: list[str] = []
  observed_codes: set[str] = set()
  archived_chunks: list[dict[str, Any]] = []
  record_count = 0
  try:
    for expected_index, item in enumerate(manifest):
      if item.get("compressed") is not True:
        raise SourceBackfillError(
          f"QMT transfer chunk 不是 gzip: {request_id}/{expected_index}"
        )
      expected_records = int(item.get("record_count") or 0)
      if expected_records <= 0 or expected_records > _MAX_CHUNK_RECORDS:
        raise SourceBackfillError(
          f"QMT transfer chunk records 非法: {request_id}/{expected_index}"
        )
      source = _safe_source_path(
        item,
        source_root=source_root,
        request_id=request_id,
        chunk_index=expected_index,
      )
      compressed = source.read_bytes()
      digest = hashlib.sha256(compressed).hexdigest()
      if digest != str(item.get("checksum_sha256") or ""):
        raise SourceBackfillError(
          f"QMT transfer chunk SHA256 不匹配: {request_id}/{expected_index}"
        )
      raw = _bounded_gzip_decompress(
        compressed,
        f"{request_id}/{expected_index}",
      )
      try:
        records = json.loads(raw.decode("utf-8"))
      except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceBackfillError(
          f"QMT transfer chunk 非 UTF-8 JSON: {request_id}/{expected_index}"
        ) from exc
      if not isinstance(records, list) or len(records) != expected_records:
        raise SourceBackfillError(
          f"QMT transfer chunk record_count 不匹配: "
          f"{request_id}/{expected_index}"
        )
      for raw_record in records:
        key = _validated_record(
          raw_record,
          request_id=request_id,
          requested_codes=requested_codes,
          start_date=str(job["start_date"]),
          end_date=str(job["end_date"]),
        )
        if key in seen_keys:
          raise SourceBackfillError(
            f"QMT transfer 含重复日线键: {request_id} {key[0]}@{key[1]}"
          )
        seen_keys.add(key)
        observed_codes.add(key[0])
        key_lines.append(f"{key[0]}|{key[1]}")
        record_count += 1

      if staging is not None:
        destination = staging / f"{expected_index:08d}.json.gz"
        _write_bytes(destination, compressed)
        if _file_sha256(destination) != digest:
          raise SourceBackfillError(
            "archive chunk 写后 SHA256 不匹配: "
            f"{request_id}/{expected_index}"
          )
      archived_chunks.append(
        {
          "index": expected_index,
          "path": (
            f"requests/{request_id}/{expected_index:08d}.json.gz"
          ),
          "sha256": digest,
          "records": expected_records,
          "compressed": True,
          "bytes": len(compressed),
        }
      )
    if record_count <= 0 or not observed_codes:
      raise SourceBackfillError(f"QMT transfer 没有日线记录: {request_id}")
    if sum(int(item["records"]) for item in archived_chunks) != record_count:
      raise SourceBackfillError(
        f"QMT transfer 总记录数与 manifest 不一致: {request_id}"
      )

    entry = {
      **expected_entry,
      "expected_chunks": expected_chunks,
      "received_chunks": received_chunks,
      "record_count": record_count,
      "symbol_count": len(observed_codes),
      "source_key_sha256": _text_sha256("\n".join(sorted(key_lines))),
      "archived_at": _now_iso(),
      "source": {
        "transport": "market_data_request",
        "request_status_before_archive": status,
        "database_manifest_sha256": _canonical_json_sha256(
          [
            {
              "chunk_index": int(item["chunk_index"]),
              "checksum_sha256": str(item["checksum_sha256"]),
              "record_count": int(item["record_count"]),
              "compressed": bool(item["compressed"]),
            }
            for item in manifest
          ]
        ),
      },
      "chunks": archived_chunks,
    }
    if staging is None:
      return entry
    _write_json(staging / "manifest.json", entry)
    if target_directory.exists():
      raise SourceBackfillError(
        f"archive request 目录发布冲突: {request_id}"
      )
    _publish_directory(staging, target_directory)
    staging = None
    return _validate_existing_request_entry(archive_root, expected_entry)
  except Exception:
    # A staging directory is deliberately never published or referenced by the
    # ledger.  Remove only this UUID-scoped directory created by this process.
    if staging is not None and staging.exists():
      shutil.rmtree(staging)
    raise


def publish_ledger_entry(
  *,
  archive_root: Path,
  ledger: dict[str, Any],
  entry: Mapping[str, Any],
) -> None:
  requests = ledger["requests"]
  request_id = str(entry["request_id"])
  job_id = str(entry["job_id"])
  by_request = {
    str(item.get("request_id")): item for item in requests if isinstance(item, dict)
  }
  by_job = {
    str(item.get("job_id")): item for item in requests if isinstance(item, dict)
  }
  if request_id in by_request:
    if by_request[request_id] != entry:
      raise SourceBackfillError(
        f"ledger request entry 与 manifest 不一致: {request_id}"
      )
    return
  if job_id in by_job:
    raise SourceBackfillError(
      f"ledger job_id 已绑定不同 request: {job_id}"
    )
  requests.append(dict(entry))
  _refresh_ledger(ledger)
  _atomic_write_json(archive_root / "ledger.json", ledger)


async def _find_request_by_idempotency(
  store: DurableRuntimeStore,
  payload: Mapping[str, Any],
) -> str | None:
  key = _canonical_json_sha256(payload)
  async with store.engine.connect() as connection:
    value = (
      await connection.execute(
        text(
          """
          SELECT request_id
          FROM market_data_request
          WHERE idempotency_key = :idempotency_key
          """
        ),
        {"idempotency_key": key},
      )
    ).scalar_one_or_none()
  return str(value) if value is not None else None


async def _active_requests(
  store: DurableRuntimeStore,
) -> list[dict[str, str]]:
  async with store.engine.connect() as connection:
    rows = (
      await connection.execute(
        text(
          """
          SELECT request_id, status, idempotency_key
          FROM market_data_request
          WHERE status IN (
            'QUEUED', 'DELIVERED', 'RECEIVING', 'UPLOADED', 'PROCESSING'
          )
          ORDER BY created_at
          """
        )
      )
    ).mappings()
    return [
      {
        "request_id": str(row["request_id"]),
        "status": str(row["status"]),
        "idempotency_key": str(row["idempotency_key"]),
      }
      for row in rows
    ]


async def _data_only_agent(
  store: DurableRuntimeStore,
  *,
  max_age_seconds: float,
) -> str:
  statuses = await store.component_status("qmt-agent:")
  now = datetime.now(timezone.utc)
  candidates: list[tuple[datetime, str]] = []
  for item in statuses:
    if str(item.get("status") or "") not in _DATA_ONLY_READY_STATUSES:
      continue
    details = item.get("details") or {}
    capabilities = {
      str(value).strip().lower()
      for value in details.get("capabilities") or []
    }
    if not {"market-data", "data-only"}.issubset(capabilities):
      continue
    updated_at = item.get("updated_at")
    if not isinstance(updated_at, datetime):
      continue
    if updated_at.tzinfo is None:
      updated_at = updated_at.replace(tzinfo=timezone.utc)
    updated_at = updated_at.astimezone(timezone.utc)
    if abs((now - updated_at).total_seconds()) > max_age_seconds:
      continue
    device_id = str(item.get("instance_id") or "").strip()
    if device_id:
      candidates.append((updated_at, device_id))
  if not candidates:
    raise SourceBackfillError(
      "没有新鲜且具备 data-only/market-data 能力的 QMT Agent"
    )
  return max(candidates)[1]


async def _wait_for_unrelated_queue(
  store: DurableRuntimeStore,
  *,
  allowed_request_id: str | None,
  poll_seconds: float,
  timeout_seconds: float,
) -> None:
  deadline = asyncio.get_running_loop().time() + timeout_seconds
  while True:
    active = await _active_requests(store)
    blockers = [
      item
      for item in active
      if str(item["request_id"]) != str(allowed_request_id or "")
    ]
    if not blockers:
      return
    if asyncio.get_running_loop().time() >= deadline:
      summary = ", ".join(
        f"{item['request_id']}:{item['status']}" for item in blockers
      )
      raise SourceBackfillError(
        f"QMT 行情队列被无关请求占用且超时: {summary}"
      )
    await asyncio.sleep(poll_seconds)


async def _wait_for_archivable_request(
  store: DurableRuntimeStore,
  request_id: str,
  *,
  poll_seconds: float,
  timeout_seconds: float,
) -> dict[str, Any]:
  deadline = asyncio.get_running_loop().time() + timeout_seconds
  while True:
    request = await store.market_data_request(request_id)
    if request is None:
      raise SourceBackfillError(f"market-data request 消失: {request_id}")
    status = str(request.get("status") or "")
    if status in _ARCHIVABLE_REQUEST_STATES:
      return request
    if status not in _ACTIVE_REQUEST_STATES:
      raise SourceBackfillError(
        f"market-data request 状态不受支持: {request_id} {status}"
      )
    if asyncio.get_running_loop().time() >= deadline:
      raise SourceBackfillError(
        f"等待 QMT source upload 超时: {request_id} status={status}"
      )
    await asyncio.sleep(poll_seconds)


async def _reconcile_archived_terminal(
  store: DurableRuntimeStore,
  entry: Mapping[str, Any],
  *,
  archive_root: Path,
) -> None:
  request_id = str(entry["request_id"])
  request = await store.market_data_request(request_id)
  if request is None:
    raise SourceBackfillError(
      f"已归档 request 在 PostgreSQL 中消失: {request_id}"
    )
  if _decoded_payload(request.get("request_payload")) != entry.get("payload"):
    raise SourceBackfillError(
      f"已归档 request payload 在 PostgreSQL 中不匹配: {request_id}"
    )
  status = str(request.get("status") or "")
  if status in {"COMPLETED", "FAILED"}:
    return
  if status != "UPLOADED":
    raise SourceBackfillError(
      f"已归档 request 尚未安全终结: {request_id} status={status}"
    )
  manifest_path = archive_root / "requests" / request_id / "manifest.json"
  manifest_sha256 = _file_sha256(manifest_path)
  await store.finish_market_data_request(
    request_id,
    status="FAILED",
    error=(
      f"{_SOURCE_ONLY_TERMINAL_PREFIX}: archive verified; "
      "no Influx ingestion was attempted; "
      f"manifest_sha256={manifest_sha256}"
    ),
  )
  terminal = await store.market_data_request_status(request_id)
  if terminal != "FAILED":
    raise SourceBackfillError(
      f"source-only request 未收敛为 FAILED: {request_id} {terminal}"
    )


async def _process_job(
  store: DurableRuntimeStore,
  *,
  archive_root: Path,
  source_root: Path,
  ledger: dict[str, Any],
  job: Mapping[str, Any],
  poll_seconds: float,
  request_timeout_seconds: float,
  queue_timeout_seconds: float,
  agent_max_age_seconds: float,
  existing_only: bool,
  dry_run: bool,
) -> str:
  existing_entries = {
    str(item.get("job_id")): item
    for item in ledger["requests"]
    if isinstance(item, dict)
  }
  if str(job["id"]) in existing_entries:
    entry = _validate_existing_request_entry(
      archive_root,
      existing_entries[str(job["id"])],
    )
    if entry != existing_entries[str(job["id"])]:
      raise SourceBackfillError(
        f"ledger 与 request manifest 不一致: {job['id']}"
      )
    if not dry_run:
      await _reconcile_archived_terminal(
        store,
        entry,
        archive_root=archive_root,
      )
    return "reconciled"

  payload = _request_payload(job)
  request_id = await _find_request_by_idempotency(store, payload)
  recorded_request_id = str(job.get("recorded_request_id") or "")
  if request_id and recorded_request_id and request_id != recorded_request_id:
    raise SourceBackfillError(
      f"任务 {job['id']} 的 PostgreSQL request_id 与原账本不一致"
    )
  if request_id is None:
    if existing_only or dry_run:
      return "pending"
    await _wait_for_unrelated_queue(
      store,
      allowed_request_id=None,
      poll_seconds=poll_seconds,
      timeout_seconds=queue_timeout_seconds,
    )
    device_id = await _data_only_agent(
      store,
      max_age_seconds=agent_max_age_seconds,
    )
    request_id = await store.create_market_data_request(
      payload,
      device_id=device_id,
    )
  if dry_run:
    request = await store.market_data_request(request_id)
    if request is None:
      raise SourceBackfillError(
        f"market-data request 消失: {request_id}"
      )
    if str(request.get("status") or "") not in _ARCHIVABLE_REQUEST_STATES:
      return "active"
  else:
    await _wait_for_unrelated_queue(
      store,
      allowed_request_id=request_id,
      poll_seconds=poll_seconds,
      timeout_seconds=queue_timeout_seconds,
    )
    request = await _wait_for_archivable_request(
      store,
      request_id,
      poll_seconds=poll_seconds,
      timeout_seconds=request_timeout_seconds,
    )
  manifest = await store.market_data_transfers(request_id)
  request_with_id = {**request, "request_id": request_id}
  entry = await asyncio.to_thread(
    archive_request,
    archive_root=archive_root,
    source_root=source_root,
    job=job,
    request=request_with_id,
    manifest=manifest,
    publish=not dry_run,
  )
  if dry_run:
    return "validated"
  publish_ledger_entry(
    archive_root=archive_root,
    ledger=ledger,
    entry=entry,
  )
  await _reconcile_archived_terminal(
    store,
    entry,
    archive_root=archive_root,
  )
  return "archived"


class _CampaignLock:
  def __init__(self, store: DurableRuntimeStore) -> None:
    self._store = store
    self._connection = None

  async def __aenter__(self) -> "_CampaignLock":
    self._connection = await self._store.engine.connect()
    acquired = await self._connection.scalar(
      text("SELECT pg_try_advisory_lock(:lock_key)"),
      {"lock_key": _CAMPAIGN_LOCK_KEY},
    )
    if not acquired:
      await self._connection.close()
      self._connection = None
      raise SourceBackfillError(
        "另一个 QMT 日线回填或 source-backfill 正在运行"
      )
    return self

  async def __aexit__(self, exc_type, exc, traceback) -> None:
    if self._connection is None:
      return
    try:
      await self._connection.scalar(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": _CAMPAIGN_LOCK_KEY},
      )
    finally:
      await self._connection.close()
      self._connection = None


async def run(args: argparse.Namespace) -> int:
  state_path = Path(args.state_file).resolve(strict=True)
  state_before = _file_sha256(state_path)
  campaign = load_campaign(state_path)
  archive_root = (
    _archive_root_for_dry_run(Path(args.archive_root))
    if args.dry_run
    else _prepare_archive_root(Path(args.archive_root))
  )
  source_root = resolve_market_data_root(args.market_data_root)
  campaign["market_data_root"] = str(source_root)
  ledger = _load_ledger(archive_root, campaign)
  store = DurableRuntimeStore()
  processed = 0
  outcomes: dict[str, int] = {}
  try:
    async with _CampaignLock(store):
      for job in campaign["jobs"]:
        if args.max_jobs is not None and processed >= args.max_jobs:
          break
        outcome = await _process_job(
          store,
          archive_root=archive_root,
          source_root=source_root,
          ledger=ledger,
          job=job,
          poll_seconds=args.poll_seconds,
          request_timeout_seconds=args.request_timeout_seconds,
          queue_timeout_seconds=args.queue_timeout_seconds,
          agent_max_age_seconds=args.agent_max_age_seconds,
          existing_only=args.existing_only,
          dry_run=args.dry_run,
        )
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome == "pending" and args.existing_only and not args.dry_run:
          continue
        processed += 1
        print(
          json.dumps(
            {
              "event": f"source_{outcome}",
              "job_id": job["id"],
              "processed": processed,
              "expected": len(campaign["jobs"]),
              "ledger": str(archive_root / "ledger.json"),
            },
            ensure_ascii=False,
          ),
          flush=True,
        )
      _refresh_ledger(ledger)
      if ledger["requests"] and not args.dry_run:
        _atomic_write_json(archive_root / "ledger.json", ledger)
  finally:
    await store.close()
  if _file_sha256(state_path) != state_before:
    raise SourceBackfillError(
      "原日线账本在 source-backfill 运行期间发生变化"
    )
  if args.dry_run:
    print(
      json.dumps(
        {
          "event": "source_dry_run_completed",
          "effective_jobs": len(campaign["jobs"]),
          "outcomes": outcomes,
          "source_state_sha256": state_before,
          "job_plan_sha256": campaign["job_plan_sha256"],
          "market_data_root": str(source_root),
          "archive_root": str(archive_root),
          "writes_performed": False,
        },
        ensure_ascii=False,
      ),
      flush=True,
    )
  return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description=(
      "通过 QMT durable transfer 构建不依赖 InfluxDB 的研究日线 archive"
    ),
  )
  parser.add_argument("--state-file", required=True)
  parser.add_argument("--archive-root", required=True)
  parser.add_argument(
    "--market-data-root",
    help=(
      "API canonical durable transfer root；默认与 "
      "QUANTX_RUNTIME_DIR/QUANTX_ROOT 解析规则一致"
    ),
  )
  parser.add_argument("--poll-seconds", type=float, default=2.0)
  parser.add_argument("--request-timeout-seconds", type=float, default=1200.0)
  parser.add_argument("--queue-timeout-seconds", type=float, default=1200.0)
  parser.add_argument("--agent-max-age-seconds", type=float, default=90.0)
  parser.add_argument("--max-jobs", type=int)
  parser.add_argument(
    "--existing-only",
    action="store_true",
    help="只归档 PostgreSQL 已有的完整 requests，不创建新的 QMT 请求",
  )
  parser.add_argument(
    "--dry-run",
    action="store_true",
    help=(
      "只读验证原账本、现有 requests/chunks 和既有 archive；"
      "不创建请求、不复制文件、不修改 PostgreSQL"
    ),
  )
  args = parser.parse_args(argv)
  for name in (
    "poll_seconds",
    "request_timeout_seconds",
    "queue_timeout_seconds",
    "agent_max_age_seconds",
  ):
    if getattr(args, name) <= 0:
      parser.error(f"--{name.replace('_', '-')} 必须大于 0")
  if args.max_jobs is not None and args.max_jobs <= 0:
    parser.error("--max-jobs 必须大于 0")
  return args


def main(argv: Sequence[str] | None = None) -> int:
  return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
  raise SystemExit(main())


__all__ = [
  "SourceBackfillError",
  "archive_request",
  "load_campaign",
  "main",
  "parse_args",
  "publish_ledger_entry",
  "resolve_market_data_root",
  "run",
]
