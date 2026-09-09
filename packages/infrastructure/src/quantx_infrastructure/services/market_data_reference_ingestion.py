"""Factor and financial upload ingestion, independent of Prefect orchestration."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from quantx_infrastructure.repositories.divid_factor_repository import (
  divid_factor_codes_sha256,
)
from quantx_infrastructure.services.divid_factor_service import DividFactorService
from quantx_infrastructure.services.financial_service import FinancialService
from quantx_infrastructure.services.market_data_ingestion_progress import evidence_hash
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  MarketDataValidationError,
  load_uploaded_request_manifest,
  load_uploaded_request_records,
)

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

_FINANCIAL_TABLES = ("Balance", "Income", "CashFlow", "Capital")

_FINANCIAL_RECORD_FORMAT = "financial-row-v1"


def _validate_divid_factor_replacement_audit(
  audit: dict[str, Any],
  *,
  records_received: int,
  stock_codes: list[str],
  start_ex_date: str,
  end_ex_date: str,
) -> None:
  """Reject a factor ingestion before COMPLETED unless exact-row proof holds."""

  def count(field: str) -> int:
    value = audit.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
      raise RuntimeError(f"divid factor replacement audit field invalid: {field}")
    return value

  if (
    type(audit.get("audit_schema_version")) is not int
    or audit.get("audit_schema_version") != 2
  ):
    raise RuntimeError("divid factor replacement audit schema is unsupported")
  if count("stock_count") != len(stock_codes):
    raise RuntimeError("divid factor replacement stock count mismatch")
  if audit.get("stock_codes_sha256") != divid_factor_codes_sha256(stock_codes):
    raise RuntimeError("divid factor replacement stock scope digest mismatch")
  if audit.get("start_ex_date") != start_ex_date or (
    audit.get("end_ex_date") != end_ex_date
  ):
    raise RuntimeError("divid factor replacement date scope mismatch")
  if count("prior_count") != count("deleted_count"):
    raise RuntimeError("divid factor replacement delete count mismatch")
  if not (records_received == count("inserted_count") == count("verified_count")):
    raise RuntimeError("divid factor replacement persisted count mismatch")
  source_sha256 = str(audit.get("source_sha256") or "")
  persisted_sha256 = str(audit.get("persisted_sha256") or "")
  if (
    len(source_sha256) != 64
    or any(character not in "0123456789abcdef" for character in source_sha256)
    or source_sha256 != persisted_sha256
  ):
    raise RuntimeError("divid factor replacement content digest mismatch")
  code_audits = audit.get("code_audits")
  if not isinstance(code_audits, dict) or set(code_audits) != set(stock_codes):
    raise RuntimeError("divid factor replacement per-code scope mismatch")
  per_code_total = 0
  for code in stock_codes:
    item = code_audits.get(code)
    if not isinstance(item, dict):
      raise RuntimeError("divid factor replacement per-code audit is invalid")
    record_count = item.get("record_count")
    if (
      isinstance(record_count, bool)
      or not isinstance(record_count, int)
      or record_count < 0
    ):
      raise RuntimeError("divid factor replacement per-code count is invalid")
    code_source_sha256 = str(item.get("source_sha256") or "")
    code_persisted_sha256 = str(item.get("persisted_sha256") or "")
    if (
      len(code_source_sha256) != 64
      or any(character not in "0123456789abcdef" for character in code_source_sha256)
      or code_source_sha256 != code_persisted_sha256
    ):
      raise RuntimeError("divid factor replacement per-code digest mismatch")
    per_code_total += record_count
  if per_code_total != records_received:
    raise RuntimeError("divid factor replacement per-code count mismatch")


def _normalize_financial_records(
  records: list[dict[str, Any]],
  payload: dict[str, Any],
) -> tuple[dict[str, dict[str, pd.DataFrame]], dict[str, Any]]:
  requested_codes = sorted(
    {
      str(code).strip().upper()
      for code in payload.get("stock_list") or []
      if str(code).strip()
    }
  )
  if not requested_codes:
    raise MarketDataValidationError("financial_data request has no stock_list")
  requested = set(requested_codes)
  requested_tables = tuple(payload.get("table_list") or _FINANCIAL_TABLES)
  if not requested_tables or any(
    table not in _FINANCIAL_TABLES for table in requested_tables
  ):
    raise MarketDataValidationError(
      "financial_data request contains unsupported tables"
    )
  if not records:
    raise MarketDataValidationError("financial_data transfer contains no records")

  is_v1 = any(record.get("record_type") for record in records)
  rows_by_code: dict[str, dict[str, list[dict[str, Any]]]] = {}
  summaries: dict[str, dict[str, int]] = {}
  keys: set[tuple[str, str, date]] = set()

  if is_v1:
    start_time = str(payload.get("start_time") or "")
    end_time = str(payload.get("end_time") or "")
    for label, value in (("start_time", start_time), ("end_time", end_time)):
      if len(value) != 8 or not value.isdigit():
        raise MarketDataValidationError(f"financial_data {label} must be YYYYMMDD")
    if end_time < start_time:
      raise MarketDataValidationError("financial_data end_time precedes start_time")
    for record in records:
      code = str(record.get("code") or "").strip().upper()
      if code not in requested:
        raise MarketDataValidationError(f"unexpected financial_data code: {code}")
      if int(record.get("schema_version") or 0) != 1:
        raise MarketDataValidationError(f"unsupported financial_data schema for {code}")
      record_type = str(record.get("record_type") or "")
      if record_type == "financial_summary":
        if code in summaries:
          raise MarketDataValidationError(f"duplicate financial_data summary: {code}")
        raw_counts = record.get("table_counts")
        if not isinstance(raw_counts, dict):
          raise MarketDataValidationError(f"invalid financial_data summary: {code}")
        counts: dict[str, int] = {}
        for table in requested_tables:
          count = raw_counts.get(table)
          if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise MarketDataValidationError(
              f"invalid financial_data summary count: {code}/{table}"
            )
          counts[table] = count
        if set(raw_counts) != set(requested_tables):
          raise MarketDataValidationError(
            f"financial_data summary tables mismatch: {code}"
          )
        summaries[code] = counts
        continue
      if record_type != "financial_row":
        raise MarketDataValidationError(
          f"unknown financial_data record type: {record_type}"
        )
      table = str(record.get("table") or "")
      if table not in requested_tables:
        raise MarketDataValidationError(
          f"unexpected financial_data table: {code}/{table}"
        )
      row = record.get("row")
      if not isinstance(row, dict):
        raise MarketDataValidationError(f"invalid financial_data row: {code}/{table}")
      raw_report_date = row.get("m_timetag")
      if not (
        isinstance(raw_report_date, str)
        and len(raw_report_date) == 8
        and raw_report_date.isdigit()
      ):
        raise MarketDataValidationError(
          f"financial_data report date is not YYYYMMDD: {code}/{table}"
        )
      raw_announce_date = row.get("m_anntime")
      if raw_announce_date is not None and not (
        isinstance(raw_announce_date, str)
        and len(raw_announce_date) == 8
        and raw_announce_date.isdigit()
      ):
        raise MarketDataValidationError(
          f"financial_data announce date is not YYYYMMDD: {code}/{table}"
        )
      report_date = FinancialService._parse_report_date(raw_report_date)
      if report_date is None:
        raise MarketDataValidationError(
          f"invalid financial_data report date: {code}/{table}"
        )
      key = (code, table, report_date)
      if key in keys:
        raise MarketDataValidationError(
          f"duplicate financial_data report: {code}/{table}/{report_date}"
        )
      keys.add(key)
      rows_by_code.setdefault(code, {}).setdefault(table, []).append(row)
  else:
    for record in records:
      code = str(record.get("code") or "").strip().upper()
      if code not in requested:
        raise MarketDataValidationError(
          f"unexpected legacy financial_data code: {code}"
        )
      if code in summaries:
        raise MarketDataValidationError(f"duplicate legacy financial_data code: {code}")
      raw_tables = record.get("financial_data") or {}
      if not isinstance(raw_tables, dict):
        raise MarketDataValidationError(
          f"invalid legacy financial_data payload: {code}"
        )
      unexpected = set(raw_tables) - set(requested_tables)
      if unexpected:
        raise MarketDataValidationError(
          f"unexpected legacy financial_data tables: {code}/{sorted(unexpected)}"
        )
      counts: dict[str, int] = {}
      for table in requested_tables:
        raw_rows = raw_tables.get(table) or []
        if not isinstance(raw_rows, list) or any(
          not isinstance(row, dict) for row in raw_rows
        ):
          raise MarketDataValidationError(
            f"invalid legacy financial_data rows: {code}/{table}"
          )
        counts[table] = len(raw_rows)
        if raw_rows:
          for row in raw_rows:
            report_date = FinancialService._parse_report_date(row.get("m_timetag"))
            if report_date is None:
              raise MarketDataValidationError(
                f"invalid legacy financial_data report date: {code}/{table}"
              )
            key = (code, table, report_date)
            if key in keys:
              raise MarketDataValidationError(
                f"duplicate legacy financial_data report: {code}/{table}/{report_date}"
              )
            keys.add(key)
          rows_by_code.setdefault(code, {})[table] = list(raw_rows)
      summaries[code] = counts

  if set(summaries) != requested:
    missing = sorted(requested - set(summaries))
    raise MarketDataValidationError(
      f"financial_data summaries missing codes: {missing}"
    )
  for code in requested_codes:
    actual_counts = {
      table: len(rows_by_code.get(code, {}).get(table, []))
      for table in requested_tables
    }
    if summaries[code] != actual_counts:
      raise MarketDataValidationError(
        f"financial_data summary count mismatch: {code} "
        f"expected={summaries[code]} actual={actual_counts}"
      )

  frames = {
    code: {table: pd.DataFrame(rows) for table, rows in tables.items() if rows}
    for code, tables in rows_by_code.items()
    if any(tables.values())
  }
  empty_codes = [code for code in requested_codes if sum(summaries[code].values()) == 0]
  source_rows = sum(sum(counts.values()) for counts in summaries.values())
  return frames, {
    "record_format": (_FINANCIAL_RECORD_FORMAT if is_v1 else "legacy-financial-map"),
    "requested_codes": len(requested_codes),
    "synced_codes": len(requested_codes) - len(empty_codes),
    "empty_codes": empty_codes,
    "source_rows": source_rows,
    "source_rows_by_code": {
      code: sum(summaries[code].values()) for code in requested_codes
    },
  }


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
    raise MarketDataValidationError("divid_factors request has no stock_list")
  start_ex_date = str(payload.get("start_time") or "")
  end_ex_date = str(payload.get("end_time") or "")
  for label, value in (
    ("start_time", start_ex_date),
    ("end_time", end_ex_date),
  ):
    if len(value) != 8 or not value.isdigit():
      raise MarketDataValidationError(f"divid_factors {label} must be YYYYMMDD")
  if end_ex_date < start_ex_date:
    raise MarketDataValidationError("divid_factors end_time precedes start_time")

  requested = set(stock_codes)
  keys: set[tuple[str, str]] = set()
  rows_by_code: dict[str, list[dict[str, Any]]] = {}
  for record in records:
    code = str(record.get("code") or "").strip().upper()
    ex_date = str(record.get("ex_date") or "").strip()
    if code not in requested:
      raise MarketDataValidationError(f"unexpected divid_factors code: {code}")
    if (
      len(ex_date) != 8
      or not ex_date.isdigit()
      or ex_date < start_ex_date
      or ex_date > end_ex_date
    ):
      raise MarketDataValidationError(
        f"divid_factors ex_date is outside request range: {code}/{ex_date}"
      )
    key = (code, ex_date)
    if key in keys:
      raise MarketDataValidationError(f"duplicate divid_factors key: {code}/{ex_date}")
    keys.add(key)

    normalized: dict[str, Any] = {"ex_date": ex_date}
    for field in _DIVID_FACTOR_FIELDS:
      try:
        numeric = float(record[field])
      except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataValidationError(
          f"invalid divid_factors {field}: {code}/{ex_date}"
        ) from exc
      if not math.isfinite(numeric):
        raise MarketDataValidationError(
          f"non-finite divid_factors {field}: {code}/{ex_date}"
        )
      normalized[field] = numeric
    if normalized["time"] <= 0 or normalized["dr"] <= 0:
      raise MarketDataValidationError(
        f"non-positive divid_factors time/dr: {code}/{ex_date}"
      )
    factor_date = (
      pd.to_datetime(normalized["time"], unit="ms", utc=True)
      .tz_convert("Asia/Shanghai")
      .strftime("%Y%m%d")
    )
    if factor_date != ex_date:
      raise MarketDataValidationError(
        f"divid_factors time/ex_date mismatch: {code}/{ex_date}/{factor_date}"
      )
    rows_by_code.setdefault(code, []).append(normalized)

  frames = {
    code: pd.DataFrame(rows).set_index("ex_date") for code, rows in rows_by_code.items()
  }
  return frames, stock_codes, start_ex_date, end_ex_date


async def _persist_reference_records(records, payload, db):
  operation = payload["operation"]
  saved = 0
  replacement_audit = None
  if operation == "divid_factors":
    frames, stock_codes, start_ex_date, end_ex_date = _normalize_divid_factor_records(
      records, payload
    )
    replacement_audit = await DividFactorService(
      **({"db_session": db} if db is not None else {})
    ).replace_batch_divid_factors(
      frames,
      stock_codes=stock_codes,
      start_ex_date=start_ex_date,
      end_ex_date=end_ex_date,
    )
    _validate_divid_factor_replacement_audit(
      replacement_audit,
      records_received=len(records),
      stock_codes=stock_codes,
      start_ex_date=start_ex_date,
      end_ex_date=end_ex_date,
    )
    saved = int(replacement_audit["inserted_count"])
  elif operation == "financial_data":
    frames, financial_audit = _normalize_financial_records(records, payload)
    persistence_audit = (
      await FinancialService(
        **({"db_session": db} if db is not None else {})
      ).save_batch_financial_data_with_audit(frames)
      if frames
      else {
        "rows_received": 0,
        "rows_upserted": 0,
        "rows_rejected": 0,
        "metric_codes_rebuilt": 0,
        "metric_rows_rebuilt": 0,
        "statement_rows_by_code": {},
        "metric_rows_by_code": {},
      }
    )
    source_rows = int(financial_audit["source_rows"])
    saved = int(persistence_audit["rows_upserted"])
    if saved != source_rows:
      raise RuntimeError(
        f"financial_data persistence count mismatch: source={source_rows} saved={saved}"
      )
    replacement_audit = {
      **financial_audit,
      **persistence_audit,
    }

  result = {
    "operation": operation,
    "records_received": len(records),
    "records_saved": saved,
  }
  if replacement_audit is not None:
    result["replacement_audit"] = replacement_audit
  return result


async def ingest_uploaded_reference_request(store, request_id, *, progress=None):
  _, payload, manifest = await load_uploaded_request_manifest(store, request_id)
  if payload.get("operation") not in {"divid_factors", "financial_data"}:
    raise MarketDataValidationError("unsupported reference upload operation")
  _, _, records = await load_uploaded_request_records(store, request_id)
  if progress is None:
    return await _persist_reference_records(records, payload, None)
  await progress.apply(
    "manifest",
    sha256=evidence_hash(
      {
        "payload": payload,
        "chunks": [
          {key: value for key, value in item.items() if key != "storage_reference"}
          for item in manifest
        ],
      }
    ),
  )
  if progress.state["phase"] == "VALIDATE":
    await progress.apply("advance", phase="WRITE")
  if progress.state["phase"] == "READBACK":
    return progress.state["write_result"]

  async def persist(db):
    return await _persist_reference_records(records, payload, db)

  progress.state = await store.persist_market_data_reference(
    request_id,
    claim_token=progress.claim_token,
    persist=persist,
  )
  return progress.state["write_result"]
