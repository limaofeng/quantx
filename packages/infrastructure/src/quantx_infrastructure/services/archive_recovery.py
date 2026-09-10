"""Worker-owned, one-day-at-a-time recovery planning from durable Engine scopes."""

import asyncio
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from quantx_contracts.development_reference import CalendarSnapshot
from quantx_contracts.market_data_service import HistoryDemand, HistoryRead
from sqlalchemy import text

from .engine_archive_generation import verify_engine_archive_generation
from .market_data_demand_store import MarketDataDemandCapacity

SHANGHAI = ZoneInfo("Asia/Shanghai")


async def _session_evidence(db, day):
  if day.weekday() >= 5:
    return {"kind": "A_SHARE_WEEKEND", "date": str(day)}
  rows = (
    (
      await db.execute(
        text("""
    SELECT date,description FROM holidays WHERE market='SH' AND year=:year
    ORDER BY date LIMIT 367
  """),
        {"year": day.year},
      )
    )
    .mappings()
    .all()
  )
  # Absence of calendar data is not evidence of a trading session or a holiday.
  snapshot = CalendarSnapshot(year=day.year, holidays=[dict(row) for row in rows])
  for item in snapshot.holidays:
    if item.date == day:
      return {
        "kind": "CALENDAR_HOLIDAY",
        "market": snapshot.market,
        "year": snapshot.year,
        "holiday": item.model_dump(mode="json"),
      }
  return None


async def advance_archive_recovery(owner):
  planned = await _plan_archive_recovery(owner)
  verified = await verify_archive_recovery(owner)
  return planned or verified


async def _plan_archive_recovery(owner):
  """No native IO here. Linking a demand does not verify or close a data gap."""
  async with asyncio.timeout(3), owner.engine.begin() as db:
    await owner._guard_ingestion_owner(db)
    row = (
      (
        await db.execute(
          text("""
      SELECT * FROM engine_archive_scope
      WHERE next_probe_at<=clock_timestamp()
        AND (ended_at IS NULL OR next_day<=(ended_at AT TIME ZONE 'Asia/Shanghai')::date)
      ORDER BY next_probe_at,generation,instrument LIMIT 1 FOR UPDATE SKIP LOCKED
    """)
        )
      )
      .mappings()
      .one_or_none()
    )
    if row is None:
      return False
    now = await db.scalar(text("SELECT clock_timestamp()"))
    ended_at = row["ended_at"]
    if ended_at is None:
      try:
        await verify_engine_archive_generation(db, row["generation"])
      except RuntimeError:
        # Use observation time, not a guessed last-tick time: include any
        # unconfirmed tail that disappeared with the old process/connection.
        ended_at = now
    day = row["next_day"]
    ready_at = datetime.combine(day, time(15, 1), SHANGHAI)
    reason = None
    next_day = day
    next_probe = now + timedelta(seconds=1)
    if ready_at > now:
      reason, next_probe = "WAITING_SESSION_CLOSE", ready_at
    else:
      try:
        evidence = await _session_evidence(db, day)
      except ValidationError:
        reason, next_probe = "WAITING_CALENDAR", now + timedelta(minutes=5)
      else:
        demand_id = None
        if evidence is not None:
          start = datetime.combine(day, time(), SHANGHAI)
          observed = await db.scalar(
            text("""
            SELECT EXISTS(SELECT 1 FROM realtime_archive_revision
            WHERE instrument=:instrument AND minute>=:start AND minute<:end)
          """),
            {
              "instrument": row["instrument"],
              "start": start,
              "end": start + timedelta(days=1),
            },
          )
          if observed:
            reason, next_probe = "CALENDAR_ARCHIVE_CONFLICT", now + timedelta(minutes=5)
        if evidence is None:
          try:
            demand_id = await owner.submit_history_demand(
              HistoryDemand(
                instrument=row["instrument"], period="1m", trading_date=day
              ),
              _connection=db,
            )
          except MarketDataDemandCapacity:
            reason, next_probe = "HISTORY_DEMAND_CAPACITY", now + timedelta(seconds=30)
        if reason is None:
          await db.execute(
            text("""
            INSERT INTO engine_archive_recovery(generation,instrument,trading_date,demand_id,state,evidence)
            VALUES (:generation,:instrument,:day,:demand,:state,CAST(:evidence AS JSONB))
          """),
            {
              "generation": row["generation"],
              "instrument": row["instrument"],
              "day": day,
              "demand": demand_id,
              "state": "NO_SESSION" if evidence is not None else "WAITING",
              "evidence": json.dumps(evidence) if evidence is not None else None,
            },
          )
          next_day = day + timedelta(days=1)
    await db.execute(
      text("""
      UPDATE engine_archive_scope SET ended_at=:ended,next_day=:day,next_probe_at=:probe,reason=:reason
      WHERE generation=:generation AND instrument=:instrument
    """),
      {
        "ended": ended_at,
        "day": next_day,
        "probe": next_probe,
        "reason": reason,
        "generation": row["generation"],
        "instrument": row["instrument"],
      },
    )
    await owner._guard_ingestion_owner(db)
    return True


async def verify_archive_recovery(owner):
  """Close one due day using a published version; never create or replace a source."""
  from .local_history_reader import HistoryReadInvalid
  from .native_bar_publication import resolve_native_bar_version

  async with asyncio.timeout(3), owner.engine.begin() as db:
    await owner._guard_ingestion_owner(db)
    row = (
      (
        await db.execute(
          text("""
        SELECT r.*,d.source_kind,d.source_request_id,d.delivery_id FROM engine_archive_recovery r
        JOIN market_data_demand d ON d.demand_id=r.demand_id
        WHERE r.state='WAITING' AND r.next_probe_at<=clock_timestamp()
        ORDER BY r.next_probe_at,r.generation,r.instrument,r.trading_date
        LIMIT 1 FOR UPDATE OF r SKIP LOCKED
      """)
        )
      )
      .mappings()
      .one_or_none()
    )
    if row is None:
      return False
    reason, evidence = "WAITING_NATIVE_VERSION", None
    if row["source_kind"] != "AGENT":
      from quantx_contracts.data_exchange import HistoryPartitionRequest

      from .development_bar_publication import resolve_remote_session_proof

      reason = "WAITING_REMOTE_SESSION_PROOF"
      try:
        remote = await resolve_remote_session_proof(
          db,
          HistoryPartitionRequest(
            instrument=row["instrument"], period="1m", trading_date=row["trading_date"]
          ),
          row["delivery_id"],
        )
      except ValueError:
        reason = "REMOTE_SESSION_PROOF_INVALID"
      else:
        if remote is not None:
          evidence = {
            **remote,
            "schema_version": 1,
            "demand_id": row["demand_id"],
            "original_source_request_id": row["source_request_id"],
            "instrument": row["instrument"],
            "period": "1m",
            "trading_date": row["trading_date"].isoformat(),
          }
          reason = None
    else:
      try:
        version = await resolve_native_bar_version(
          db,
          HistoryRead(
            instrument=row["instrument"], period="1m", trading_date=row["trading_date"]
          ),
        )
      except HistoryReadInvalid as exc:
        reason = str(exc)
      else:
        if version is not None:
          if not version.full_session:
            reason = "WAITING_FULL_SESSION_VERSION"
          else:
            evidence = {
              "schema_version": 1,
              "kind": "NATIVE_FULL_SESSION_VERSION",
              "demand_id": row["demand_id"],
              "original_source_request_id": row["source_request_id"],
              "proof_source_request_id": version.source_request_id,
              "instrument": row["instrument"],
              "period": "1m",
              "trading_date": row["trading_date"].isoformat(),
              "storage_version": version.storage_version,
              "content_sha256": version.content_sha256,
              "source_records_verified": version.records_verified,
              "source_fields_verified": version.fields_verified,
              "source_created_at": version.source_created_at.isoformat(),
            }
            reason = None
    await db.execute(
      text("""
      UPDATE engine_archive_recovery SET state=:state,evidence=CAST(:evidence AS JSONB),
        verified_at=CASE WHEN :verified THEN clock_timestamp() ELSE NULL END,
        next_probe_at=clock_timestamp()+interval '5 minutes',reason=:reason
      WHERE generation=:generation AND instrument=:instrument AND trading_date=:day
    """),
      {
        "generation": row["generation"],
        "instrument": row["instrument"],
        "day": row["trading_date"],
        "state": "VERIFIED" if evidence else "WAITING",
        "verified": evidence is not None,
        "evidence": json.dumps(evidence) if evidence else None,
        "reason": reason,
      },
    )
    await owner._guard_ingestion_owner(db)
    return evidence is not None
