"""Durable minute revisions and bounded attempts; PostgreSQL decides visibility."""

import asyncio
import json
from contextlib import asynccontextmanager

from quantx_contracts.realtime_archive import (
  MAX_ARCHIVE_PENDING,
  MAX_ARCHIVE_REQUEST_BYTES,
  ArchiveRevision,
  ArchiveStatus,
)
from sqlalchemy import text

from .engine_archive_generation import verify_engine_archive_generation


class ArchiveRejected(ValueError):
  pass


class ArchiveCapacity(RuntimeError):
  pass


class RealtimeArchiveStore:
  def __init__(self, engine):
    self.engine = engine

  async def submit(self, request: ArchiveRevision):
    identity = request.identity()
    encoded = request.model_dump_json()
    if len(encoded.encode()) > MAX_ARCHIVE_REQUEST_BYTES:
      raise ArchiveCapacity("ARCHIVE_REQUEST_TOO_LARGE")
    async with asyncio.timeout(3), self.engine.begin() as db:
      await db.execute(text("SELECT pg_advisory_xact_lock(817234595)"))
      existing = await db.scalar(
        text("SELECT request FROM realtime_archive_revision WHERE request_id=:id"),
        {"id": identity},
      )
      if existing is not None:
        if existing != request.model_dump(mode="json"):
          raise ArchiveRejected("ARCHIVE_CONTENT_CONFLICT")
        return identity
      try:
        await verify_engine_archive_generation(db, request.generation)
      except RuntimeError:
        raise ArchiveRejected("ARCHIVE_GENERATION_INACTIVE") from None
      stream = (
        (
          await db.execute(
            text("""
        SELECT continuity_generation,stream_id FROM realtime_archive_stream
        WHERE generation=:generation ORDER BY continuity_generation DESC LIMIT 1
      """),
            {"generation": request.generation},
          )
        )
        .mappings()
        .one_or_none()
      )
      if stream and (
        stream["continuity_generation"] > request.continuity_generation
        or stream["continuity_generation"] == request.continuity_generation
        and stream["stream_id"] != str(request.stream_id)
      ):
        raise ArchiveRejected("ARCHIVE_STREAM_CONFLICT")
      previous = (
        (
          await db.execute(
            text("""
        SELECT generation,continuity_generation,sequence,sealed FROM realtime_archive_revision
        WHERE instrument=:instrument AND minute=:minute
        ORDER BY generation DESC,continuity_generation DESC,sequence DESC,sealed DESC LIMIT 1
      """),
            {"instrument": request.instrument, "minute": request.minute},
          )
        )
        .mappings()
        .one_or_none()
      )
      revision = (
        request.generation,
        request.continuity_generation,
        request.sequence,
        request.sealed,
      )
      if previous:
        old = tuple(
          previous[key]
          for key in ("generation", "continuity_generation", "sequence", "sealed")
        )
        if revision <= old:
          raise ArchiveRejected("ARCHIVE_REVISION_STALE")
        if previous["sealed"] and (not request.sealed or old[:2] == revision[:2]):
          raise ArchiveRejected("ARCHIVE_MINUTE_SEALED")
      count = await db.scalar(
        text("""
        SELECT count(*) FROM (SELECT 1 FROM realtime_archive_revision
        WHERE phase IN ('WRITE','READBACK') LIMIT :limit) pending
      """),
        {"limit": MAX_ARCHIVE_PENDING},
      )
      if count >= MAX_ARCHIVE_PENDING:
        raise ArchiveCapacity("ARCHIVE_PENDING_CAPACITY")
      await db.execute(
        text("""
        INSERT INTO realtime_archive_stream(generation,continuity_generation,stream_id)
        VALUES (:generation,:continuity,:stream) ON CONFLICT DO NOTHING
      """),
        {
          "generation": request.generation,
          "continuity": request.continuity_generation,
          "stream": str(request.stream_id),
        },
      )
      await db.execute(
        text("""
        INSERT INTO realtime_archive_revision(request_id,request,instrument,minute,generation,continuity_generation,sequence,sealed)
        VALUES (:id,CAST(:request AS JSONB),:instrument,:minute,:generation,:continuity,:sequence,:sealed)
      """),
        {
          "id": identity,
          "request": encoded,
          "instrument": request.instrument,
          "minute": request.minute,
          "generation": request.generation,
          "continuity": request.continuity_generation,
          "sequence": request.sequence,
          "sealed": request.sealed,
        },
      )
      await verify_engine_archive_generation(db, request.generation)
      return identity

  async def status(self, identity):
    async with asyncio.timeout(3), self.engine.connect() as db:
      row = (
        (
          await db.execute(
            text("""
        SELECT request_id,request,phase,write_attempts,read_attempts,reason,next_retry_at,proof
        FROM realtime_archive_revision WHERE request_id=:id
      """),
            {"id": identity},
          )
        )
        .mappings()
        .one_or_none()
      )
      return ArchiveStatus.model_validate(dict(row)) if row else None

  @asynccontextmanager
  async def owned(self, owner):
    async with asyncio.timeout(3), self.engine.begin() as db:
      await owner._guard_ingestion_owner(db)
      yield db
      await owner._guard_ingestion_owner(db)

  async def claim(self, owner):
    async with self.owned(owner) as db:
      row = (
        (
          await db.execute(
            text("""
        SELECT * FROM realtime_archive_revision
        WHERE phase IN ('WRITE','READBACK') AND next_retry_at <= clock_timestamp()
        ORDER BY next_retry_at,created_at,request_id LIMIT 1 FOR UPDATE SKIP LOCKED
      """)
          )
        )
        .mappings()
        .one_or_none()
      )
      if row is None:
        return None
      row = dict(row)
      counter = "write_attempts" if row["phase"] == "WRITE" else "read_attempts"
      if row[counter] >= 4:
        await db.execute(
          text(
            "UPDATE realtime_archive_revision SET phase='BLOCKED',reason='ARCHIVE_ATTEMPTS_EXHAUSTED' WHERE request_id=:id"
          ),
          {"id": row["request_id"]},
        )
        return None
      row[counter] += 1
      await db.execute(
        text(f"""
        UPDATE realtime_archive_revision SET {counter}=:{counter},next_retry_at=clock_timestamp()+INTERVAL '30 seconds'
        WHERE request_id=:request_id
      """),
        row,
      )
      return row

  async def finish(self, owner, claim, *, phase, reason=None, proof=None):
    async with self.owned(owner) as db:
      attempts = (
        claim["write_attempts"] if claim["phase"] == "WRITE" else claim["read_attempts"]
      )
      if phase == claim["phase"] and attempts >= 4:
        phase, reason = "BLOCKED", "ARCHIVE_ATTEMPTS_EXHAUSTED"
      delay = (5, 30, 120, 240)[max(0, attempts - 1)] if phase == claim["phase"] else 0
      result = await db.execute(
        text("""
        UPDATE realtime_archive_revision SET phase=:phase,reason=:reason,proof=CAST(:proof AS JSONB),
          next_retry_at=clock_timestamp()+:delay * INTERVAL '1 second'
        WHERE request_id=:id AND phase=:old_phase AND write_attempts=:writes AND read_attempts=:reads
      """),
        {
          "id": claim["request_id"],
          "phase": phase,
          "reason": reason,
          "proof": json.dumps(proof) if proof is not None else None,
          "delay": delay,
          "old_phase": claim["phase"],
          "writes": claim["write_attempts"],
          "reads": claim["read_attempts"],
        },
      )
      if result.rowcount != 1:
        raise RuntimeError("ARCHIVE_CLAIM_LOST")

  async def published(self, instrument, minute):
    # Rank accepted revisions first. An unverified newest revision does not
    # authorize falling back to an older one that now describes stale content.
    async with asyncio.timeout(3), self.engine.connect() as db:
      row = (
        (
          await db.execute(
            text("""
        SELECT request_id,request,phase,write_attempts,read_attempts,reason,next_retry_at,proof
        FROM realtime_archive_revision WHERE request_id=(
          SELECT request_id FROM realtime_archive_revision
          WHERE instrument=:instrument AND minute=:minute
          ORDER BY generation DESC,continuity_generation DESC,sequence DESC,sealed DESC LIMIT 1
        ) AND phase='VERIFIED'
      """),
            {"instrument": instrument, "minute": minute},
          )
        )
        .mappings()
        .one_or_none()
      )
      return ArchiveStatus.model_validate(dict(row)) if row else None
