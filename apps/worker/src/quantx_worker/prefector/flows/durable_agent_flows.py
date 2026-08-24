"""Prefect flows that request Agent work through durable database messages."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import pandas as pd
from prefect import flow, get_run_logger
from prefect.runtime import flow_run as flow_run_runtime
from quantx_infrastructure import DurableRuntimeStore
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.broker_position_snapshot import BrokerPositionSnapshot
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.repositories.financial_sync_run_repository import (
  FinancialSyncRunRepository,
)
from quantx_infrastructure.services.divid_factor_service import (
  DividFactorService,
)
from quantx_infrastructure.services.financial_service import FinancialService
from quantx_infrastructure.services.market_data_request_service import (
  recover_failed_market_data_request,
)
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  claim_ingest_and_finish_market_data_request,
  ingest_uploaded_bar_request,
  load_uploaded_request_manifest,
  load_uploaded_request_records,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from quantx_infrastructure.services.trading_time_service import TradingDateHelper
from sqlalchemy import and_, select

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
_FINANCIAL_TABLES = ("Balance", "Income", "CashFlow", "Capital")
_FINANCIAL_RECORD_FORMAT = "financial-row-v1"
_FINANCIAL_BATCH_SIZE = 100
_FINANCIAL_LOOKBACK_DAYS = 1095
_ARCHIVE_SNAPSHOT_MAX_AGE_SECONDS = 90.0
_ARCHIVE_REQUEST_TIMEOUT_SECONDS = 30 * 60
_ARCHIVE_FAILED_RETRY_HOPS = 3
_MARKET_DATA_INGESTION_RECOVERY_BATCH_SIZE = 20


async def _persisted_instrument_codes() -> list[str]:
  store = DurableRuntimeStore()
  try:
    return await store.instrument_codes()
  finally:
    await store.close()


async def _persisted_stock_codes() -> list[str]:
  store = DurableRuntimeStore()
  try:
    return await store.instrument_codes(instrument_type="STOCK")
  finally:
    await store.close()


async def _persisted_position_codes() -> list[str]:
  """Return the distinct instruments that currently have a positive position."""
  async with AsyncSessionLocal() as db:
    values = (
      await db.execute(
        select(Position.stock_code)
        .where(Position.volume > 0)
        .distinct()
        .order_by(Position.stock_code)
      )
    ).scalars()
    return sorted(
      {
        str(value).strip().upper()
        for value in values.all()
        if str(value or "").strip()
      }
    )


async def _fresh_position_archive_universe() -> dict[str, Any]:
  """Freeze one fresh, complete single-account broker position universe."""

  now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
  async with AsyncSessionLocal() as db:
    rows = (
      await db.execute(
        select(BrokerPositionSnapshot, Position.stock_code)
        .select_from(BrokerPositionSnapshot)
        .outerjoin(
          Position,
          and_(
            Position.account_id == BrokerPositionSnapshot.account_id,
            Position.volume > 0,
          ),
        )
        .where(BrokerPositionSnapshot.is_complete.is_(True))
        .order_by(
          BrokerPositionSnapshot.received_at.desc(),
          BrokerPositionSnapshot.account_id,
          Position.stock_code,
        )
      )
    ).all()
    universes: dict[str, tuple[Any, set[str]]] = {}
    for snapshot, raw_code in rows:
      account_id = str(snapshot.account_id)
      _, codes = universes.setdefault(account_id, (snapshot, set()))
      normalized_code = str(raw_code or "").strip().upper()
      if normalized_code:
        codes.add(normalized_code)
    if len(universes) != 1:
      raise RuntimeError(
        "持仓 Tick 同步要求唯一完整券商持仓快照: "
        f"actual={len(universes)}"
      )
    snapshot, frozen_codes = next(iter(universes.values()))
    if int(snapshot.sequence or 0) <= 0 or str(snapshot.last_error or "").strip():
      raise RuntimeError("持仓 Tick 同步要求无错误的完整券商持仓快照")
    for field, observed in (
      ("reported_at", snapshot.reported_at),
      ("received_at", snapshot.received_at),
    ):
      if observed is None:
        raise RuntimeError(f"持仓 Tick 同步缺少快照时间: {field}")
      age = (now_utc - observed).total_seconds()
      if age < -5 or age > _ARCHIVE_SNAPSHOT_MAX_AGE_SECONDS:
        raise RuntimeError(
          "持仓 Tick 同步拒绝陈旧券商持仓快照: "
          f"field={field} age_seconds={age:.1f}"
        )
    codes = sorted(frozen_codes)
    if len(codes) != int(snapshot.position_count or 0):
      raise RuntimeError(
        "持仓 Tick 同步拒绝未闭合的持仓投影: "
        f"snapshot_count={int(snapshot.position_count or 0)} "
        f"positive_positions={len(codes)}"
      )
    return {
      "account_id": str(snapshot.account_id),
      "sequence": int(snapshot.sequence),
      "reported_at": snapshot.reported_at.isoformat(),
      "received_at": snapshot.received_at.isoformat(),
      "stock_codes": codes,
    }


def _scheduled_start_time() -> Optional[datetime]:
  try:
    return flow_run_runtime.get_scheduled_start_time()
  except Exception:
    return None


async def _request_and_wait(
  payload: dict[str, Any],
  *,
  timeout_seconds: int = _ARCHIVE_REQUEST_TIMEOUT_SECONDS,
  agent_device_id: str = "",
  required_capabilities: Optional[list[str]] = None,
  idempotency_scope: str = "",
) -> dict[str, Any]:
  store = DurableRuntimeStore()
  try:
    request_kwargs: dict[str, Any] = {}
    if agent_device_id:
      request_kwargs["device_id"] = agent_device_id
    if required_capabilities:
      request_kwargs["required_capabilities"] = required_capabilities
    if idempotency_scope:
      request_kwargs["idempotency_scope"] = idempotency_scope
    request_id = await store.create_market_data_request(payload, **request_kwargs)
    reopen_attempted: set[str] = set()
    retry_hops = 0
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
      request = await store.market_data_request(request_id)
      if request is None:
        return {"status": "missing", "request_id": request_id}
      status = str(request.get("status") or "MISSING").upper()
      if status == "COMPLETED":
        ingestion_result = request.get("ingestion_result")
        if not isinstance(ingestion_result, dict):
          raise RuntimeError(
            "COMPLETED market-data request is missing its ingestion audit: "
            f"request_id={request_id}"
          )
        return {
          "status": "completed",
          "request_id": request_id,
          **ingestion_result,
        }
      if status == "FAILED":
        if retry_hops >= _ARCHIVE_FAILED_RETRY_HOPS:
          return {
            "status": "failed",
            "request_id": request_id,
            "reason": request.get("processing_error"),
          }
        recovery = await recover_failed_market_data_request(
          store,
          payload=payload,
          request_id=request_id,
          reopen_attempted=reopen_attempted,
          retry_hops=retry_hops,
          device_id=agent_device_id or None,
        )
        if recovery is None:
          return {
            "status": "failed",
            "request_id": request_id,
            "reason": request.get("processing_error"),
          }
        request_id, retry_hops, _ = recovery
        continue
      if status in {"UPLOADED", "PROCESSING"}:
        convergence = await claim_ingest_and_finish_market_data_request(
          store,
          request_id,
          ingest_request=_ingest_uploaded_request,
        )
        if convergence is not None:
          if convergence.get("status") == "completed":
            return convergence
          if convergence.get("status") == "failed":
            continue
          await asyncio.sleep(5)
          continue
      await asyncio.sleep(2)
    # A caller deadline is not evidence that Agent preparation/upload failed.
    # Leave the durable request open so the Prefect retry observes the same
    # request_id and the Agent can reuse its immutable local spool.
    final = await store.market_data_request(request_id)
    if final is not None and str(final.get("status") or "").upper() == "COMPLETED":
      ingestion_result = final.get("ingestion_result")
      if isinstance(ingestion_result, dict):
        return {
          "status": "completed",
          "request_id": request_id,
          **ingestion_result,
        }
    return {
      "status": "timeout",
      "request_id": request_id,
      "reason": "wait attempt expired; durable request remains open",
    }
  finally:
    await store.close()


async def _ingest_uploaded_request(
  store: DurableRuntimeStore,
  request_id: str,
) -> dict[str, Any]:
  _, payload, _ = await load_uploaded_request_manifest(store, request_id)
  operation = str(payload.get("operation") or "bars")
  saved = 0
  replacement_audit: dict[str, Any] | None = None
  if operation == "bars":
    return await ingest_uploaded_bar_request(
      store,
      request_id,
      save_period=save_market_data,
    )
  _, _, records = await load_uploaded_request_records(store, request_id)
  if operation == "divid_factors":
    frames, stock_codes, start_ex_date, end_ex_date = _normalize_divid_factor_records(
      records, payload
    )
    replacement_audit = await DividFactorService().replace_batch_divid_factors(
      frames,
      stock_codes=stock_codes,
      start_ex_date=start_ex_date,
      end_ex_date=end_ex_date,
    )
    saved = int(replacement_audit["inserted_count"])
  elif operation == "financial_data":
    frames, financial_audit = _normalize_financial_records(records, payload)
    persistence_audit = (
      await FinancialService().save_batch_financial_data_with_audit(frames)
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
    raise RuntimeError("financial_data request has no stock_list")
  requested = set(requested_codes)
  requested_tables = tuple(payload.get("table_list") or _FINANCIAL_TABLES)
  if not requested_tables or any(
    table not in _FINANCIAL_TABLES for table in requested_tables
  ):
    raise RuntimeError("financial_data request contains unsupported tables")
  if not records:
    raise RuntimeError("financial_data transfer contains no records")

  is_v1 = any(record.get("record_type") for record in records)
  rows_by_code: dict[str, dict[str, list[dict[str, Any]]]] = {}
  summaries: dict[str, dict[str, int]] = {}
  keys: set[tuple[str, str, date]] = set()

  if is_v1:
    start_time = str(payload.get("start_time") or "")
    end_time = str(payload.get("end_time") or "")
    for label, value in (("start_time", start_time), ("end_time", end_time)):
      if len(value) != 8 or not value.isdigit():
        raise RuntimeError(f"financial_data {label} must be YYYYMMDD")
    if end_time < start_time:
      raise RuntimeError("financial_data end_time precedes start_time")
    for record in records:
      code = str(record.get("code") or "").strip().upper()
      if code not in requested:
        raise RuntimeError(f"unexpected financial_data code: {code}")
      if int(record.get("schema_version") or 0) != 1:
        raise RuntimeError(f"unsupported financial_data schema for {code}")
      record_type = str(record.get("record_type") or "")
      if record_type == "financial_summary":
        if code in summaries:
          raise RuntimeError(f"duplicate financial_data summary: {code}")
        raw_counts = record.get("table_counts")
        if not isinstance(raw_counts, dict):
          raise RuntimeError(f"invalid financial_data summary: {code}")
        counts: dict[str, int] = {}
        for table in requested_tables:
          count = raw_counts.get(table)
          if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RuntimeError(f"invalid financial_data summary count: {code}/{table}")
          counts[table] = count
        if set(raw_counts) != set(requested_tables):
          raise RuntimeError(f"financial_data summary tables mismatch: {code}")
        summaries[code] = counts
        continue
      if record_type != "financial_row":
        raise RuntimeError(f"unknown financial_data record type: {record_type}")
      table = str(record.get("table") or "")
      if table not in requested_tables:
        raise RuntimeError(f"unexpected financial_data table: {code}/{table}")
      row = record.get("row")
      if not isinstance(row, dict):
        raise RuntimeError(f"invalid financial_data row: {code}/{table}")
      raw_report_date = row.get("m_timetag")
      if not (
        isinstance(raw_report_date, str)
        and len(raw_report_date) == 8
        and raw_report_date.isdigit()
      ):
        raise RuntimeError(
          f"financial_data report date is not YYYYMMDD: {code}/{table}"
        )
      raw_announce_date = row.get("m_anntime")
      if raw_announce_date is not None and not (
        isinstance(raw_announce_date, str)
        and len(raw_announce_date) == 8
        and raw_announce_date.isdigit()
      ):
        raise RuntimeError(
          f"financial_data announce date is not YYYYMMDD: {code}/{table}"
        )
      report_date = FinancialService._parse_report_date(raw_report_date)
      if report_date is None:
        raise RuntimeError(f"invalid financial_data report date: {code}/{table}")
      key = (code, table, report_date)
      if key in keys:
        raise RuntimeError(
          f"duplicate financial_data report: {code}/{table}/{report_date}"
        )
      keys.add(key)
      rows_by_code.setdefault(code, {}).setdefault(table, []).append(row)
  else:
    for record in records:
      code = str(record.get("code") or "").strip().upper()
      if code not in requested:
        raise RuntimeError(f"unexpected legacy financial_data code: {code}")
      if code in summaries:
        raise RuntimeError(f"duplicate legacy financial_data code: {code}")
      raw_tables = record.get("financial_data") or {}
      if not isinstance(raw_tables, dict):
        raise RuntimeError(f"invalid legacy financial_data payload: {code}")
      unexpected = set(raw_tables) - set(requested_tables)
      if unexpected:
        raise RuntimeError(
          f"unexpected legacy financial_data tables: {code}/{sorted(unexpected)}"
        )
      counts: dict[str, int] = {}
      for table in requested_tables:
        raw_rows = raw_tables.get(table) or []
        if not isinstance(raw_rows, list) or any(
          not isinstance(row, dict) for row in raw_rows
        ):
          raise RuntimeError(f"invalid legacy financial_data rows: {code}/{table}")
        counts[table] = len(raw_rows)
        if raw_rows:
          for row in raw_rows:
            report_date = FinancialService._parse_report_date(row.get("m_timetag"))
            if report_date is None:
              raise RuntimeError(
                f"invalid legacy financial_data report date: {code}/{table}"
              )
            key = (code, table, report_date)
            if key in keys:
              raise RuntimeError(
                f"duplicate legacy financial_data report: {code}/{table}/{report_date}"
              )
            keys.add(key)
          rows_by_code.setdefault(code, {})[table] = list(raw_rows)
      summaries[code] = counts

  if set(summaries) != requested:
    missing = sorted(requested - set(summaries))
    raise RuntimeError(f"financial_data summaries missing codes: {missing}")
  for code in requested_codes:
    actual_counts = {
      table: len(rows_by_code.get(code, {}).get(table, []))
      for table in requested_tables
    }
    if summaries[code] != actual_counts:
      raise RuntimeError(
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
    convergence = await claim_ingest_and_finish_market_data_request(
      store,
      normalized_request_id,
      ingest_request=_ingest_uploaded_request,
    )
    if convergence is None:
      raise RuntimeError(
        "market-data request could not be claimed for reprocessing: "
        f"request_id={normalized_request_id}"
      )
    if convergence.get("status") != "completed":
      raise RuntimeError(
        "market-data request reprocessing did not complete: "
        f"request_id={normalized_request_id} "
        f"status={convergence.get('status')} reason={convergence.get('reason')}"
      )
    return convergence
  finally:
    await store.close()


@flow(name="QMT Agent 行情摄取恢复")
async def recover_market_data_ingestion_flow(
  limit: int = _MARKET_DATA_INGESTION_RECOVERY_BATCH_SIZE,
) -> dict[str, Any]:
  """Recover expired delivery leases and durably converge immutable uploads."""

  if isinstance(limit, bool) or int(limit) < 1:
    raise ValueError("market-data recovery limit must be positive")
  store = DurableRuntimeStore()
  try:
    requeued_delivery_request_ids = (
      await store.requeue_expired_market_data_delivery_leases(limit=int(limit))
    )
    request_ids = await store.recoverable_market_data_request_ids(limit=int(limit))
    completed_request_ids: list[str] = []
    retryable_request_ids: list[str] = []
    unclaimed_request_ids: list[str] = []
    for request_id in request_ids:
      convergence = await claim_ingest_and_finish_market_data_request(
        store,
        request_id,
        ingest_request=_ingest_uploaded_request,
      )
      if convergence is None:
        unclaimed_request_ids.append(request_id)
      elif convergence.get("status") == "completed":
        completed_request_ids.append(request_id)
      else:
        retryable_request_ids.append(request_id)
    return {
      "status": "completed",
      "requeued_delivery_request_ids": requeued_delivery_request_ids,
      "scanned": len(request_ids),
      "completed_request_ids": completed_request_ids,
      "retryable_request_ids": retryable_request_ids,
      "unclaimed_request_ids": unclaimed_request_ids,
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
        raise RuntimeError(f"invalid divid_factors {field}: {code}/{ex_date}") from exc
      if not math.isfinite(numeric):
        raise RuntimeError(f"non-finite divid_factors {field}: {code}/{ex_date}")
      normalized[field] = numeric
    if normalized["time"] <= 0 or normalized["dr"] <= 0:
      raise RuntimeError(f"non-positive divid_factors time/dr: {code}/{ex_date}")
    factor_date = (
      pd.to_datetime(normalized["time"], unit="ms", utc=True)
      .tz_convert("Asia/Shanghai")
      .strftime("%Y%m%d")
    )
    if factor_date != ex_date:
      raise RuntimeError(
        f"divid_factors time/ex_date mismatch: {code}/{ex_date}/{factor_date}"
      )
    rows_by_code.setdefault(code, []).append(normalized)

  frames = {
    code: pd.DataFrame(rows).set_index("ex_date") for code, rows in rows_by_code.items()
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
  start_time: str = "",
  end_time: str = "",
  batch_size: int = _FINANCIAL_BATCH_SIZE,
  agent_device_id: str = "",
) -> dict[str, Any]:
  if not stock_codes:
    stock_codes = await _persisted_stock_codes()
  stock_codes = sorted(
    {str(code).strip().upper() for code in stock_codes or [] if str(code).strip()}
  )
  if not stock_codes:
    return {"status": "skipped", "reason": "persisted universe is empty"}
  if batch_size <= 0 or batch_size > _FINANCIAL_BATCH_SIZE:
    raise ValueError(
      f"financial batch_size must be between 1 and {_FINANCIAL_BATCH_SIZE}"
    )
  end_date = (
    datetime.strptime(end_time, "%Y%m%d").date() if end_time else datetime.now().date()
  )
  start_date = (
    datetime.strptime(start_time, "%Y%m%d").date()
    if start_time
    else end_date - timedelta(days=_FINANCIAL_LOOKBACK_DAYS)
  )
  if end_date < start_date:
    raise ValueError("financial end_time precedes start_time")
  start_text = start_date.strftime("%Y%m%d")
  end_text = end_date.strftime("%Y%m%d")
  total_batches = (len(stock_codes) + batch_size - 1) // batch_size
  started_at = datetime.now()

  async with AsyncSessionLocal() as db:
    run = await FinancialSyncRunRepository(db).create_run(
      {
        "status": "running",
        "started_at": started_at,
        "window_start": start_date,
        "window_end": end_date,
        "batch_count": total_batches,
        "requested_codes": len(stock_codes),
        "details": {"batches": [], "empty_codes": []},
      }
    )
    run_id = int(run.id)

  transfers: list[dict[str, Any]] = []
  empty_codes: list[str] = []
  statement_rows = 0
  metric_rows = 0
  synced_codes = 0
  failed_batches = 0
  current_batch: list[str] = []
  current_batch_index = 0
  current_batch_audited = False
  try:
    for batch_index, offset in enumerate(
      range(0, len(stock_codes), batch_size),
      start=1,
    ):
      code_batch = stock_codes[offset : offset + batch_size]
      current_batch = code_batch
      current_batch_index = batch_index
      current_batch_audited = False
      request_payload = {
        "operation": "financial_data",
        "record_format": _FINANCIAL_RECORD_FORMAT,
        "download": True,
        "stock_list": code_batch,
        "table_list": list(_FINANCIAL_TABLES),
        "start_time": start_text,
        "end_time": end_text,
        "sync_run_id": run_id,
        "batch_index": batch_index,
      }
      transfer = await _request_and_wait(
        request_payload,
        agent_device_id=agent_device_id,
        required_capabilities=["financial-data-v1"],
      )
      if transfer.get("status") != "completed":
        failed_batches += 1
        raise RuntimeError(
          "QMT Agent 财务请求失败: "
          f"batch={batch_index}/{total_batches} "
          f"request_id={transfer.get('request_id')} "
          f"status={transfer.get('status')}"
        )
      audit = transfer.get("replacement_audit") or {}
      if int(audit.get("requested_codes") or 0) != len(code_batch):
        failed_batches += 1
        raise RuntimeError(f"财务批次覆盖数不一致: batch={batch_index}/{total_batches}")
      transfers.append(transfer)
      empty_codes.extend(str(code) for code in audit.get("empty_codes") or [])
      statement_rows += int(audit.get("rows_upserted") or 0)
      metric_rows += int(audit.get("metric_rows_rebuilt") or 0)
      synced_codes += int(audit.get("synced_codes") or 0)
      verified_at = datetime.now()
      empty_code_set = {
        str(code).strip().upper() for code in audit.get("empty_codes") or []
      }
      statement_rows_by_code = audit.get("statement_rows_by_code") or {}
      metric_rows_by_code = audit.get("metric_rows_by_code") or {}
      async with AsyncSessionLocal() as db:
        run_repo = FinancialSyncRunRepository(db)
        await run_repo.upsert_code_audits(
          [
            {
              "run_id": run_id,
              "stock_code": code,
              "window_start": start_date,
              "window_end": end_date,
              "status": "EMPTY" if code in empty_code_set else "SUCCESS",
              "statement_rows": int(statement_rows_by_code.get(code) or 0),
              "metric_rows": int(metric_rows_by_code.get(code) or 0),
              "verified_at": verified_at,
              "details": {
                "batch_index": batch_index,
                "request_id": transfer.get("request_id"),
              },
            }
            for code in code_batch
          ]
        )
        current_batch_audited = True
        await run_repo.update_run(
          run_id,
          {
            "status": "running",
            "synced_codes": synced_codes,
            "empty_codes": len(set(empty_codes)),
            "statement_rows": statement_rows,
            "metric_rows": metric_rows,
            "details": {
              "completed_batches": len(transfers),
              "request_ids": [item.get("request_id") for item in transfers],
              "empty_codes": sorted(set(empty_codes)),
            },
          },
        )

    status = "partial_failure" if empty_codes else "success"
    warning = (
      f"{len(empty_codes)} 只股票未返回受支持的财务四表" if empty_codes else None
    )
    details = {
      "request_ids": [item.get("request_id") for item in transfers],
      "empty_codes": sorted(set(empty_codes)),
      "batches": [
        {
          "request_id": item.get("request_id"),
          **(item.get("replacement_audit") or {}),
        }
        for item in transfers
      ],
    }
    async with AsyncSessionLocal() as db:
      await FinancialSyncRunRepository(db).update_run(
        run_id,
        {
          "status": status,
          "completed_at": datetime.now(),
          "failed_batches": 0,
          "synced_codes": synced_codes,
          "empty_codes": len(set(empty_codes)),
          "statement_rows": statement_rows,
          "metric_rows": metric_rows,
          "warnings": warning,
          "details": details,
        },
      )
    return {
      "status": status,
      "run_id": run_id,
      "stock_count": len(stock_codes),
      "batch_count": total_batches,
      "request_ids": details["request_ids"],
      "synced_codes": synced_codes,
      "empty_codes": sorted(set(empty_codes)),
      "records_saved": statement_rows,
      "metric_rows": metric_rows,
      "start_time": start_text,
      "end_time": end_text,
      "warnings": [warning] if warning else [],
    }
  except Exception as exc:
    if current_batch and not current_batch_audited:
      try:
        async with AsyncSessionLocal() as db:
          await FinancialSyncRunRepository(db).upsert_code_audits(
            [
              {
                "run_id": run_id,
                "stock_code": code,
                "window_start": start_date,
                "window_end": end_date,
                "status": "FAILED",
                "statement_rows": 0,
                "metric_rows": 0,
                "verified_at": None,
                "details": {
                  "batch_index": current_batch_index,
                  "error": f"{exc.__class__.__name__}: {exc}",
                },
              }
              for code in current_batch
            ]
          )
      except Exception:
        logger.exception("Failed to persist per-code financial sync failure audit")
    async with AsyncSessionLocal() as db:
      await FinancialSyncRunRepository(db).update_run(
        run_id,
        {
          "status": "failed",
          "completed_at": datetime.now(),
          "failed_batches": max(1, failed_batches),
          "synced_codes": synced_codes,
          "empty_codes": len(set(empty_codes)),
          "statement_rows": statement_rows,
          "metric_rows": metric_rows,
          "warnings": f"{exc.__class__.__name__}: {exc}",
          "details": {
            "request_ids": [item.get("request_id") for item in transfers],
            "empty_codes": sorted(set(empty_codes)),
          },
        },
      )
    raise


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


@flow(
  name="Agent 状态收敛检查",
  retries=2,
  retry_delay_seconds=60,
)
async def agent_convergence_flow(
  sync_market_data: bool = False,
  target_date: str = "",
) -> dict[str, Any]:
  store = DurableRuntimeStore()
  try:
    agents = await store.component_status("qmt-agent:")
    market_data_device_id = (
      await store.available_market_data_device()
      if sync_market_data
      else None
    )
    ready = [
      item
      for item in agents
      if item.get("status") == "READY"
      and (
        not sync_market_data
        or str(item.get("component") or "")
        == f"qmt-agent:{market_data_device_id}"
      )
    ]
    result: dict[str, Any] = {
      "status": "ready" if ready else "degraded",
      "ready_agents": len(ready),
      "agents": agents,
    }
  finally:
    await store.close()

  if not sync_market_data:
    return result
  if not market_data_device_id:
    raise RuntimeError("持仓 Tick 同步无法启动: no fresh market-data QMT Agent")

  normalized_target = str(target_date or "").strip()
  scheduled_start = _scheduled_start_time()
  trading_date = (
    datetime.strptime(normalized_target, "%Y%m%d").date()
    if normalized_target
    else time_utils.to_shanghai(scheduled_start).date()
    if scheduled_start is not None
    else time_utils.today()
  )
  current_trading_date = time_utils.today()
  if trading_date != current_trading_date:
    raise RuntimeError(
      "持仓 Tick 归档拒绝隔日补跑: 仅保存了最新券商持仓投影，"
      "无法证明目标日收盘持仓集合；请使用已归档的目标日 universe 重建请求"
    )
  if not await TradingDateHelper().is_trading_date("SH", trading_date):
    result["market_data_sync"] = {
      "status": "skipped",
      "reason": "target date is not a trading date",
      "target_date": trading_date.strftime("%Y%m%d"),
    }
    return result

  position_universe = await _fresh_position_archive_universe()
  stock_codes = list(position_universe["stock_codes"])
  if not stock_codes:
    result["market_data_sync"] = {
      "status": "skipped",
      "reason": "no positive positions",
      "target_date": trading_date.strftime("%Y%m%d"),
    }
    return result

  compact_date = trading_date.strftime("%Y%m%d")
  transfer = await _request_and_wait(
    {
      "operation": "bars",
      "download": True,
      "stock_list": stock_codes,
      "periods": ["tick"],
      "start_time": compact_date,
      "end_time": compact_date,
    },
    agent_device_id=market_data_device_id,
    idempotency_scope=f"position-tick-archive-v1:{compact_date}",
  )
  if transfer.get("status") != "completed":
    raise RuntimeError(
      "持仓 Tick 同步失败: "
      f"request_id={transfer.get('request_id')} "
      f"status={transfer.get('status')}"
    )

  records_received = int(transfer.get("records_received") or 0)
  records_saved = int(transfer.get("records_saved") or 0)
  requested_codes = sorted(str(code) for code in transfer.get("requested_codes") or [])
  requested_periods = list(transfer.get("requested_periods") or [])
  summaries = list(transfer.get("code_summaries") or [])
  summary_pairs = sorted(
    (str(item.get("code") or ""), str(item.get("period") or ""))
    for item in summaries
  )
  summary_row_count = sum(int(item.get("row_count") or 0) for item in summaries)
  audited_empty_codes = sorted(
    str(item.get("code") or "")
    for item in summaries
    if int(item.get("row_count") or 0) == 0
  )
  empty_codes = sorted(str(code) for code in transfer.get("empty_codes") or [])
  expected_pairs = sorted((code, "tick") for code in stock_codes)
  if (
    requested_codes != stock_codes
    or requested_periods != ["tick"]
    or str(transfer.get("start_time") or "") != compact_date
    or str(transfer.get("end_time") or "") != compact_date
    or summary_pairs != expected_pairs
    or summary_row_count != records_received
    or empty_codes != audited_empty_codes
  ):
    raise RuntimeError(
      "持仓 Tick 入库审计与冻结请求不一致: "
      f"request_id={transfer.get('request_id')}"
    )
  if records_saved != records_received:
    raise RuntimeError(
      "持仓 Tick 数据未完整写入: "
      f"request_id={transfer.get('request_id')} "
      f"received={records_received} accepted={records_saved}"
    )
  if empty_codes:
    logger.warning(
      "Position Tick archive contains explicit no-data instruments: %s",
      empty_codes,
    )

  result["market_data_sync"] = {
    **transfer,
    "target_date": compact_date,
    "stock_count": len(stock_codes),
    "stock_codes": stock_codes,
    "position_snapshot": position_universe,
  }
  return result


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
  get_run_logger().warning("国债逆回购已持久化为 TradeCommand，等待 QMT Agent 投递")
  return {
    "status": str(queued.status).lower(),
    "client_order_id": queued.client_order_id,
    "message_id": queued.message_id,
  }
