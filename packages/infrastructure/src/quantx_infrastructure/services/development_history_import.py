"""Download, resume and verify history partitions into development storage."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import date, timedelta

import httpx
from quantx_contracts.data_exchange import HistoryPartitionRequest
from sqlalchemy import text

from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.services.data_exchange import (
  content_path,
  export_root,
  get_export,
  submit,
)
from quantx_infrastructure.services.data_exchange_reference import import_reference
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  MAX_TRANSFER_CHUNK_COMPRESSED_BYTES,
  ingest_uploaded_bar_request,
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


async def import_partition(request: HistoryPartitionRequest) -> dict:
  if os.environ.get("ENV") != "development":
    raise ValueError("History import is development-only")
  identity = hashlib.sha256(request.model_dump_json().encode()).digest()
  lock_key = int.from_bytes(identity[:8], "big", signed=True)
  async with AsyncSessionLocal() as db:
    locked = await db.scalar(
      text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}
    )
    if not locked:
      return {"status": "IMPORT_IN_PROGRESS"}
    try:
      return await _import_partition_owned(request)
    finally:
      await db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})


async def _import_partition_owned(request: HistoryPartitionRequest) -> dict:
  if os.environ.get("ENV") != "development":
    raise ValueError("History import is development-only")
  identity = await submit(request)
  local = await get_export(identity)
  if local["state"] == "LOCAL_VERIFIED":
    return local["manifest"]
  base = os.environ["QUANTX_MARKET_DATA_URL"].rstrip("/")
  headers = {"Authorization": f"Bearer {os.environ['QUANTX_MARKET_DATA_TOKEN']}"}
  async with httpx.AsyncClient(
    base_url=base, headers=headers, timeout=30, trust_env=False
  ) as client:
    response = await client.post(
      "/market-data/v1/history", json=request.model_dump(mode="json")
    )
    response.raise_for_status()
    if response.json()["id"] != identity:
      raise ValueError("Remote request identity mismatch")
    response = await client.get(f"/market-data/v1/history/{identity}")
    response.raise_for_status()
    remote = response.json()
    if remote["state"] != "READY":
      if remote["state"] == "INCOMPLETE":
        async with AsyncSessionLocal() as db:
          await db.execute(
            text(
              "UPDATE development_data_export SET state='INCOMPLETE',error='SOURCE_INCOMPLETE' WHERE id=:id"
            ),
            {"id": identity},
          )
          await db.commit()
      return {"status": remote["state"], "id": identity, "reason": remote.get("error")}
    manifest = remote["manifest"]
    if manifest["payload"] != request.agent_payload():
      raise ValueError("Remote request scope mismatch")
    expected_version = hashlib.sha256(
      json.dumps(
        {"chunks": manifest["chunks"], "reference": manifest["reference"]},
        sort_keys=True,
      ).encode()
    ).hexdigest()
    if manifest.get("version") != 1 or manifest["data_version"] != expected_version:
      raise ValueError("Invalid remote data version")
    if not 0 < len(manifest["chunks"]) <= 128:
      raise ValueError("Invalid remote chunk count")
    export_root().mkdir(parents=True, exist_ok=True)
    for item in manifest["chunks"]:
      digest = item["checksum_sha256"]
      path = content_path(digest)
      if path.is_file() and path.stat().st_size <= MAX_TRANSFER_CHUNK_COMPRESSED_BYTES:
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
          continue
      temporary = path.with_suffix(".part")
      hasher, size = hashlib.sha256(), 0
      try:
        async with client.stream(
          "GET", f"/market-data/v1/history/{identity}/chunks/{digest}"
        ) as download:
          download.raise_for_status()
          with temporary.open("wb") as target:
            async for block in download.aiter_bytes():
              size += len(block)
              if size > MAX_TRANSFER_CHUNK_COMPRESSED_BYTES:
                raise ValueError("Remote chunk exceeds byte budget")
              target.write(block)
              hasher.update(block)
        if size != item["compressed_bytes"] or hasher.hexdigest() != digest:
          raise ValueError("Remote chunk checksum mismatch")
        temporary.replace(path)
      finally:
        temporary.unlink(missing_ok=True)
    audit = await ingest_uploaded_bar_request(ImportedTransfer(manifest), identity)
    await import_reference(manifest["reference"], code=request.instrument)
    receipt = {**manifest, "local_verification": audit}
    async with AsyncSessionLocal() as db:
      await db.execute(
        text("""
        UPDATE development_data_export SET state='LOCAL_VERIFIED',manifest=CAST(:manifest AS JSON),
        updated_at=CURRENT_TIMESTAMP WHERE id=:id
      """),
        {"id": identity, "manifest": json.dumps(receipt, default=str)},
      )
      await db.commit()
    return receipt


async def run_range(
  instruments: list[str], period: str, start: date, end: date
) -> int:
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
  raise SystemExit(asyncio.run(
    run_range(args.instruments.split(","), args.period, args.start, args.end)
  ))


async def request_remote_history(
  payload: dict, *, timeout_seconds: float = 600
) -> dict:
  """Adapt existing historical callers while keeping QMT requests off the Mac."""
  from datetime import datetime

  from quantx_contracts import HistoricalBarSummary, historical_bar_key

  from quantx_infrastructure.services.holiday_service import HolidayService
  from quantx_infrastructure.services.market_data_transfer_ingestion import (
    _BarTransferValidator,
    _iter_transfer_chunks,
    _parse_bars_request,
  )

  if payload.get("operation", "bars") != "bars":
    if payload.get("operation") == "divid_factors":
      async with httpx.AsyncClient(
        base_url=os.environ["QUANTX_MARKET_DATA_URL"],
        headers={"Authorization": f"Bearer {os.environ['QUANTX_MARKET_DATA_TOKEN']}"},
        timeout=30,
        trust_env=False,
      ) as client:
        count = 0
        for code in payload["stock_list"]:
          response = await client.get(
            f"/market-data/v1/reference/{code}",
            params={
              "as_of": datetime.strptime(payload["end_time"], "%Y%m%d")
              .date()
              .isoformat()
            },
          )
          response.raise_for_status()
          reference = response.json()
          proof = reference.get("factor_coverage", {})
          if (
            proof.get("status") != "VERIFIED"
            or proof["start_date"] > payload["start_time"]
            or proof["end_date"] < payload["end_time"]
          ):
            return {"status": "failed", "reason": "DIVID_FACTOR_COVERAGE_UNVERIFIED"}
          await import_reference(reference, code=code)
          count += len(reference["factors"])
      return {
        "status": "success",
        "operation": "divid_factors",
        "records_received": count,
        "records_saved": count,
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
  closed = set()
  for year in range(start.year, end.year + 1):
    holidays = await HolidayService().get_holidays("SH", year)
    if not holidays:
      async with httpx.AsyncClient(
        base_url=os.environ["QUANTX_MARKET_DATA_URL"],
        headers={"Authorization": f"Bearer {os.environ['QUANTX_MARKET_DATA_TOKEN']}"},
        timeout=30,
        trust_env=False,
      ) as client:
        response = await client.get(f"/market-data/v1/calendar/{year}")
        response.raise_for_status()
        calendar = response.json()
      rows = calendar.get("holidays", [])
      if (
        calendar.get("year") != year
        or calendar.get("market") != "SH"
        or not 0 < len(rows) <= 366
      ):
        raise ValueError("Invalid source calendar")
      for row in rows:
        if (
          set(row) != {"date", "description"}
          or date.fromisoformat(row["date"]).year != year
        ):
          raise ValueError("Invalid source calendar day")
      holidays = await HolidayService().bulk_save_holidays(
        "SH", year, [{**row, "date": date.fromisoformat(row["date"])} for row in rows]
      )
    closed.update(item.date for item in holidays)
  partitions = []
  day = start
  while day <= end:
    if day.weekday() < 5 and day not in closed:
      for period, code in scope.groups:
        partitions.append(
          HistoryPartitionRequest(instrument=code, period=period, trading_date=day)
        )
    day += timedelta(days=1)
  receipts = {}
  partition_status = {}
  while True:
    pending = False
    for request in partitions:
      key = (request.period, request.instrument, request.trading_date)
      if key in receipts:
        continue
      result = await import_partition(request)
      partition_status[key] = {
        **request.model_dump(mode="json"),
        "id": result.get("id"),
        "reason": result.get("reason"),
        "status": "LOCAL_VERIFIED"
        if "local_verification" in result else result.get("status"),
      }
      if "local_verification" in result:
        receipts[key] = result
      elif result.get("status") != "INCOMPLETE":
        pending = True
    # Finish submitting this bounded range even when one source partition failed.
    # Report every gap; never silently truncate the requested universe.
    progress = {
      "expected_partitions": len(partitions),
      "verified_partitions": len(receipts),
      "partitions": list(partition_status.values()),
    }
    if any(p["status"] == "INCOMPLETE" for p in partition_status.values()):
      return {
        "status": "failed",
        "reason": "DEVELOPMENT_SOURCE_INCOMPLETE",
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
  validator = _BarTransferValidator(scope)
  coverage, versions = [], []
  for period, code in scope.groups:
    count, first, last, digest = 0, None, None, hashlib.sha256()
    for key, receipt in sorted(receipts.items()):
      if key[:2] != (period, code):
        continue
      files = await ImportedTransfer(receipt).market_data_transfers(identity)
      for chunk in _iter_transfer_chunks(files):
        for record in chunk:
          if "record_type" in record:
            continue
          validator.consume(record)
          if count:
            digest.update(b"\n")
          digest.update(
            historical_bar_key(
              code=code,
              period=period,
              time_ms=record["time"],
              tick_ordinal=record.get("tick_ordinal"),
            ).encode()
          )
          first = record["time"] if first is None else first
          last, count = record["time"], count + 1
      coverage.extend(receipt["local_verification"].get("day_coverage", []))
      versions.append(receipt["data_version"])
    if count == 0:
      return {
        "status": "failed",
        "reason": "DEVELOPMENT_SOURCE_EMPTY",
        "request_id": identity,
      }
    validator.consume(
      HistoricalBarSummary(
        code=code,
        period=period,
        row_count=count,
        min_time=first,
        max_time=last,
        key_sha256=digest.hexdigest(),
        no_data_reason=None,
      ).model_dump(mode="json")
    )
  audit = validator.finish()
  return {
    **audit,
    "status": "success",
    "request_id": identity,
    "day_coverage": coverage,
    "records_verified": audit["records_received"],
    "data_versions": sorted(set(versions)),
    **progress,
  }


if __name__ == "__main__":
  main()
