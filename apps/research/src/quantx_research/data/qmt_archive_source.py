"""Audited read-only research source for archived QMT daily-bar chunks.

The archive is produced from the durable QMT transfer spool.  PostgreSQL
remains authoritative for instrument metadata, dividend factors and factor
coverage; only daily bars are read from the immutable archive.  This keeps the
normal InfluxDB path available while making a completed QMT source transfer
independently usable when InfluxDB ingestion is unavailable.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .normalization import as_datetime, normalize_daily_bars, normalize_instruments
from .source import ResearchDataSource

QMT_DAILY_BAR_ARCHIVE_FORMAT = "quantx-qmt-daily-bars-source-v1"
QMT_DAILY_BAR_ARCHIVE_SCHEMA_VERSION = 1
FULL_A_SHARE_REQUIRED_REQUEST_COUNT = 180

_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_COMPRESSED_CHUNK_BYTES = 64 * 1024**2
_MAX_UNCOMPRESSED_CHUNK_BYTES = 256 * 1024**2
_MAX_CHUNK_RECORDS = 100_000
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_PRICE_COLUMNS = ("open", "high", "low", "close")
_FLOW_COLUMNS = ("volume", "amount")


class QmtDailyBarArchiveError(RuntimeError):
  """The archive is incomplete, mutable or inconsistent with its ledger."""


@dataclass(frozen=True, slots=True)
class _ArchiveChunk:
  index: int
  relative_path: str
  sha256: str
  records: int
  compressed_bytes: int


@dataclass(frozen=True, slots=True)
class _ArchiveRequest:
  raw: dict[str, Any]
  job_id: str
  request_id: str
  kind: str
  codes: tuple[str, ...]
  start_date: date
  end_date: date
  record_count: int
  symbol_count: int
  source_key_sha256: str
  chunks: tuple[_ArchiveChunk, ...]

  @property
  def code_set(self) -> frozenset[str]:
    return frozenset(self.codes)


class QmtDailyBarArchiveResearchDataSource:
  """Read bars from a verified QMT archive and delegate relational reads.

  The ledger and every selected request manifest/chunk are revalidated by the
  research process.  An archive that changes while the process is running is
  rejected instead of yielding a mixed snapshot.
  """

  def __init__(
    self,
    archive: str | Path,
    *,
    metadata_source: ResearchDataSource,
    boundary_tolerance_days: int = 7,
    required_request_count: int = FULL_A_SHARE_REQUIRED_REQUEST_COUNT,
  ) -> None:
    if boundary_tolerance_days < 0:
      raise ValueError("boundary_tolerance_days 不能小于 0")
    if required_request_count <= 0:
      raise ValueError("required_request_count 必须大于 0")
    self._metadata_source = metadata_source
    self._boundary_tolerance_days = boundary_tolerance_days
    self._required_request_count = required_request_count
    self._ledger_path = _resolve_ledger_path(archive)
    ledger_bytes = self._ledger_path.read_bytes()
    self._ledger_sha256 = _bytes_sha256(ledger_bytes)
    try:
      ledger = json.loads(ledger_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
      raise QmtDailyBarArchiveError("QMT 行情 archive ledger 不是有效 UTF-8 JSON") from exc
    self._archive_root = self._ledger_path.parent.resolve(strict=True)
    self._ledger, self._campaign, self._requests = _validate_ledger(
      ledger,
      archive_root=self._archive_root,
      required_request_count=required_request_count,
    )
    self._request_by_id = {
      request.request_id: request for request in self._requests
    }
    self._selected_request_ids: set[str] = set()
    self._manifest_file_sha256: dict[str, str] = {}
    self._queries: list[dict[str, Any]] = []
    self._emitted_rows = 0
    self._universe_validated = False
    self._instrument_lifecycle: dict[
      str, tuple[date | None, date | None]
    ] = {}

  async def list_instruments(
    self,
    *,
    instrument_types: Sequence[str] = ("stock",),
    codes: Sequence[str] | None = None,
  ) -> pd.DataFrame:
    frame = await self._metadata_source.list_instruments(
      instrument_types=instrument_types,
      codes=codes,
    )
    normalized = normalize_instruments(frame)
    for row in normalized.itertuples(index=False):
      code = str(row.stock_code or "").strip().upper()
      if not _CODE_PATTERN.fullmatch(code):
        continue
      self._instrument_lifecycle[code] = (
        _optional_date(row.open_date),
        _optional_date(row.expire_date),
      )
    if codes is None and any(
      str(value).strip().lower() == "stock" for value in instrument_types
    ):
      stock_codes = sorted(
        code
        for code in normalized["stock_code"].dropna().astype(str)
        if _CODE_PATTERN.fullmatch(code)
      )
      observed = _code_sha256(stock_codes)
      expected = str(self._campaign["universe_sha256"])
      if observed != expected:
        raise QmtDailyBarArchiveError(
          "QMT 行情 archive 的证券总体与 PostgreSQL 当前股票总体不一致: "
          f"archive={expected} postgres={observed}"
        )
      self._universe_validated = True
    return frame

  async def load_daily_bars(
    self,
    stock_codes: Sequence[str],
    start: date | datetime,
    end: date | datetime,
    *,
    batch_size: int = 300,
  ) -> pd.DataFrame:
    if batch_size <= 0:
      raise ValueError("batch_size 必须大于 0")
    self._ensure_ledger_unchanged()
    codes = _unique_codes(stock_codes)
    if not codes:
      return normalize_daily_bars(None)
    invalid_codes = [code for code in codes if not _CODE_PATTERN.fullmatch(code)]
    if invalid_codes:
      raise ValueError(
        "QMT archive 日线请求含非法沪深代码: " + ", ".join(invalid_codes)
      )
    start_date = as_datetime(start).date()
    end_date = as_datetime(end).date()
    if end_date < start_date:
      raise ValueError("日线查询结束时间不能早于开始时间")

    requested_codes = frozenset(codes)
    selected = tuple(
      request
      for request in self._requests
      if not requested_codes.isdisjoint(request.code_set)
      and request.start_date <= end_date
      and request.end_date >= start_date
    )
    coverage = _validate_query_coverage(
      codes,
      selected,
      requested_start=start_date,
      requested_end=end_date,
      boundary_tolerance_days=self._boundary_tolerance_days,
      instrument_lifecycle=self._instrument_lifecycle,
    )

    rows: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, int]] = set()
    for request in selected:
      records, manifest_sha256 = self._read_verified_request(request)
      self._selected_request_ids.add(request.request_id)
      self._manifest_file_sha256[request.request_id] = manifest_sha256
      for record in records:
        code = str(record["code"])
        if code not in requested_codes:
          continue
        time_ms = int(record["time_ms"])
        local_date = _millis_to_local_date(time_ms)
        if local_date < start_date or local_date > end_date:
          continue
        key = (code, time_ms)
        if key in selected_keys:
          raise QmtDailyBarArchiveError(
            "QMT 行情 archive 在所选 requests 间包含重复日线键: "
            f"{code}@{time_ms}"
          )
        selected_keys.add(key)
        rows.append(
          {
            "stock_code": code,
            "time": time_ms,
            "open": record.get("open"),
            "high": record.get("high"),
            "low": record.get("low"),
            "close": record.get("close"),
            "volume": record.get("volume"),
            "amount": record.get("amount"),
            "suspend_flag": record.get("suspend_flag"),
          }
        )

    self._emitted_rows += len(rows)
    self._queries.append(
      {
        "requested_start": start_date.isoformat(),
        "requested_end": end_date.isoformat(),
        "requested_code_count": len(codes),
        "requested_codes_sha256": _code_sha256(sorted(codes)),
        "selected_request_count": len(selected),
        "available_start": coverage["available_start"],
        "available_end": coverage["available_end"],
        "boundary_truncated": coverage["boundary_truncated"],
        "lifecycle_adjusted_code_count": coverage[
          "lifecycle_adjusted_code_count"
        ],
        "emitted_rows": len(rows),
      }
    )
    if not rows:
      return normalize_daily_bars(None)
    return _normalize_worker_compatible_bars(rows)

  async def load_dividend_factors(
    self,
    stock_codes: Sequence[str],
    *,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
  ) -> pd.DataFrame:
    return await self._metadata_source.load_dividend_factors(
      stock_codes,
      start=start,
      end=end,
    )

  async def load_dividend_factor_coverage(
    self,
    stock_codes: Sequence[str],
    *,
    start: date | datetime,
    end: date | datetime,
  ) -> pd.DataFrame:
    return await self._metadata_source.load_dividend_factor_coverage(
      stock_codes,
      start=start,
      end=end,
    )

  @property
  def provenance(self) -> dict[str, Any]:
    """Return compact evidence for the run manifest and data-quality artifact."""
    self._ensure_ledger_unchanged()
    selected_requests = [
      self._request_by_id[request_id]
      for request_id in sorted(self._selected_request_ids)
    ]
    request_evidence = []
    chunk_evidence_lines: list[str] = []
    for request in selected_requests:
      request_evidence.append(
        {
          "job_id": request.job_id,
          "request_id": request.request_id,
          "kind": request.kind,
          "start_date": request.start_date.isoformat(),
          "end_date": request.end_date.isoformat(),
          "code_count": len(request.codes),
          "codes_sha256": _code_sha256(list(request.codes)),
          "record_count": request.record_count,
          "source_key_sha256": request.source_key_sha256,
          "manifest_file_sha256": self._manifest_file_sha256.get(
            request.request_id
          ),
        }
      )
      chunk_evidence_lines.extend(
        f"{request.request_id}|{chunk.index}|{chunk.sha256}"
        for chunk in request.chunks
      )
    return {
      "kind": "qmt-daily-bar-archive",
      "archive_format": QMT_DAILY_BAR_ARCHIVE_FORMAT,
      "schema_version": QMT_DAILY_BAR_ARCHIVE_SCHEMA_VERSION,
      "ledger_path": str(self._ledger_path),
      "ledger_sha256": self._ledger_sha256,
      "campaign": dict(self._campaign),
      "metadata_universe_validated": self._universe_validated,
      "preprocessing": {
        "compatibility": "quantx-worker-preprocess-market-data",
        "price_decimals": 3,
        "volume_amount_decimals": 2,
        "timezone": "Asia/Shanghai",
      },
      "boundary_tolerance_days": self._boundary_tolerance_days,
      "required_request_count": self._required_request_count,
      "selected_request_count": len(selected_requests),
      "selected_chunk_count": sum(
        len(request.chunks) for request in selected_requests
      ),
      "selected_source_record_count": sum(
        request.record_count for request in selected_requests
      ),
      "selected_chunk_manifest_sha256": _text_sha256(
        "\n".join(chunk_evidence_lines)
      ),
      "emitted_rows": self._emitted_rows,
      "queries": list(self._queries),
      "requests": request_evidence,
    }

  def _read_verified_request(
    self,
    request: _ArchiveRequest,
  ) -> tuple[list[dict[str, Any]], str]:
    manifest_relative = (
      f"requests/{request.request_id}/manifest.json"
    )
    manifest_path = _safe_archive_file(
      self._archive_root,
      manifest_relative,
      label=f"request manifest {request.request_id}",
    )
    manifest_bytes = manifest_path.read_bytes()
    try:
      manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
      raise QmtDailyBarArchiveError(
        f"QMT request manifest 不是有效 JSON: {request.request_id}"
      ) from exc
    if manifest != request.raw:
      raise QmtDailyBarArchiveError(
        "QMT request manifest 与 archive ledger 条目不一致: "
        f"{request.request_id}"
      )

    records: list[dict[str, Any]] = []
    key_strings: list[str] = []
    keys: set[tuple[str, int]] = set()
    observed_codes: set[str] = set()
    for chunk in request.chunks:
      path = _safe_archive_file(
        self._archive_root,
        chunk.relative_path,
        label=f"chunk {request.request_id}/{chunk.index}",
      )
      compressed = path.read_bytes()
      if len(compressed) != chunk.compressed_bytes:
        raise QmtDailyBarArchiveError(
          f"QMT chunk 字节数不匹配: {chunk.relative_path}"
        )
      if _bytes_sha256(compressed) != chunk.sha256:
        raise QmtDailyBarArchiveError(
          f"QMT chunk SHA256 不匹配: {chunk.relative_path}"
        )
      raw = _bounded_gzip_decompress(compressed, chunk.relative_path)
      try:
        payload = json.loads(raw.decode("utf-8"))
      except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QmtDailyBarArchiveError(
          f"QMT chunk 不是有效 UTF-8 JSON: {chunk.relative_path}"
        ) from exc
      if not isinstance(payload, list):
        raise QmtDailyBarArchiveError(
          f"QMT chunk 根节点不是 array: {chunk.relative_path}"
        )
      if len(payload) != chunk.records:
        raise QmtDailyBarArchiveError(
          f"QMT chunk 记录数不匹配: {chunk.relative_path}"
        )
      for raw_record in payload:
        record = _validate_record(raw_record, request=request)
        code = str(record["code"])
        time_ms = int(record["time_ms"])
        key = (code, time_ms)
        if key in keys:
          raise QmtDailyBarArchiveError(
            "QMT request 包含重复日线键: "
            f"{request.request_id} {code}@{time_ms}"
          )
        keys.add(key)
        observed_codes.add(code)
        key_strings.append(f"{code}|{time_ms}")
        records.append(record)

    if len(records) != request.record_count:
      raise QmtDailyBarArchiveError(
        "QMT request 总记录数不匹配: "
        f"{request.request_id} ledger={request.record_count} "
        f"actual={len(records)}"
      )
    if len(observed_codes) != request.symbol_count:
      raise QmtDailyBarArchiveError(
        "QMT request 标的数不匹配: "
        f"{request.request_id} ledger={request.symbol_count} "
        f"actual={len(observed_codes)}"
      )
    observed_source_key_sha256 = _text_sha256(
      "\n".join(sorted(key_strings))
    )
    if observed_source_key_sha256 != request.source_key_sha256:
      raise QmtDailyBarArchiveError(
        "QMT request 日线键指纹不匹配: "
        f"{request.request_id}"
      )
    return records, _bytes_sha256(manifest_bytes)

  def _ensure_ledger_unchanged(self) -> None:
    try:
      current = _file_sha256(self._ledger_path)
    except OSError as exc:
      raise QmtDailyBarArchiveError(
        "QMT 行情 archive ledger 在研究期间不可访问"
      ) from exc
    if current != self._ledger_sha256:
      raise QmtDailyBarArchiveError(
        "QMT 行情 archive ledger 在研究期间发生变化"
      )


def describe_qmt_daily_bar_archive(archive: str | Path) -> dict[str, Any]:
  """Return immutable ledger identity before a run directory is created."""
  ledger_path = _resolve_ledger_path(archive)
  return {
    "kind": "qmt-daily-bar-archive",
    "archive_format": QMT_DAILY_BAR_ARCHIVE_FORMAT,
    "ledger_path": str(ledger_path),
    "ledger_sha256": _file_sha256(ledger_path),
  }


def _validate_ledger(
  ledger: Any,
  *,
  archive_root: Path,
  required_request_count: int,
) -> tuple[dict[str, Any], dict[str, Any], tuple[_ArchiveRequest, ...]]:
  if not isinstance(ledger, dict):
    raise QmtDailyBarArchiveError("QMT archive ledger 根节点必须是 object")
  if int(ledger.get("schema_version") or 0) != (
    QMT_DAILY_BAR_ARCHIVE_SCHEMA_VERSION
  ):
    raise QmtDailyBarArchiveError(
      f"不支持的 QMT archive schema_version: {ledger.get('schema_version')}"
    )
  if ledger.get("archive_format") != QMT_DAILY_BAR_ARCHIVE_FORMAT:
    raise QmtDailyBarArchiveError("QMT archive_format 不匹配")
  if ledger.get("status") != "completed":
    raise QmtDailyBarArchiveError(
      "QMT archive 尚未 completed；正式研究拒绝读取部分 archive"
    )
  expected_request_count = _positive_int(
    ledger.get("expected_request_count"),
    "QMT archive expected_request_count",
  )
  effective_job_count = _positive_int(
    ledger.get("effective_job_count"),
    "QMT archive effective_job_count",
  )
  if (
    expected_request_count != required_request_count
    or effective_job_count != required_request_count
  ):
    raise QmtDailyBarArchiveError(
      "QMT archive 全量任务门禁不匹配: "
      f"required={required_request_count} "
      f"expected={expected_request_count} effective={effective_job_count}"
    )
  job_plan_sha256 = _require_sha256(
    ledger.get("job_plan_sha256"),
    "QMT archive job_plan_sha256",
  )
  campaign_raw = ledger.get("campaign")
  if not isinstance(campaign_raw, dict):
    raise QmtDailyBarArchiveError("QMT archive ledger 缺少 campaign")
  campaign_start = _parse_compact_date(
    campaign_raw.get("start_date"),
    "campaign.start_date",
  )
  campaign_end = _parse_compact_date(
    campaign_raw.get("end_date"),
    "campaign.end_date",
  )
  if campaign_end < campaign_start:
    raise QmtDailyBarArchiveError("QMT archive campaign 日期倒置")
  universe_sha256 = _require_sha256(
    campaign_raw.get("universe_sha256"),
    "campaign.universe_sha256",
  )
  run_key = str(campaign_raw.get("run_key") or "").strip()
  if not run_key:
    raise QmtDailyBarArchiveError("QMT archive campaign.run_key 不能为空")
  campaign = {
    **campaign_raw,
    "run_key": run_key,
    "start_date": campaign_start.strftime("%Y%m%d"),
    "end_date": campaign_end.strftime("%Y%m%d"),
    "universe_sha256": universe_sha256,
  }

  raw_requests = ledger.get("requests")
  if not isinstance(raw_requests, list) or not raw_requests:
    raise QmtDailyBarArchiveError("QMT archive ledger requests 不能为空")
  if len(raw_requests) != expected_request_count:
    raise QmtDailyBarArchiveError(
      "QMT archive requests 未达到完整门禁: "
      f"expected={expected_request_count} actual={len(raw_requests)}"
    )
  requests: list[_ArchiveRequest] = []
  request_ids: set[str] = set()
  job_ids: set[str] = set()
  intervals_by_code: dict[str, list[tuple[date, date, str]]] = {}
  for raw in raw_requests:
    request = _validate_request(
      raw,
      archive_root=archive_root,
      campaign_start=campaign_start,
      campaign_end=campaign_end,
    )
    if request.request_id in request_ids:
      raise QmtDailyBarArchiveError(
        f"QMT archive 包含重复 request_id: {request.request_id}"
      )
    if request.job_id in job_ids:
      raise QmtDailyBarArchiveError(
        f"QMT archive 包含重复 job_id: {request.job_id}"
      )
    request_ids.add(request.request_id)
    job_ids.add(request.job_id)
    requests.append(request)
    for code in request.codes:
      intervals_by_code.setdefault(code, []).append(
        (request.start_date, request.end_date, request.request_id)
      )

  for code, intervals in intervals_by_code.items():
    ordered = sorted(intervals)
    for left, right in zip(ordered, ordered[1:]):
      if right[0] <= left[1]:
        raise QmtDailyBarArchiveError(
          "QMT archive 存在重叠 code/date coverage: "
          f"{code} {left[2]} {right[2]}"
        )
  expected_plan = [
    {
      "job_id": request.job_id,
      "kind": request.kind,
      "codes": list(request.codes),
      "start_date": request.start_date.strftime("%Y%m%d"),
      "end_date": request.end_date.strftime("%Y%m%d"),
    }
    for request in sorted(requests, key=lambda item: item.job_id)
  ]
  if _canonical_json_sha256(expected_plan) != job_plan_sha256:
    raise QmtDailyBarArchiveError("QMT archive job_plan_sha256 不匹配")
  summary = ledger.get("summary")
  if not isinstance(summary, dict):
    raise QmtDailyBarArchiveError("QMT archive ledger 缺少 summary")
  summary_request_count = _positive_int(
    summary.get("request_count"),
    "QMT archive summary.request_count",
  )
  summary_chunk_count = _positive_int(
    summary.get("chunk_count"),
    "QMT archive summary.chunk_count",
  )
  summary_record_count = _positive_int(
    summary.get("record_count"),
    "QMT archive summary.record_count",
  )
  actual_chunk_count = sum(len(request.chunks) for request in requests)
  actual_record_count = sum(request.record_count for request in requests)
  if (
    summary_request_count != len(requests)
    or summary_chunk_count != actual_chunk_count
    or summary_record_count != actual_record_count
  ):
    raise QmtDailyBarArchiveError(
      "QMT archive summary 与 requests 汇总不一致: "
      f"requests={summary_request_count}/{len(requests)} "
      f"chunks={summary_chunk_count}/{actual_chunk_count} "
      f"records={summary_record_count}/{actual_record_count}"
    )
  return dict(ledger), campaign, tuple(requests)


def _validate_request(
  raw: Any,
  *,
  archive_root: Path,
  campaign_start: date,
  campaign_end: date,
) -> _ArchiveRequest:
  if not isinstance(raw, dict):
    raise QmtDailyBarArchiveError("QMT archive request 必须是 object")
  request_id = _canonical_uuid(raw.get("request_id"))
  job_id = str(raw.get("job_id") or "").strip()
  if not job_id:
    raise QmtDailyBarArchiveError(f"QMT request 缺少 job_id: {request_id}")
  kind = str(raw.get("kind") or "")
  if kind != "benchmark" and not kind.startswith("stocks"):
    raise QmtDailyBarArchiveError(
      f"QMT request kind 不支持: {request_id} {kind!r}"
    )
  raw_codes = raw.get("codes")
  if not isinstance(raw_codes, list) or not raw_codes:
    raise QmtDailyBarArchiveError(f"QMT request codes 不能为空: {request_id}")
  codes = tuple(str(code) for code in raw_codes)
  if codes != tuple(sorted(set(codes))):
    raise QmtDailyBarArchiveError(
      f"QMT request codes 必须 canonical、排序且不重复: {request_id}"
    )
  invalid_codes = [code for code in codes if not _CODE_PATTERN.fullmatch(code)]
  if invalid_codes:
    raise QmtDailyBarArchiveError(
      f"QMT request 含非法沪深代码: {request_id} {invalid_codes}"
    )
  start_date = _parse_compact_date(raw.get("start_date"), "request.start_date")
  end_date = _parse_compact_date(raw.get("end_date"), "request.end_date")
  if end_date < start_date:
    raise QmtDailyBarArchiveError(f"QMT request 日期倒置: {request_id}")
  if start_date < campaign_start or end_date > campaign_end:
    raise QmtDailyBarArchiveError(
      f"QMT request 超出 campaign 日期范围: {request_id}"
    )

  expected_payload = {
    "operation": "bars",
    "download": True,
    "stock_list": list(codes),
    "periods": ["1d"],
    "start_time": start_date.strftime("%Y%m%d"),
    "end_time": end_date.strftime("%Y%m%d"),
  }
  payload = raw.get("payload")
  if payload != expected_payload:
    raise QmtDailyBarArchiveError(
      f"QMT request payload 与 ledger scope 不一致: {request_id}"
    )
  payload_sha256 = _require_sha256(
    raw.get("payload_sha256"),
    f"request {request_id} payload_sha256",
  )
  if payload_sha256 != _canonical_json_sha256(expected_payload):
    raise QmtDailyBarArchiveError(
      f"QMT request payload_sha256 不匹配: {request_id}"
    )

  expected_chunks = _positive_int(
    raw.get("expected_chunks"),
    f"request {request_id} expected_chunks",
  )
  received_chunks = _positive_int(
    raw.get("received_chunks"),
    f"request {request_id} received_chunks",
  )
  if expected_chunks != received_chunks:
    raise QmtDailyBarArchiveError(
      f"QMT request transfer 不完整: {request_id}"
    )
  record_count = _positive_int(
    raw.get("record_count"),
    f"request {request_id} record_count",
  )
  symbol_count = _positive_int(
    raw.get("symbol_count"),
    f"request {request_id} symbol_count",
  )
  if symbol_count > len(codes):
    raise QmtDailyBarArchiveError(
      f"QMT request symbol_count 超过请求代码数: {request_id}"
    )
  source_key_sha256 = _require_sha256(
    raw.get("source_key_sha256"),
    f"request {request_id} source_key_sha256",
  )

  raw_chunks = raw.get("chunks")
  if not isinstance(raw_chunks, list) or len(raw_chunks) != expected_chunks:
    raise QmtDailyBarArchiveError(
      f"QMT request chunks 与 expected_chunks 不一致: {request_id}"
    )
  chunks: list[_ArchiveChunk] = []
  for expected_index, item in enumerate(raw_chunks):
    if not isinstance(item, dict):
      raise QmtDailyBarArchiveError(
        f"QMT request chunk 必须是 object: {request_id}/{expected_index}"
      )
    index = _nonnegative_int(
      item.get("index"),
      f"request {request_id} chunk.index",
    )
    if index != expected_index:
      raise QmtDailyBarArchiveError(
        f"QMT request chunk index 不连续: {request_id}/{index}"
      )
    if item.get("compressed") is not True:
      raise QmtDailyBarArchiveError(
        f"QMT request chunk 必须是 gzip: {request_id}/{index}"
      )
    expected_path = f"requests/{request_id}/{index:08d}.json.gz"
    relative_path = str(item.get("path") or "")
    if relative_path != expected_path:
      raise QmtDailyBarArchiveError(
        f"QMT request chunk path 非 canonical: {request_id}/{index}"
      )
    _validate_relative_archive_path(relative_path)
    compressed_bytes = _positive_int(
      item.get("bytes"),
      f"request {request_id} chunk.bytes",
    )
    if compressed_bytes > _MAX_COMPRESSED_CHUNK_BYTES:
      raise QmtDailyBarArchiveError(
        f"QMT request chunk 超过压缩大小上限: {relative_path}"
      )
    records = _positive_int(
      item.get("records"),
      f"request {request_id} chunk.records",
    )
    if records > _MAX_CHUNK_RECORDS:
      raise QmtDailyBarArchiveError(
        f"QMT request chunk 超过记录数上限: {relative_path}"
      )
    chunks.append(
      _ArchiveChunk(
        index=index,
        relative_path=relative_path,
        sha256=_require_sha256(
          item.get("sha256"),
          f"request {request_id} chunk.sha256",
        ),
        records=records,
        compressed_bytes=compressed_bytes,
      )
    )
  if sum(chunk.records for chunk in chunks) != record_count:
    raise QmtDailyBarArchiveError(
      f"QMT request chunk records 汇总不匹配: {request_id}"
    )

  # Resolve both the request directory and manifest path now so an archive
  # cannot hide them behind a traversal or symbolic link before the first read.
  _safe_archive_directory(
    archive_root,
    f"requests/{request_id}",
    label=f"request directory {request_id}",
  )
  _safe_archive_file(
    archive_root,
    f"requests/{request_id}/manifest.json",
    label=f"request manifest {request_id}",
  )
  return _ArchiveRequest(
    raw=dict(raw),
    job_id=job_id,
    request_id=request_id,
    kind=kind,
    codes=codes,
    start_date=start_date,
    end_date=end_date,
    record_count=record_count,
    symbol_count=symbol_count,
    source_key_sha256=source_key_sha256,
    chunks=tuple(chunks),
  )


def _validate_record(
  raw: Any,
  *,
  request: _ArchiveRequest,
) -> dict[str, Any]:
  if not isinstance(raw, dict):
    raise QmtDailyBarArchiveError(
      f"QMT chunk 包含非 object 记录: {request.request_id}"
    )
  code = str(raw.get("code") or "")
  if code not in request.code_set:
    raise QmtDailyBarArchiveError(
      f"QMT chunk 包含请求外标的: {request.request_id} {code or '<empty>'}"
    )
  if str(raw.get("period") or "1d") != "1d":
    raise QmtDailyBarArchiveError(
      f"QMT chunk 包含非日线周期: {request.request_id}"
    )
  if raw.get("time") is None:
    raise QmtDailyBarArchiveError(
      f"QMT chunk 记录缺少 time: {request.request_id} {code}"
    )
  time_ms = _timestamp_millis(raw["time"])
  local_date = _millis_to_local_date(time_ms)
  if local_date < request.start_date or local_date > request.end_date:
    raise QmtDailyBarArchiveError(
      "QMT chunk 记录超出 request 日期范围: "
      f"{request.request_id} {code}@{time_ms}"
    )
  return {
    "code": code,
    "time_ms": time_ms,
    "open": raw.get("open"),
    "high": raw.get("high"),
    "low": raw.get("low"),
    "close": raw.get("close"),
    "volume": raw.get("volume"),
    "amount": raw.get("amount"),
    "suspend_flag": raw.get("suspendFlag", raw.get("suspend_flag")),
  }


def _validate_query_coverage(
  codes: Sequence[str],
  requests: Sequence[_ArchiveRequest],
  *,
  requested_start: date,
  requested_end: date,
  boundary_tolerance_days: int,
  instrument_lifecycle: Mapping[
    str, tuple[date | None, date | None]
  ] | None = None,
) -> dict[str, Any]:
  available_starts: list[date] = []
  available_ends: list[date] = []
  truncated = False
  lifecycle_adjusted_code_count = 0
  tolerance = timedelta(days=boundary_tolerance_days)
  lifecycle_by_code = instrument_lifecycle or {}
  for code in codes:
    open_date, expire_date = lifecycle_by_code.get(code, (None, None))
    active_start = max(requested_start, open_date or requested_start)
    active_end = min(requested_end, expire_date or requested_end)
    if active_end < active_start:
      raise QmtDailyBarArchiveError(
        f"QMT archive 查询标的与证券生命周期不相交: {code}"
      )
    intervals = sorted(
      (request.start_date, request.end_date)
      for request in requests
      if code in request.code_set
    )
    if not intervals:
      raise QmtDailyBarArchiveError(
        f"QMT archive 未覆盖请求标的: {code}"
      )
    merged: list[list[date]] = []
    for interval_start, interval_end in intervals:
      if not merged or interval_start > merged[-1][1] + timedelta(days=1):
        merged.append([interval_start, interval_end])
      else:
        merged[-1][1] = max(merged[-1][1], interval_end)
    if len(merged) != 1:
      raise QmtDailyBarArchiveError(
        f"QMT archive 对 {code} 的查询区间存在内部日期缺口"
      )
    available_start, available_end = merged[0]
    if available_start > active_start + tolerance:
      raise QmtDailyBarArchiveError(
        "QMT archive 起始覆盖不足: "
        f"{code} requested={requested_start} active={active_start} "
        f"available={available_start}"
      )
    if available_end < active_end - tolerance:
      raise QmtDailyBarArchiveError(
        "QMT archive 截止覆盖不足: "
        f"{code} requested={requested_end} active={active_end} "
        f"available={available_end}"
      )
    if available_start > active_start or available_end < active_end:
      truncated = True
    lifecycle_adjusted = False
    if active_start > requested_start and available_start <= active_start + tolerance:
      available_starts.append(requested_start)
      lifecycle_adjusted = True
    else:
      available_starts.append(available_start)
    if active_end < requested_end and available_end >= active_end - tolerance:
      available_ends.append(requested_end)
      lifecycle_adjusted = True
    else:
      available_ends.append(available_end)
    if lifecycle_adjusted:
      lifecycle_adjusted_code_count += 1
  return {
    "available_start": max(available_starts).isoformat(),
    "available_end": min(available_ends).isoformat(),
    "boundary_truncated": truncated,
    "lifecycle_adjusted_code_count": lifecycle_adjusted_code_count,
  }


def _optional_date(value: Any) -> date | None:
  if value is None or pd.isna(value):
    return None
  return pd.Timestamp(value).date()


def _normalize_worker_compatible_bars(
  rows: list[dict[str, Any]],
) -> pd.DataFrame:
  frame = pd.DataFrame(rows)
  frame["time"] = pd.to_datetime(
    frame["time"],
    unit="ms",
    utc=True,
  ).dt.tz_convert("Asia/Shanghai")
  for column in _PRICE_COLUMNS:
    frame[column] = pd.to_numeric(frame[column], errors="coerce").round(3)
  for column in _FLOW_COLUMNS:
    frame[column] = pd.to_numeric(frame[column], errors="coerce").round(2)
  frame["suspend_flag"] = (
    pd.to_numeric(frame["suspend_flag"], errors="coerce")
    .fillna(0)
    .astype("int64")
  )
  return normalize_daily_bars(frame)


def _resolve_ledger_path(archive: str | Path) -> Path:
  path = Path(archive)
  candidate = path / "ledger.json" if path.is_dir() else path
  return _safe_regular_file(candidate, "QMT archive ledger")


def _safe_regular_file(path: Path, label: str) -> Path:
  lexical = path.absolute()
  _reject_symlink_chain(lexical, label)
  try:
    resolved = lexical.resolve(strict=True)
  except OSError as exc:
    raise QmtDailyBarArchiveError(f"{label} 不可访问: {path}") from exc
  if not resolved.is_file():
    raise QmtDailyBarArchiveError(f"{label} 不是普通文件: {resolved}")
  return resolved


def _safe_archive_file(root: Path, relative: str, *, label: str) -> Path:
  candidate = _safe_archive_candidate(root, relative, label=label)
  if not candidate.is_file():
    raise QmtDailyBarArchiveError(f"{label} 不是普通文件: {candidate}")
  return candidate


def _safe_archive_directory(root: Path, relative: str, *, label: str) -> Path:
  candidate = _safe_archive_candidate(root, relative, label=label)
  if not candidate.is_dir():
    raise QmtDailyBarArchiveError(f"{label} 不是目录: {candidate}")
  return candidate


def _safe_archive_candidate(root: Path, relative: str, *, label: str) -> Path:
  parts = _validate_relative_archive_path(relative)
  lexical = root.joinpath(*parts)
  _reject_symlink_chain(lexical, label, stop=root)
  try:
    resolved = lexical.resolve(strict=True)
  except OSError as exc:
    raise QmtDailyBarArchiveError(f"{label} 不可访问: {relative}") from exc
  try:
    resolved.relative_to(root)
  except ValueError as exc:
    raise QmtDailyBarArchiveError(f"{label} 逃逸 archive 根目录") from exc
  return resolved


def _validate_relative_archive_path(value: str) -> tuple[str, ...]:
  if not value or "\\" in value or ":" in value:
    raise QmtDailyBarArchiveError(f"archive 相对路径非法: {value!r}")
  parsed = PurePosixPath(value)
  if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
    raise QmtDailyBarArchiveError(f"archive 相对路径非法: {value!r}")
  return parsed.parts


def _reject_symlink_chain(
  path: Path,
  label: str,
  *,
  stop: Path | None = None,
) -> None:
  current = path
  boundary = stop.resolve(strict=True) if stop is not None else None
  while True:
    if current.exists() and current.is_symlink():
      raise QmtDailyBarArchiveError(f"{label} 不能经过符号链接: {current}")
    if boundary is not None and current == boundary:
      break
    parent = current.parent
    if parent == current:
      break
    current = parent


def _bounded_gzip_decompress(compressed: bytes, label: str) -> bytes:
  try:
    with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as handle:
      raw = handle.read(_MAX_UNCOMPRESSED_CHUNK_BYTES + 1)
  except (OSError, EOFError) as exc:
    raise QmtDailyBarArchiveError(f"QMT chunk gzip 损坏: {label}") from exc
  if len(raw) > _MAX_UNCOMPRESSED_CHUNK_BYTES:
    raise QmtDailyBarArchiveError(f"QMT chunk 解压后超过大小上限: {label}")
  return raw


def _parse_compact_date(value: Any, label: str) -> date:
  try:
    return datetime.strptime(str(value), "%Y%m%d").date()
  except ValueError as exc:
    raise QmtDailyBarArchiveError(f"{label} 必须是 YYYYMMDD") from exc


def _canonical_uuid(value: Any) -> str:
  try:
    parsed = str(uuid.UUID(str(value)))
  except (ValueError, AttributeError) as exc:
    raise QmtDailyBarArchiveError(f"非法 request_id: {value!r}") from exc
  if parsed != str(value):
    raise QmtDailyBarArchiveError(f"request_id 非 canonical: {value!r}")
  return parsed


def _require_sha256(value: Any, label: str) -> str:
  text = str(value or "")
  if not _SHA256_PATTERN.fullmatch(text):
    raise QmtDailyBarArchiveError(f"{label} 不是小写 SHA256")
  return text


def _positive_int(value: Any, label: str) -> int:
  parsed = _nonnegative_int(value, label)
  if parsed <= 0:
    raise QmtDailyBarArchiveError(f"{label} 必须大于 0")
  return parsed


def _nonnegative_int(value: Any, label: str) -> int:
  if isinstance(value, bool):
    raise QmtDailyBarArchiveError(f"{label} 必须是整数")
  try:
    parsed = int(value)
  except (TypeError, ValueError) as exc:
    raise QmtDailyBarArchiveError(f"{label} 必须是整数") from exc
  if str(parsed) != str(value) and not isinstance(value, int):
    raise QmtDailyBarArchiveError(f"{label} 必须是 canonical 整数")
  if parsed < 0:
    raise QmtDailyBarArchiveError(f"{label} 不能小于 0")
  return parsed


def _timestamp_millis(value: Any) -> int:
  if isinstance(value, bool):
    raise QmtDailyBarArchiveError("QMT 日线 time 不能是布尔值")
  if isinstance(value, (int, float)):
    numeric = float(value)
    if not math.isfinite(numeric):
      raise QmtDailyBarArchiveError("QMT 日线 time 不是有限数")
    return int(numeric)
  if hasattr(value, "to_pydatetime"):
    value = value.to_pydatetime()
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
    raise QmtDailyBarArchiveError(f"QMT 日线 time 无法解析: {value!r}") from exc
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
  return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _millis_to_local_date(value: int) -> date:
  try:
    return datetime.fromtimestamp(
      value / 1000,
      tz=timezone.utc,
    ).astimezone(_SHANGHAI).date()
  except (OSError, OverflowError, ValueError) as exc:
    raise QmtDailyBarArchiveError(
      f"QMT 日线 time 超出可用范围: {value}"
    ) from exc


def _canonical_json_sha256(value: Any) -> str:
  encoded = json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    default=str,
  ).encode("utf-8")
  return _bytes_sha256(encoded)


def _code_sha256(codes: Sequence[str]) -> str:
  return _text_sha256("\n".join(codes))


def _unique_codes(values: Sequence[str]) -> list[str]:
  return list(
    dict.fromkeys(
      str(value).strip().upper()
      for value in values
      if str(value).strip()
    )
  )


def _file_sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for block in iter(lambda: handle.read(1024 * 1024), b""):
      digest.update(block)
  return digest.hexdigest()


def _bytes_sha256(value: bytes) -> str:
  return hashlib.sha256(value).hexdigest()


def _text_sha256(value: str) -> str:
  return _bytes_sha256(value.encode("utf-8"))


__all__ = [
  "FULL_A_SHARE_REQUIRED_REQUEST_COUNT",
  "QMT_DAILY_BAR_ARCHIVE_FORMAT",
  "QMT_DAILY_BAR_ARCHIVE_SCHEMA_VERSION",
  "QmtDailyBarArchiveError",
  "QmtDailyBarArchiveResearchDataSource",
  "describe_qmt_daily_bar_archive",
]
