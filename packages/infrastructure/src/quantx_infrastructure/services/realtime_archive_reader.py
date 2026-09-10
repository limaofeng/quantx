"""Select committed minute revisions before bounded historical storage reads."""

import asyncio
import json
import time

from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_contracts.market_data_service import HistoryPage
from quantx_contracts.realtime_archive import ArchiveStatus
from sqlalchemy import text

from .development_bar_publication import resolve_published_bar_version
from .local_history_reader import HistoryReadBusy, HistoryReadInvalid
from .native_bar_publication import resolve_native_bar_version

MAX_ARCHIVE_MINUTES = 1440
MAX_ARCHIVE_DIRECTORY_BYTES = 4 * 1024 * 1024


async def _archive_heads(db, request):
  start, end = request.bounds()
  rows = (
    (
      await db.execute(
        text("""
    SELECT DISTINCT ON (minute) * FROM realtime_archive_revision
    WHERE instrument=:instrument AND minute>=:start AND minute<:end
    ORDER BY minute,generation DESC,continuity_generation DESC,sequence DESC,sealed DESC
    LIMIT :limit
  """),
        {
          "instrument": request.instrument,
          "start": start,
          "end": end,
          "limit": MAX_ARCHIVE_MINUTES + 1,
        },
      )
    )
    .mappings()
    .all()
  )
  if len(rows) > MAX_ARCHIVE_MINUTES:
    raise HistoryReadInvalid("ARCHIVE_DIRECTORY_CAPACITY")
  used, heads = 0, {}
  for row in rows:
    fields = {
      name: row[name]
      for name in (
        "request_id",
        "request",
        "phase",
        "write_attempts",
        "read_attempts",
        "reason",
        "next_retry_at",
        "proof",
      )
    }
    used += len(json.dumps(fields, default=str).encode())
    if used > MAX_ARCHIVE_DIRECTORY_BYTES:
      raise HistoryReadInvalid("ARCHIVE_DIRECTORY_CAPACITY")
    status = ArchiveStatus.model_validate(fields)
    value = status.request
    if (
      value.instrument != request.instrument
      or value.minute != row["minute"]
      or not start <= value.minute < end
      or any(
        getattr(value, key) != row[key]
        for key in ("generation", "continuity_generation", "sequence", "sealed")
      )
    ):
      raise HistoryReadInvalid("ARCHIVE_DIRECTORY_IDENTITY_MISMATCH")
    heads[value.minute] = status
  return heads


async def read_archive_history(reader, request, *, session_factory, development):
  if reader._slot.locked():
    raise HistoryReadBusy("local history query capacity exhausted")
  async with reader._slot:
    budget = {"deadline": time.monotonic() + 10, "bytes": 0}
    async with asyncio.timeout(3), session_factory() as db:
      if development:
        version = await resolve_published_bar_version(
          db,
          HistoryPartitionRequest(
            instrument=request.instrument,
            period=request.period,
            trading_date=request.trading_date,
          ),
        )
        native = None
      else:
        version = None
        native = await resolve_native_bar_version(db, request)
      heads = (
        {}
        if version or (native and native.full_session)
        else await _archive_heads(db, request)
      )
    if version:
      return await reader._read_thread(
        lambda value: reader._read(
          value, storage_version=version["storage_version"], budget=budget
        ),
        request,
      )
    if native and not heads:
      return await reader._read_thread(
        lambda value: reader._read(
          value, storage_version=native.storage_version, budget=budget
        ),
        request,
      )
    if not heads:
      raise HistoryReadInvalid("HISTORY_STORAGE_VERSION_UNAVAILABLE")
    selected = {
      minute: head for minute, head in heads.items() if head.phase == "VERIFIED"
    }

    def read(value):
      archives = reader._read(value, archive_versions=selected, budget=budget)
      if native is None:
        return archives
      original = reader._read(
        value,
        storage_version=native.storage_version,
        excluded_minutes=heads,
        budget=budget,
      )
      rows = sorted([*archives.records, *original.records], key=lambda row: row["time"])
      if any(left["time"] == right["time"] for left, right in zip(rows, rows[1:])):
        raise HistoryReadInvalid("ARCHIVE_CANONICAL_OVERLAP")
      rows = rows[: value.page_size]
      return HistoryPage(
        records=rows, next_after=rows[-1]["time"] if rows else None, exhausted=not rows
      )

    return await reader._read_thread(read, request)
