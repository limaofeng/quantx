"""Repository for durable strategy decision audit records."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sqlalchemy import desc, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.strategy_decision_trace_record import (
  StrategyDecisionTraceRecord,
)

_IMMUTABLE_TRACE_FIELDS = (
  "id",
  "trace_id",
  "strategy_run_id",
  "strategy_id",
  "instrument_code",
  "decided_at",
  "input_summary",
  "output_summary",
  "trade_intents",
  "state_patch",
  "decision_trace",
)
# A day batch has at most 600 evaluations per instrument in the fixed pressure
# fixture.  PostgreSQL receives a single JSONB bind for each bounded 1,024-row
# chunk, so the batch no longer expands to eleven bind parameters per trace or
# requires a remote INSERT round trip every 256 records.
_TRACE_APPEND_POSTGRESQL_RECORDSET_CHUNK_SIZE = 1_024
# Production relational storage is PostgreSQL-only.  Keep the pre-existing
# Core multi-values path exclusively for SQLite-backed test sessions.
_TRACE_APPEND_TEST_VALUES_CHUNK_SIZE = 256
_TRACE_APPEND_POSTGRESQL_RECORDSET = text(
  """
  INSERT INTO strategy_decision_traces (
    id,
    trace_id,
    strategy_run_id,
    strategy_id,
    instrument_code,
    decided_at,
    input_summary,
    output_summary,
    trade_intents,
    state_patch,
    decision_trace,
    created_at,
    updated_at
  )
  SELECT
    trace.id,
    trace.trace_id,
    trace.strategy_run_id,
    trace.strategy_id,
    trace.instrument_code,
    trace.decided_at,
    trace.input_summary::json,
    trace.output_summary::json,
    trace.trade_intents::json,
    trace.state_patch::json,
    trace.decision_trace::json,
    now(),
    now()
  FROM jsonb_to_recordset(CAST(:trace_payload AS jsonb)) AS trace(
    id text,
    trace_id text,
    strategy_run_id text,
    strategy_id text,
    instrument_code text,
    decided_at timestamp without time zone,
    input_summary jsonb,
    output_summary jsonb,
    trade_intents jsonb,
    state_patch jsonb,
    decision_trace jsonb
  )
  ON CONFLICT (id) DO NOTHING
  RETURNING id
  """
)


def _normalize_decided_at(value: Any) -> Any:
  """Keep PostgreSQL ``TIMESTAMP WITHOUT TIME ZONE`` values UTC-naive.

  Decision traces are ordered by the instant at which the StrategyOutput was
  produced.  PostgreSQL's mapped column deliberately has no timezone, so an
  aware input must be converted to its UTC wall-clock representation before it
  reaches asyncpg.  Existing naive values are already the repository contract
  and remain unchanged.
  """

  if isinstance(value, datetime) and value.tzinfo is not None:
    return value.astimezone(timezone.utc).replace(tzinfo=None)
  return value


def _normalized_trace_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
  """Normalize the immutable database boundary without changing record IDs."""

  normalized = dict(payload)
  if "decided_at" in normalized:
    normalized["decided_at"] = _normalize_decided_at(normalized["decided_at"])
  return normalized


def _trace_recordset_json(payloads: Sequence[Mapping[str, Any]]) -> str:
  """Serialize one PostgreSQL recordset bind without changing trace facts."""

  rows: list[Dict[str, Any]] = []
  for payload in payloads:
    row = dict(payload)
    decided_at = row.get("decided_at")
    if isinstance(decided_at, datetime):
      row["decided_at"] = decided_at.isoformat()
    rows.append(row)
  return json.dumps(
    rows,
    ensure_ascii=True,
    separators=(",", ":"),
    allow_nan=False,
  )


def _uses_postgresql_jsonb_recordset(db_session: Any) -> bool:
  """Select the production insert path; SQLite exists only for test isolation."""

  bind = getattr(db_session, "bind", None)
  dialect = getattr(bind, "dialect", None)
  dialect_name = getattr(dialect, "name", None)
  # Lightweight repository fakes intentionally omit a bind and exercise the
  # PostgreSQL statement shape.  Real production sessions are asyncpg-backed;
  # an explicit SQLite bind retains the small test-only Core fallback.
  return dialect_name in {None, "postgresql"}


def _canonical_trace_value(value: Any) -> Any:
  if isinstance(value, datetime):
    value = _normalize_decided_at(value)
    return value.isoformat(timespec="microseconds")
  if isinstance(value, Mapping):
    return {
      str(key): _canonical_trace_value(item)
      for key, item in value.items()
    }
  if isinstance(value, (list, tuple)):
    return [_canonical_trace_value(item) for item in value]
  return value


def _immutable_trace_fingerprint(payload: Mapping[str, Any]) -> str:
  """Hash exactly the immutable content protected by a stable trace UUID."""

  canonical = {
    field: _canonical_trace_value(payload.get(field))
    for field in _IMMUTABLE_TRACE_FIELDS
  }
  encoded = json.dumps(
    canonical,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
  )
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class StrategyDecisionTraceRepository(BaseRepository[StrategyDecisionTraceRecord]):
  """Strategy decision trace repository."""

  model_class = StrategyDecisionTraceRecord

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def create_trace(self, trace_data: Dict[str, Any]) -> StrategyDecisionTraceRecord:
    payload = _normalized_trace_payload(trace_data or {})
    payload.setdefault("id", str(uuid.uuid4()))
    record = StrategyDecisionTraceRecord(**payload)
    self.db.add(record)
    await self.db.commit()
    await self.db.refresh(record)
    return record

  async def append_traces(
    self,
    trace_data: Iterable[Mapping[str, Any]],
    *,
    commit: bool = True,
    flush: bool = True,
  ) -> list[StrategyDecisionTraceRecord]:
    """Append a bounded batch without changing any trace identity.

    ``RuntimeStateManager`` supplies records with stable UUIDs and may place
    this append in the same transaction as its RuntimeState CAS.  Keeping the
    append caller-owned avoids a second remote commit per evaluated Tick while
    preserving append-only trace rows and all-or-nothing failure semantics.
    """

    payloads = [_normalized_trace_payload(payload) for payload in trace_data]
    if not payloads:
      return []
    trace_ids = [str(payload.get("id") or "") for payload in payloads]
    if any(not trace_id for trace_id in trace_ids):
      raise ValueError("decision trace append requires stable record ids")
    if len(trace_ids) != len(set(trace_ids)):
      raise ValueError("decision trace append batch contains duplicate record ids")
    payload_by_id = {
      trace_id: {**payload, "id": trace_id}
      for trace_id, payload in zip(trace_ids, payloads)
    }
    payloads = list(payload_by_id.values())

    # A commit response can be lost after PostgreSQL has durably committed the
    # RuntimeState CAS and this append.  The manager then retries the exact
    # stable UUID batch after reconciling the CAS winner.  PostgreSQL is the
    # authoritative relational store, so make that replay an idempotent no-op
    # rather than turning it into a permanent primary-key failure.
    inserted_ids: set[str] = set()
    use_postgresql_recordset = _uses_postgresql_jsonb_recordset(self.db)
    chunk_size = (
      _TRACE_APPEND_POSTGRESQL_RECORDSET_CHUNK_SIZE
      if use_postgresql_recordset
      else _TRACE_APPEND_TEST_VALUES_CHUNK_SIZE
    )
    for start in range(0, len(payloads), chunk_size):
      chunk = payloads[start : start + chunk_size]
      if use_postgresql_recordset:
        # One JSONB bind preserves every trace row and lets PostgreSQL expand
        # the recordset server-side.  This is materially cheaper than a
        # multi-values statement with thousands of JSON bind parameters over a
        # remote asyncpg connection, while preserving the surrounding CAS
        # transaction and stable-ID idempotency proof.
        result = await self.db.execute(
          _TRACE_APPEND_POSTGRESQL_RECORDSET,
          {"trace_payload": _trace_recordset_json(chunk)},
        )
      else:
        # SQLite is not a runtime relational backend.  Keep this limited
        # fallback for isolated repository tests that construct SQLite sessions.
        statement = (
          insert(StrategyDecisionTraceRecord)
          .values(chunk)
          .on_conflict_do_nothing(
            index_elements=[StrategyDecisionTraceRecord.id]
          )
          .returning(StrategyDecisionTraceRecord.id)
        )
        result = await self.db.execute(statement)
      inserted_ids.update(str(value) for value in result.scalars().all())
    replayed_ids = set(trace_ids) - inserted_ids
    if replayed_ids:
      existing_result = await self.db.execute(
        select(StrategyDecisionTraceRecord).where(
          StrategyDecisionTraceRecord.id.in_(sorted(replayed_ids))
        )
      )
      existing_by_id = {
        str(record.id): record
        for record in existing_result.scalars().all()
      }
      missing_ids = replayed_ids - set(existing_by_id)
      conflicting_ids = [
        trace_id
        for trace_id in replayed_ids
        if trace_id in existing_by_id
        and _immutable_trace_fingerprint(
          payload_by_id[trace_id]
        )
        != _immutable_trace_fingerprint(
          {
            field: getattr(existing_by_id[trace_id], field)
            for field in _IMMUTABLE_TRACE_FIELDS
          }
        )
      ]
      if missing_ids or conflicting_ids:
        raise ValueError(
          "decision trace idempotency conflict: "
          f"missing={sorted(missing_ids)} conflicting={sorted(conflicting_ids)}"
        )
    if commit:
      await self.db.commit()
    elif flush:
      await self.db.flush()
    return [StrategyDecisionTraceRecord(**payload) for payload in payloads]

  async def find_by_strategy_run(
    self,
    strategy_run_id: str,
    *,
    limit: int = 50,
    cursor: Optional[str] = None,
  ) -> List[StrategyDecisionTraceRecord]:
    stmt = (
      select(StrategyDecisionTraceRecord)
      .filter(StrategyDecisionTraceRecord.strategy_run_id == strategy_run_id)
      .order_by(desc(StrategyDecisionTraceRecord.decided_at), desc(StrategyDecisionTraceRecord.created_at))
      .limit(max(1, min(int(limit or 50), 200)))
    )
    if cursor:
      stmt = stmt.filter(StrategyDecisionTraceRecord.id < cursor)
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_by_trace_id(
    self,
    strategy_run_id: str,
    trace_id: str,
  ) -> List[StrategyDecisionTraceRecord]:
    stmt = (
      select(StrategyDecisionTraceRecord)
      .filter(StrategyDecisionTraceRecord.strategy_run_id == strategy_run_id)
      .filter(StrategyDecisionTraceRecord.trace_id == trace_id)
      .order_by(desc(StrategyDecisionTraceRecord.decided_at), desc(StrategyDecisionTraceRecord.created_at))
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())
