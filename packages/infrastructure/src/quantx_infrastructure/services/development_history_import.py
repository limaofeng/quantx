"""Download, resume and verify history partitions into development storage."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from uuid import uuid4

import httpx
from quantx_application.market_data.ingestion import IngestionEvidenceConflict
from quantx_contracts.data_exchange import HistoryPartitionRequest
from sqlalchemy import text

from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.database.timeseries_connection import (
  NonRetryableWriteError,
  get_timeseries_connection,
)
from quantx_infrastructure.services.data_exchange import (
  content_path,
  export_root,
  get_export,
  submit,
  submit_in_transaction,
)
from quantx_infrastructure.services.development_delivery_execution import (
  run_delivery_execution,
)
from quantx_infrastructure.services.development_delivery_manifest import (
  MAX_DELIVERY_METADATA_BYTES,
  pin_delivery_manifest,
  read_delivery_metadata,
  validate_delivery_manifest,
)
from quantx_infrastructure.services.development_download_budget import (
  DeliveryDownloadBudgetExhausted,
  DeliveryRemoteUnavailable,
  DevelopmentDownloadBudget,
)
from quantx_infrastructure.services.development_ingestion_progress import (
  DevelopmentIngestionStore,
)
from quantx_infrastructure.services.market_data_persistence_verification import (
  MarketDataPersistenceBlockedError,
  MarketDataPersistenceMismatchError,
)
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  MAX_TRANSFER_CHUNK_COMPRESSED_BYTES,
  MarketDataValidationError,
)


class ImportedTransfer:
  def __init__(self, manifest: dict):
    self.manifest = manifest

  async def market_data_request(self, _identity):
    count = len(self.manifest["chunks"])
    return {
      "request_payload": self.manifest["payload"],
      "expected_chunks": count,
      "received_chunks": count,
      "status": "UPLOADED",
    }

  async def market_data_transfers(self, _identity):
    return [
      {**item, "storage_reference": str(content_path(item["checksum_sha256"]))}
      for item in self.manifest["chunks"]
    ]


@asynccontextmanager
async def _delivery_transaction(owner):
  async with AsyncSessionLocal() as db:
    if owner is not None:
      await owner._guard_ingestion_owner(db, lock=False)
    yield db
    if owner is not None:
      await owner._guard_ingestion_owner(db)
    await db.commit()


async def import_partition(request: HistoryPartitionRequest, *, owner=None) -> dict:
  if os.environ.get("ENV") != "development":
    raise ValueError("History import is development-only")

  async def execute(execution_owner):
    try:
      return await _import_partition_owned(request, owner=execution_owner)
    except DeliveryDownloadBudgetExhausted as exc:
      return {
        "id": exc.delivery_id,
        "status": "BLOCKED",
        "reason": "DELIVERY_DOWNLOAD_BUDGET_EXHAUSTED",
      }
    except DeliveryRemoteUnavailable as exc:
      return {
        "id": exc.delivery_id,
        "status": "WAITING_SOURCE",
        "reason": "DELIVERY_REMOTE_UNAVAILABLE",
      }

  return await run_delivery_execution(
    request, AsyncSessionLocal, execute, worker_owner=owner
  )


async def _import_partition_owned(
  request: HistoryPartitionRequest, *, owner=None
) -> dict:
  if os.environ.get("ENV") != "development":
    raise ValueError("History import is development-only")
  if owner is None:
    identity = await submit(request)
  else:
    async with _delivery_transaction(owner) as db:
      identity = await submit_in_transaction(request, db)
  local = await get_export(identity)
  if isinstance(local.get("manifest"), dict) and local["manifest"].get("version") == 1:
    return await _block_legacy_delivery(
      identity, owner, "SOURCE_PROVENANCE_MIGRATION_REQUIRED"
    )
  if local["state"] == "BLOCKED":
    return {"id": identity, "status": "BLOCKED", "reason": local["error"]}
  budget = DevelopmentDownloadBudget(AsyncSessionLocal, identity, owner=owner)
  schedule = await budget.schedule()
  if schedule["reason_code"]:
    return {"id": identity, "status": "BLOCKED", "reason": schedule["reason_code"]}
  if not schedule["due"]:
    waiting = (
      "WAITING_LOCAL_PROOF"
      if local["state"] == "WAITING_LOCAL_PROOF"
      else "WAITING_SOURCE"
    )
    return {"id": identity, "status": waiting, "reason": schedule["wait_reason"]}
  if local["state"] in {"LOCAL_VERIFIED", "WAITING_LOCAL_PROOF"}:
    return await _recheck_local_partition(identity, request, local["manifest"], budget)
  ingestion_store = DevelopmentIngestionStore(AsyncSessionLocal, identity, owner=owner)
  resumed = await ingestion_store.status()
  if resumed is not None:
    state = resumed["progress"]
    if state["blocked"] or not resumed["due"]:
      return {
        "id": identity,
        "status": "BLOCKED" if state["blocked"] else "WAITING_LOCAL_INGESTION",
        "reason": state["reason_code"],
      }
    return await _ingest_local_partition(
      identity, request, local["manifest"], ingestion_store
    )
  base = os.environ["QUANTX_MARKET_DATA_URL"].rstrip("/")
  headers = {"Authorization": f"Bearer {os.environ['QUANTX_MARKET_DATA_TOKEN']}"}
  async with httpx.AsyncClient(
    base_url=base, headers=headers, timeout=30, trust_env=False
  ) as client:
    if not schedule["remote_submitted"]:
      async with budget.attempt(MAX_DELIVERY_METADATA_BYTES):
        response = await read_delivery_metadata(
          client,
          "POST",
          "/market-data/v1/history",
          json=request.model_dump(mode="json"),
        )
      if response["id"] != identity:
        raise ValueError("Remote request identity mismatch")
      await budget.schedule("submitted")
    async with budget.attempt(MAX_DELIVERY_METADATA_BYTES):
      remote = await read_delivery_metadata(
        client, "GET", f"/market-data/v1/history/{identity}"
      )
    if remote.get("id") != identity:
      raise ValueError("Remote request identity mismatch")
    if remote["state"] == "EXPIRED":
      await budget.schedule("expired")
      return {"id": identity, "status": "BLOCKED", "reason": "DELIVERY_REMOTE_EXPIRED"}
    if remote["state"] != "READY":
      if remote["state"] == "INCOMPLETE":
        async with _delivery_transaction(owner) as db:
          await db.execute(
            text(
              "UPDATE development_data_export SET state='INCOMPLETE',error='SOURCE_INCOMPLETE' WHERE id=:id"
            ),
            {"id": identity},
          )
      elif remote["state"] in {"QUEUED", "WAITING_SOURCE"}:
        await budget.schedule("pending")
      return {"status": remote["state"], "id": identity, "reason": remote.get("error")}
    manifest = remote["manifest"]
    async with _delivery_transaction(owner) as db:
      await pin_delivery_manifest(db, identity, request, manifest)
    await budget.schedule("ready")
    export_root().mkdir(parents=True, exist_ok=True)
    for item in manifest["chunks"]:
      digest = item["checksum_sha256"]
      path = content_path(digest)
      if path.is_file() and path.stat().st_size == item["compressed_bytes"]:
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
          continue
      temporary = path.with_suffix(f".{identity}.{uuid4().hex}.part")
      hasher, size = hashlib.sha256(), 0
      try:
        async with (
          budget.attempt(item["compressed_bytes"]),
          client.stream(
            "GET", f"/market-data/v1/history/{identity}/chunks/{digest}"
          ) as download,
        ):
          download.raise_for_status()
          with temporary.open("wb") as target:
            async for block in download.aiter_bytes(chunk_size=65536):
              size += len(block)
              if size > min(
                item["compressed_bytes"], MAX_TRANSFER_CHUNK_COMPRESSED_BYTES
              ):
                raise ValueError("Remote chunk exceeds byte budget")
              target.write(block)
              hasher.update(block)
        if size != item["compressed_bytes"] or hasher.hexdigest() != digest:
          raise ValueError("Remote chunk checksum mismatch")
        temporary.replace(path)
      finally:
        temporary.unlink(missing_ok=True)
    return await _ingest_local_partition(identity, request, manifest, ingestion_store)


async def _block_legacy_delivery(
  identity, owner, reason="LOCAL_STORAGE_VERSION_MIGRATION_REQUIRED"
):
  # Preserve the old receipt, files, checkpoints and reserved attempts verbatim.
  async with _delivery_transaction(owner) as db:
    await db.execute(
      text(
        "UPDATE development_data_export SET state='BLOCKED',error=:reason,updated_at=clock_timestamp() WHERE id=:id"
      ),
      {"id": identity, "reason": reason},
    )
  return {"id": identity, "status": "BLOCKED", "reason": reason}


async def _ingest_local_partition(identity, request, manifest, ingestion_store):
  from .development_version_ingestion import ingest_development_storage_version
  from .market_data_ingestion_progress import evidence_hash

  if isinstance(manifest, dict) and manifest.get("version") == 1:
    return await _block_legacy_delivery(
      identity, ingestion_store.owner, "SOURCE_PROVENANCE_MIGRATION_REQUIRED"
    )
  prior = await ingestion_store.status()
  if prior is not None and isinstance(manifest, dict):
    legacy_hash = evidence_hash(
      {"payload": manifest.get("payload"), "chunks": manifest.get("chunks")}
    )
    if prior["progress"]["manifest_hash"] == legacy_hash:
      return await _block_legacy_delivery(identity, ingestion_store.owner)
  progress = await ingestion_store.begin()
  if progress.state["blocked"]:
    return {
      "id": identity,
      "status": "BLOCKED",
      "reason": progress.state["reason_code"],
    }
  try:
    validate_delivery_manifest(manifest, request)
    return await ingest_development_storage_version(
      request, manifest, progress, connection=get_timeseries_connection()
    )

  except Exception as exc:
    permanent = isinstance(
      exc,
      (
        MarketDataPersistenceBlockedError,
        NonRetryableWriteError,
        MarketDataValidationError,
        IngestionEvidenceConflict,
        ValueError,
        FileNotFoundError,
      ),
    )
    reason = (
      exc.reason_code
      if isinstance(exc, MarketDataPersistenceBlockedError)
      else "DEPENDENCY_WRITE_CAPACITY_BLOCKED"
      if isinstance(exc, NonRetryableWriteError)
      else "LOCAL_DELIVERY_PROOF_INVALID"
      if permanent
      else "PERSISTED_DATA_NOT_VISIBLE"
      if isinstance(exc, MarketDataPersistenceMismatchError)
      else "LOCAL_READBACK_UNAVAILABLE"
      if progress.state["phase"] == "READBACK"
      else "LOCAL_WRITE_UNAVAILABLE"
    )
    state = await progress.apply("defer", reason_code=reason, blocked=permanent)
    return {
      "id": identity,
      "status": "BLOCKED" if state["blocked"] else "WAITING_LOCAL_INGESTION",
      "reason": reason,
    }


async def _recheck_local_partition(identity, request, receipt, budget):
  from .development_version_ingestion import recheck_development_storage_version

  if isinstance(receipt, dict) and receipt.get("version") == 1:
    return await _block_legacy_delivery(
      identity, budget.owner, "SOURCE_PROVENANCE_MIGRATION_REQUIRED"
    )
  audit = receipt.get("local_verification") if isinstance(receipt, dict) else None
  if isinstance(audit, dict) and "immutable_storage" not in audit:
    return await _block_legacy_delivery(identity, budget.owner)
  return await recheck_development_storage_version(
    request, receipt, identity, budget, connection=get_timeseries_connection()
  )


async def request_partition_delivery(request: HistoryPartitionRequest) -> dict:
  """Submit/query the Data API; only its Worker advances downloads and ingestion."""
  from quantx_contracts.market_data_service import HistoryDemand

  from .local_market_data_client import LocalMarketDataClient

  client = LocalMarketDataClient()
  demand = HistoryDemand.model_validate(request.model_dump())
  result = None
  try:
    identity = await client.submit_history_demand(demand)
    status = await client.history_demand(identity, expected_partition=demand)
    if status is not None and status.delivery_status == "LOCAL_VERIFIED":
      result = await client.history_demand_result(identity, expected_partition=demand)
  finally:
    await client.close()
  if status is None:
    raise ValueError("Submitted history demand disappeared")
  if result is not None:
    if result.delivery_id != status.delivery_id:
      raise ValueError("Local history delivery identity changed")
    return {
      "id": result.delivery_id,
      "status": "LOCAL_VERIFIED",
      "delivery_result": result.model_dump(mode="json"),
    }
  return {
    "id": status.delivery_id or identity,
    "status": "WAITING_LOCAL_PROOF"
    if status.delivery_status == "LOCAL_VERIFIED"
    else status.delivery_status or "WAITING_SOURCE",
    "reason": "LOCAL_DELIVERY_PROOF_UNAVAILABLE"
    if status.delivery_status == "LOCAL_VERIFIED"
    else status.reason_code,
  }


async def run_range(instruments: list[str], period: str, start: date, end: date) -> int:
  if end < start:
    raise ValueError("Invalid date range")
  result = await request_remote_history(
    {
      "operation": "bars",
      "stock_list": instruments,
      "periods": [period],
      "start_time": start.strftime("%Y%m%d"),
      "end_time": end.strftime("%Y%m%d"),
    },
    timeout_seconds=0,
  )
  print(json.dumps(result, ensure_ascii=False, default=str))
  return 2 if result.get("status") == "failed" else 0


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--instruments", required=True)
  parser.add_argument("--period", choices=("tick", "1m", "1d"), required=True)
  parser.add_argument("--start", type=date.fromisoformat, required=True)
  parser.add_argument("--end", type=date.fromisoformat, required=True)
  args = parser.parse_args()
  raise SystemExit(
    asyncio.run(
      run_range(args.instruments.split(","), args.period, args.start, args.end)
    )
  )


async def _wait_reference_requests(requests, *, timeout_seconds):
  from .local_market_data_client import LocalMarketDataClient

  if not 1 <= len(requests) <= 5000:
    raise ValueError("reference request batch exceeds budget")
  client = LocalMarketDataClient()
  deadline = asyncio.get_running_loop().time() + timeout_seconds
  try:
    identities = [
      await client.submit_reference_request(request) for request in requests
    ]
    while True:
      statuses = [
        await client.reference_status(identity, expected_request=request)
        for identity, request in zip(identities, requests, strict=True)
      ]
      if any(status is None for status in statuses):
        raise ValueError("submitted reference request disappeared")
      if all(status.state == "VERIFIED" for status in statuses) or any(
        status.state == "BLOCKED" for status in statuses
      ):
        return statuses
      if asyncio.get_running_loop().time() >= deadline:
        return statuses
      await asyncio.sleep(1)
  finally:
    await client.close()


def _reference_wait_result(statuses):
  blocked = next((status for status in statuses if status.state == "BLOCKED"), None)
  if blocked or any(status.state != "VERIFIED" for status in statuses):
    return {
      "status": "failed" if blocked else "timeout",
      "reason": blocked.reason if blocked else "DEVELOPMENT_REFERENCE_PENDING",
      "reference_requests": [status.model_dump(mode="json") for status in statuses],
    }
  return None


async def request_remote_history(
  payload: dict, *, timeout_seconds: float = 600
) -> dict:
  """Adapt existing historical callers while keeping QMT requests off the Mac."""
  from datetime import datetime

  from quantx_contracts.development_reference import (
    CalendarRequest,
    FactorReferenceRequest,
  )

  from quantx_infrastructure.services.market_data_transfer_ingestion import (
    _parse_bars_request,
  )

  if payload.get("operation", "bars") != "bars":
    if payload.get("operation") == "divid_factors":
      codes = payload["stock_list"]
      if not isinstance(codes, list) or len(codes) != len(set(codes)):
        raise ValueError("factor request requires unique stock codes")
      requests = [
        FactorReferenceRequest(
          instrument=code,
          start_date=datetime.strptime(payload["start_time"], "%Y%m%d").date(),
          end_date=datetime.strptime(payload["end_time"], "%Y%m%d").date(),
        )
        for code in codes
      ]
      statuses = await _wait_reference_requests(
        requests, timeout_seconds=timeout_seconds
      )
      pending = _reference_wait_result(statuses)
      if pending:
        return pending
      count = sum(status.result.records_verified for status in statuses)
      return {
        "status": "success",
        "operation": "divid_factors",
        "records_received": count,
        "records_saved": count,
        "records_verified": count,
        "reference_requests": [status.model_dump(mode="json") for status in statuses],
      }
    return {
      "status": "failed",
      "reason": "REFERENCE_DATA_MUST_BE_IMPORTED_BEFORE_RESEARCH",
    }
  scope = _parse_bars_request(payload)
  start = datetime.strptime(payload["start_time"], "%Y%m%d").date()
  end = datetime.strptime(payload["end_time"], "%Y%m%d").date()
  if ((end - start).days + 1) * len(scope.groups) > 5000:
    raise ValueError(
      "Split history requests into at most 5000 code/day/period partitions"
    )
  from uuid import UUID

  identity = str(
    UUID(
      hex=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]
    )
  )
  deadline = asyncio.get_running_loop().time() + timeout_seconds
  calendars = await _wait_reference_requests(
    [CalendarRequest(year=year) for year in range(start.year, end.year + 1)],
    timeout_seconds=max(0, deadline - asyncio.get_running_loop().time()),
  )
  pending = _reference_wait_result(calendars)
  if pending:
    return {**pending, "request_id": identity}
  closed = {item.date for status in calendars for item in status.result.holidays}
  partitions = []
  day = start
  while day <= end:
    if day.weekday() < 5 and day not in closed:
      for period, code in scope.groups:
        partitions.append(
          HistoryPartitionRequest(instrument=code, period=period, trading_date=day)
        )
    day += timedelta(days=1)
  results = {}
  partition_status = {}
  while True:
    pending = False
    for request in partitions:
      key = (request.period, request.instrument, request.trading_date)
      if key in results:
        continue
      result = await request_partition_delivery(request)
      partition_status[key] = {
        **request.model_dump(mode="json"),
        "id": result.get("id"),
        "reason": result.get("reason"),
        "status": "LOCAL_VERIFIED"
        if "delivery_result" in result
        else result.get("status"),
      }
      if "delivery_result" in result:
        results[key] = result["delivery_result"]
      elif result.get("status") not in {"INCOMPLETE", "BLOCKED"}:
        pending = True
    # Finish submitting this bounded range even when one source partition failed.
    # Report every gap; never silently truncate the requested universe.
    progress = {
      "expected_partitions": len(partitions),
      "verified_partitions": len(results),
      "partitions": list(partition_status.values()),
    }
    if any(p["status"] in {"INCOMPLETE", "BLOCKED"} for p in partition_status.values()):
      return {
        "status": "failed",
        "reason": "DEVELOPMENT_SOURCE_INCOMPLETE"
        if any(p["status"] == "INCOMPLETE" for p in partition_status.values())
        else next(
          p["reason"] for p in partition_status.values() if p["status"] == "BLOCKED"
        ),
        "request_id": identity,
        **progress,
      }
    if not pending:
      break
    if asyncio.get_running_loop().time() >= deadline:
      return {
        "status": "timeout",
        "reason": "DEVELOPMENT_HISTORY_PENDING",
        "request_id": identity,
        **progress,
      }
    await asyncio.sleep(5)
  summaries = []
  for period, code in scope.groups:
    count = sum(
      result["records_verified"]
      for key, result in results.items()
      if key[:2] == (period, code)
    )
    if count == 0:
      return {
        "status": "failed",
        "reason": "DEVELOPMENT_SOURCE_EMPTY",
        "request_id": identity,
      }
    summaries.append({"code": code, "period": period, "row_count": count})
  records = sum(result["records_verified"] for result in results.values())
  return {
    "operation": "bars",
    "status": "success",
    "request_id": identity,
    "requested_codes": list(scope.codes),
    "requested_periods": list(scope.periods),
    "start_time": scope.start_text,
    "end_time": scope.end_text,
    "empty_codes": [],
    "code_summaries": summaries,
    "records_received": records,
    "records_saved": records,
    "records_verified": records,
    "data_versions": sorted({result["source_version"] for result in results.values()}),
    "partition_proofs": list(results.values()),
    **progress,
  }


if __name__ == "__main__":
  main()
