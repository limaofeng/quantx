"""Worker-only reference import with durable attempts and frozen source snapshots."""

import asyncio
import hashlib
import json
import os

import httpx
from quantx_contracts.development_reference import (
  REFERENCE_REQUEST,
  CalendarRequest,
  CalendarSnapshot,
  FactorReferenceResult,
  ReferenceStatus,
)
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from quantx_infrastructure.models.holidays import Holiday

from .data_exchange_reference import (
  _parse_imported_reference,
  import_reference_in_transaction,
)
from .development_delivery_manifest import read_delivery_metadata


class ReferenceCapacity(ValueError):
  pass


def validate_source(request, source):
  if isinstance(request, CalendarRequest):
    snapshot = CalendarSnapshot.model_validate(source)
    if (snapshot.year, snapshot.market) != (request.year, request.market):
      raise ValueError("calendar response scope changed")
    return
  if (
    set(source) != {"as_of", "instrument", "holidays", "factors", "factor_coverage"}
    or not isinstance(source["factors"], list)
    or len(source["factors"]) > 10000
  ):
    raise ValueError("invalid reference fields or row budget")
  proof = source["factor_coverage"]
  if (
    set(proof)
    != {
      "status",
      "start_date",
      "end_date",
      "evidence",
      "expected_chunks",
      "received_chunks",
    }
    or source["as_of"] != request.end_date.isoformat()
    or proof["status"] != "VERIFIED"
    or proof["start_date"] > request.start_date.strftime("%Y%m%d")
    or proof["end_date"] < request.end_date.strftime("%Y%m%d")
  ):
    raise ValueError("factor coverage does not contain the requested window")
  _parse_imported_reference(source, code=request.instrument)


class DevelopmentReferenceStore:
  def __init__(self, engine, owner=None):
    self.factory = async_sessionmaker(engine)
    self.owner = owner

  async def submit(self, request):
    request = REFERENCE_REQUEST.validate_python(request)
    encoded = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    identity = hashlib.sha256(
      ("development-reference-v1:" + encoded).encode()
    ).hexdigest()
    async with asyncio.timeout(3), self.factory() as db:
      await db.execute(text("SELECT pg_advisory_xact_lock(817234594)"))
      exists = await db.scalar(
        text(
          "SELECT request_id FROM development_reference_request WHERE request_id=:id"
        ),
        {"id": identity},
      )
      if not exists:
        count = await db.scalar(
          text(
            "SELECT count(*) FROM (SELECT 1 FROM development_reference_request WHERE state IN ('QUEUED','WAITING') LIMIT 5000) pending"
          )
        )
        if count >= 5000:
          raise ReferenceCapacity("reference request capacity exhausted")
        await db.execute(
          text(
            "INSERT INTO development_reference_request(request_id,request) VALUES (:id,CAST(:request AS JSONB))"
          ),
          {"id": identity, "request": encoded},
        )
      await db.commit()
    return identity

  async def status(self, identity):
    async with asyncio.timeout(3), self.factory() as db:
      row = (
        (
          await db.execute(
            text(
              "SELECT request_id,request,state,attempts,reason,next_probe_at,result FROM development_reference_request WHERE request_id=:id"
            ),
            {"id": identity},
          )
        )
        .mappings()
        .one_or_none()
      )
    return ReferenceStatus.model_validate(dict(row)) if row else None

  async def claim(self):
    async with self.factory() as db:
      await self.owner._guard_ingestion_owner(db)
      row = (
        (
          await db.execute(
            text("""
        SELECT * FROM development_reference_request
        WHERE state IN ('QUEUED','WAITING') AND next_probe_at <= clock_timestamp()
        ORDER BY next_probe_at,updated_at,request_id LIMIT 1 FOR UPDATE SKIP LOCKED
      """)
          )
        )
        .mappings()
        .one_or_none()
      )
      if row is None:
        return None
      if row["attempts"] >= 4:
        await db.execute(
          text(
            "UPDATE development_reference_request SET state='BLOCKED',reason='REFERENCE_RETRY_BUDGET_EXHAUSTED',updated_at=clock_timestamp() WHERE request_id=:id"
          ),
          {"id": row["request_id"]},
        )
        await self.owner._guard_ingestion_owner(db)
        await db.commit()
        return None
      value = dict(row)
      value["attempts"] += 1
      await db.execute(
        text(
          "UPDATE development_reference_request SET attempts=:attempt,state='WAITING',next_probe_at=clock_timestamp()+INTERVAL '30 seconds',updated_at=clock_timestamp() WHERE request_id=:id"
        ),
        {"id": value["request_id"], "attempt": value["attempts"]},
      )
      await self.owner._guard_ingestion_owner(db)
      await db.commit()
      return value

  async def guard(self, db, item):
    await self.owner._guard_ingestion_owner(db)
    row = (
      (
        await db.execute(
          text(
            "SELECT * FROM development_reference_request WHERE request_id=:id AND attempts=:attempt AND state='WAITING' FOR UPDATE"
          ),
          {"id": item["request_id"], "attempt": item["attempts"]},
        )
      )
      .mappings()
      .one_or_none()
    )
    if row is None:
      raise RuntimeError("reference execution was superseded")
    return row

  async def pin(self, item, source):
    async with self.factory() as db:
      row = await self.guard(db, item)
      if row["source"] is not None and row["source"] != source:
        raise ValueError("reference source changed")
      await db.execute(
        text(
          "UPDATE development_reference_request SET source=CAST(:source AS JSONB) WHERE request_id=:id"
        ),
        {"id": item["request_id"], "source": json.dumps(source, allow_nan=False)},
      )
      await self.owner._guard_ingestion_owner(db)
      await db.commit()

  async def fail(self, item, *, reason, permanent):
    async with self.factory() as db:
      await self.guard(db, item)
      await db.execute(
        text("""
        UPDATE development_reference_request SET state=:state,reason=:reason,
          next_probe_at=clock_timestamp()+make_interval(secs => :delay),updated_at=clock_timestamp()
        WHERE request_id=:id
      """),
        {
          "id": item["request_id"],
          "state": "BLOCKED" if permanent or item["attempts"] >= 4 else "WAITING",
          "reason": reason,
          "delay": min(900, 30 * 2 ** (item["attempts"] - 1)),
        },
      )
      await self.owner._guard_ingestion_owner(db)
      await db.commit()

  async def complete(self, item, request, source):
    validate_source(request, source)
    async with self.factory() as db:
      row = await self.guard(db, item)
      if row["source"] != source:
        raise ValueError("reference source is not pinned")
      if isinstance(request, CalendarRequest):
        result = await _import_calendar(db, request, source)
      else:
        audit = await import_reference_in_transaction(
          await db.connection(), source, code=request.instrument, owner=self.owner
        )
        count = sum(
          request.start_date.strftime("%Y%m%d")
          <= item["ex_date"]
          <= request.end_date.strftime("%Y%m%d")
          for item in source["factors"]
        )
        result = FactorReferenceResult(
          **request.model_dump(),
          records_verified=count,
          content_sha256=audit["reference_sha256"],
        )
      await db.execute(
        text(
          "UPDATE development_reference_request SET state='VERIFIED',reason=NULL,result=CAST(:result AS JSONB),updated_at=clock_timestamp() WHERE request_id=:id"
        ),
        {"id": item["request_id"], "result": result.model_dump_json()},
      )
      await self.owner._guard_ingestion_owner(db)
      await db.commit()


async def _import_calendar(db, request, source):
  snapshot = CalendarSnapshot.model_validate(source)
  if (snapshot.year, snapshot.market) != (request.year, request.market):
    raise ValueError("calendar response scope changed")
  query = (
    select(Holiday.date, Holiday.description)
    .where(Holiday.market == request.market, Holiday.year == request.year)
    .order_by(Holiday.date)
    .limit(367)
  )
  existing = (await db.execute(query)).all()
  expected = {item.date: item.description for item in snapshot.holidays}
  if len(existing) > 366 or len(existing) != len({row.date for row in existing}):
    raise ValueError("calendar storage contains duplicate or excessive rows")
  if any(
    day not in expected or description != expected[day] for day, description in existing
  ):
    raise ValueError("calendar conflicts with pinned source")
  present = {row.date for row in existing}
  values = [
    {
      "market": request.market,
      "year": request.year,
      "date": day,
      "description": description,
    }
    for day, description in expected.items()
    if day not in present
  ]
  if values:
    await db.execute(insert(Holiday), values)
  if list((await db.execute(query)).all()) != sorted(expected.items()):
    raise ValueError("calendar readback mismatch")
  return snapshot


async def advance_reference_request(owner):
  if owner.demand_source_kind != "REMOTE":
    return False
  base = os.environ.get("QUANTX_MARKET_DATA_URL", "").rstrip("/")
  token = os.environ.get("QUANTX_MARKET_DATA_TOKEN", "")
  if not base or not token:
    raise RuntimeError("REFERENCE_SOURCE_CONFIGURATION_UNAVAILABLE")
  store = DevelopmentReferenceStore(owner.engine, owner)
  item = await store.claim()
  if item is None:
    return False
  try:
    request = REFERENCE_REQUEST.validate_python(item["request"])
    source = item["source"]
    if source is None:
      async with (
        asyncio.timeout(30),
        httpx.AsyncClient(
        base_url=base,
        headers={"Authorization": "Bearer " + token},
          timeout=30,
          trust_env=False,
        ) as client,
      ):
        path = (
          f"/market-data/v1/calendar/{request.year}"
          if isinstance(request, CalendarRequest)
          else f"/market-data/v1/reference/{request.instrument}"
        )
        kwargs = (
          {}
          if isinstance(request, CalendarRequest)
          else {"params": {"as_of": request.end_date.isoformat()}}
        )
        source = await read_delivery_metadata(client, "GET", path, **kwargs)
      validate_source(request, source)
      await store.pin(item, source)
    await store.complete(item, request, source)
  except (httpx.TransportError, TimeoutError):
    await store.fail(item, reason="REFERENCE_SOURCE_UNAVAILABLE", permanent=False)
  except httpx.HTTPStatusError as exc:
    await store.fail(
      item,
      reason="REFERENCE_SOURCE_UNAVAILABLE"
      if exc.response.status_code >= 500 or exc.response.status_code == 429
      else "REFERENCE_SOURCE_REJECTED",
      permanent=exc.response.status_code < 500 and exc.response.status_code != 429,
    )
  except (ValueError, KeyError, TypeError, ArithmeticError):
    await store.fail(item, reason="REFERENCE_SOURCE_INVALID", permanent=True)
  return True
