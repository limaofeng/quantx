"""Worker-owned, one-day-at-a-time recovery planning from durable Engine scopes."""

import asyncio
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from quantx_contracts.development_reference import CalendarSnapshot
from quantx_contracts.market_data_service import HistoryDemand
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
